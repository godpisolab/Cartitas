"""Dispatcher/orquestación: junta las piezas de las capas de abajo (config,
http_client, store_state, scrapers) para ejecutar el scraping real de una o
todas las tiendas -- circuit breaker por tienda, robots.txt (A.2), backoff
persistido entre ejecuciones (A.3) y el timeout por inactividad. Los
scrapers no dependen de este módulo (ver docs/estandares_organizacion_codigo.md,
sección 2); es este módulo el que depende de ellos para saber qué clase
instanciar por plataforma.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.robotparser import RobotFileParser

import pybreaker
import requests

import store_state
from config import STORES
from shared.domain import Platform, Product, StoreConfig
from http_client import DEFAULT_DELAY, DEFAULT_TIMEOUT, StoreLogger, build_session
from run_logging import console_logger
from scrapers import SCRAPER_CLASSES

# Tiempo máximo (segundos) SIN NINGUNA actividad (petición enviada o recibida)
# antes de dar una tienda por caída. No es un límite de duración total: mientras
# la tienda siga respondiendo (aunque tenga muchas páginas y tarde), no se corta.
STORE_TIMEOUT = 90
STORE_POLL_INTERVAL = 5  # cada cuánto se comprueba si ha habido actividad reciente

# Circuit breaker por tienda (usado por query_store, la consulta puntual "a
# dedo" pensada para un front -- ver StoreQueryResult). Tras BREAKER_FAIL_MAX
# fallos SEGUIDOS de la misma tienda, se deja de intentar scrapearla durante
# BREAKER_RESET_TIMEOUT segundos y se devuelve el fallo al instante en vez de
# esperar el timeout completo (hasta STORE_TIMEOUT) en cada consulta -- útil
# cuando una tienda concreta está caída/bloqueada (ej. WAF) y el usuario del
# front la sigue seleccionando mientras tanto. NO afecta a run_all_stores
# (el batch): cada tienda se scrapea una sola vez por ejecución ahí, así que
# el circuito no aporta nada y solo sumaría complejidad a un camino ya
# probado.
BREAKER_FAIL_MAX = 3
BREAKER_RESET_TIMEOUT = 300

# A.2: cada cuánto se refresca la caché de robots.txt por tienda -- cambia
# poco, comprobarlo una vez por semana es razonable (no en cada scrape).
ROBOTS_CACHE_TTL_SECONDS = 7 * 24 * 3600

# A.3: al tercer fallo seguido ENTRE EJECUCIONES (ver store_state.py), se
# fija un backoff que también respeta el siguiente ciclo de scraping, no
# solo los reintentos de la ejecución actual.
STORE_BACKOFF_FAILURE_THRESHOLD = 3
STORE_BACKOFF_DEFAULT_SECONDS = 1800  # 30 min de cooldown si el servidor no dio su propio Retry-After


# ===========================================================================
# robots.txt / Crawl-delay (A.2) -- se comprueba una vez por tienda y se
# cachea en store_state.py (no en cada scrape: robots.txt cambia poco).
# ===========================================================================

# Prefijo estable del mensaje de exclusión por robots.txt (ver scrape_store) --
# permite distinguir "excluida por política" de un fallo real de la tienda al
# clasificar resultados (_attempt_scrape, _record_backoff_outcome), sin
# necesitar un campo/estado nuevo en StoreQueryResult solo para esto.
ROBOTS_EXCLUSION_LOG_PREFIX = "AVISO: excluida por robots.txt"


def _is_policy_exclusion(error: Optional[str]) -> bool:
    """True si `error` es la exclusión por robots.txt de scrape_store, no un
    fallo real -- respetar robots.txt no debe abrir el circuito ni contar
    para el backoff entre ejecuciones (A.3)."""
    return bool(error) and error.startswith(ROBOTS_EXCLUSION_LOG_PREFIX)


@dataclass
class RobotsRules:
    disallowed: bool
    crawl_delay: Optional[float]


def _robots_check_target(config: StoreConfig) -> Optional[str]:
    """URL representativa que este scraper va a pedir de verdad, para
    comprobarla contra robots.txt -- una por plataforma, la ruta de listado
    principal (no cada URL de producto individual)."""
    if config.platform == Platform.SHOPIFY:
        return f"{config.domain}/collections/{config.shopify_collection}"
    if config.platform == Platform.PRESTASHOP:
        return config.prestashop_category_url
    if config.platform == Platform.WOOCOMMERCE:
        if config.woocommerce_fallback_paths:
            return f"{config.domain}/{config.woocommerce_fallback_paths[0].strip('/')}/"
        return None  # solo Store API, sin ruta HTML que comprobar
    if config.platform == Platform.ODOO:
        return config.odoo_category_url
    if config.platform == Platform.OPENCART:
        return config.opencart_category_url
    if config.platform == Platform.GENERIC_JSONLD:
        return config.jsonld_listing_urls[0] if config.jsonld_listing_urls else None
    return None


def _fetch_robots_rules(config: StoreConfig, target_url: str, logger: StoreLogger) -> RobotsRules:
    """Descarga y parsea robots.txt de config.domain con RobotFileParser
    (librería estándar). Si no se puede descargar (o no existe), se asume
    permisivo -- es la convención estándar cuando robots.txt no está
    disponible, no una forma de saltárselo."""
    robots_url = f"{config.domain}/robots.txt"
    session = build_session(anti_bot=False, config=config)

    try:
        resp = session.get(robots_url, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException:
        logger.log(f"AVISO: no se pudo descargar {robots_url}, se asume sin restricciones")
        return RobotsRules(disallowed=False, crawl_delay=None)

    parser = RobotFileParser()
    if resp.status_code == 200:
        parser.parse(resp.text.splitlines())
    else:
        parser.parse([])  # 404 u otro -- sin reglas, RobotFileParser permite todo por defecto

    disallowed = not parser.can_fetch("*", target_url)
    crawl_delay = parser.crawl_delay("*")
    return RobotsRules(disallowed=disallowed, crawl_delay=float(crawl_delay) if crawl_delay else None)


def get_robots_rules(config: StoreConfig, target_url: str, logger: StoreLogger) -> RobotsRules:
    """Rules cacheadas en store_state.py, refrescadas cada ROBOTS_CACHE_TTL_SECONDS."""
    state = store_state.get_state(config.domain)
    now = time.time()

    if state.robots_checked_at is not None and (now - state.robots_checked_at) < ROBOTS_CACHE_TTL_SECONDS:
        return RobotsRules(disallowed=state.disallowed, crawl_delay=state.crawl_delay)

    rules = _fetch_robots_rules(config, target_url, logger)
    store_state.update_state(
        config.domain,
        robots_checked_at=now,
        disallowed=rules.disallowed,
        crawl_delay=rules.crawl_delay,
    )
    return rules


# ===========================================================================
# Dispatcher / orquestación
# ===========================================================================

def scrape_store(config: StoreConfig, logger: StoreLogger) -> list[Product]:
    """Instancia el scraper de la plataforma de `config` y lo ejecuta. Punto
    de entrada síncrono y sin red de seguridad -- quien llame a esto (siempre
    dentro de un hilo de un ThreadPoolExecutor, ver run_all_stores/
    query_store) es responsable de aplicar timeout y capturar excepciones.

    Antes de scrapear, respeta robots.txt (A.2): si el Disallow cubre la URL
    que se va a pedir, la tienda se excluye este ciclo con motivo explícito
    en logs -- no se ignora robots.txt para seguir scrapeando de todos
    modos. El Crawl-delay declarado (si lo hay) sube el delay entre
    peticiones de esta tienda por encima de DEFAULT_DELAY, nunca por debajo."""
    logger.log(f"empezando ({config.platform.value})...")

    delay = DEFAULT_DELAY
    target_url = _robots_check_target(config)
    if target_url:
        rules = get_robots_rules(config, target_url, logger)
        if rules.disallowed:
            logger.log(f"{ROBOTS_EXCLUSION_LOG_PREFIX} (Disallow cubre {target_url})")
            return []
        if rules.crawl_delay:
            delay = max(DEFAULT_DELAY, rules.crawl_delay)

    scraper_cls = SCRAPER_CLASSES[config.platform]
    scraper = scraper_cls(config, logger, delay=delay)
    products = scraper.scrape()

    logger.log(f"terminado: {len(products)} productos")
    return products


def wait_for_store(future, label: str, activity_tracker: dict[str, float],
                    timeout: int, poll_interval: int) -> list[Product]:
    """Espera a que termine el future, pero solo lo da por caído si pasan
    `timeout` segundos SIN actividad (no por duración total del scraping)."""
    while True:
        try:
            return future.result(timeout=poll_interval)
        except FutureTimeoutError:
            inactive_for = time.time() - activity_tracker.get(label, 0)
            if inactive_for >= timeout:
                raise


@dataclass
class StoreQueryResult:
    """Resultado de intentar scrapear UNA tienda, siempre estructurado --
    nunca se lanza una excepción fuera de aquí. Pensado para ser el tipo de
    retorno que un futuro endpoint (consulta puntual de una tienda "a dedo"
    desde un front) pueda devolver tal cual, sin que el cliente tenga que
    conocer FutureTimeoutError ni ningún otro detalle interno: solo mira
    `status` y, según el caso, `products` o `error`.

    status:
        "ok"           -- terminó con al menos un producto.
        "empty"        -- terminó sin errores pero con 0 productos (posible
                          selector roto, categoría vacía, o scoping demasiado
                          estricto -- no se puede distinguir automáticamente,
                          hay que revisar a mano).
        "timeout"      -- sin actividad (peticiones enviadas/recibidas)
                          durante `STORE_TIMEOUT` segundos seguidos.
        "error"        -- excepción durante el scraping (red, parseo,
                          bug...); el mensaje queda en `error`, nunca se
                          propaga.
        "circuit_open" -- (solo vía query_store) esta tienda acumuló
                          BREAKER_FAIL_MAX fallos seguidos recientes y el
                          circuito está abierto: ni siquiera se ha intentado
                          la petición esta vez, para no insistir contra algo
                          ya confirmado caído/bloqueado."""
    label: str
    platform: str
    status: str
    products: list[Product] = field(default_factory=list)
    error: Optional[str] = None
    elapsed_seconds: float = 0.0


def _build_query_result(config: StoreConfig, future, logger: StoreLogger, activity_tracker: dict[str, float],
                         timeout: int, poll_interval: int, started: float) -> StoreQueryResult:
    """Espera el future de scrape_store (vía wait_for_store) y lo convierte
    en un StoreQueryResult -- lógica compartida por query_store y
    run_all_stores para que ambos caminos clasifiquen los fallos igual."""
    try:
        products = wait_for_store(future, config.label, activity_tracker, timeout, poll_interval)
    except FutureTimeoutError:
        return StoreQueryResult(config.label, config.platform.value, "timeout",
                                 error=f"sin actividad durante {timeout}s",
                                 elapsed_seconds=time.time() - started)
    except Exception as e:
        return StoreQueryResult(config.label, config.platform.value, "error",
                                 error=f"{type(e).__name__}: {e}",
                                 elapsed_seconds=time.time() - started)

    if products:
        status, error = "ok", None
    else:
        # "empty" a secas es ambiguo (¿categoría legítimamente vacía, o la
        # tienda bloqueó/falló a mitad de camino?) -- si el logger capturó
        # algún ERROR/AVISO durante el scraping, se adjunta como motivo.
        status, error = "empty", logger.last_error

    return StoreQueryResult(config.label, config.platform.value, status, products=products,
                             error=error, elapsed_seconds=time.time() - started)


def find_store(label: str) -> Optional[StoreConfig]:
    """Busca una tienda en STORES por su label exacto (case-sensitive).
    Devuelve None si no existe -- pensado para que un futuro endpoint pueda
    convertir eso directamente en un 404, sin excepciones que capturar."""
    return next((s for s in STORES if s.label == label), None)


class _StoreScrapeFailed(Exception):
    """Señal interna para que pybreaker cuente un StoreQueryResult "malo"
    (timeout/error, o empty con un motivo real) como fallo del circuito, sin
    que query_store() deje de devolver siempre un StoreQueryResult normal
    -- pybreaker solo sabe contar éxitos/fallos de una llamada a través de
    si lanza o no, así que esta excepción lleva el resultado ya calculado
    para que query_store lo recupere tal cual, en vez de tener que rehacer
    el scraping."""

    def __init__(self, result: StoreQueryResult):
        """Envuelve el StoreQueryResult ya calculado para recuperarlo intacto
        en el except de query_store."""
        self.result = result
        super().__init__(result.error or result.status)


# Un CircuitBreaker por tienda (así el bloqueo de una no afecta a las demás),
# creados perezosamente la primera vez que se consulta cada una.
_breakers: dict[str, pybreaker.CircuitBreaker] = {}


def _get_breaker(label: str) -> pybreaker.CircuitBreaker:
    """Devuelve el CircuitBreaker de esta tienda, creándolo la primera vez.
    Vive en memoria del proceso -- se reinicia si el proceso se reinicia."""
    if label not in _breakers:
        _breakers[label] = pybreaker.CircuitBreaker(
            fail_max=BREAKER_FAIL_MAX, reset_timeout=BREAKER_RESET_TIMEOUT,
        )
    return _breakers[label]


def _attempt_scrape(config: StoreConfig, timeout: int, poll_interval: int,
                     run_logger: Optional[logging.Logger] = None) -> StoreQueryResult:
    """Un intento real de scrapear `config`: crea su propio logger/activity_
    tracker/ThreadPoolExecutor de un solo hilo, y lanza _StoreScrapeFailed si
    el resultado cuenta como fallo (para que pybreaker.call() lo registre).
    Es el "trabajo real" que envuelve query_store; no se llama directamente
    desde fuera de este módulo."""
    activity_tracker: dict[str, float] = {config.label: time.time()}
    logger = StoreLogger(config.label, activity_tracker, run_logger=run_logger)
    started = time.time()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(scrape_store, config, logger)
        result = _build_query_result(config, future, logger, activity_tracker, timeout, poll_interval, started)

    # Cuenta como fallo del circuito: timeout, excepción, o "empty" con un
    # motivo real capturado (bloqueo/página caída). Un "empty" SIN motivo
    # (catálogo legítimamente vacío ahora mismo), o excluida por robots.txt
    # (decisión de política, no un problema de la tienda -- ver A.2), no
    # cuentan -- eso no debe abrir el circuito.
    is_real_failure = result.status in ("timeout", "error") or (
        result.status == "empty" and result.error and not _is_policy_exclusion(result.error)
    )
    if is_real_failure:
        raise _StoreScrapeFailed(result)
    return result


def query_store(config: StoreConfig, *, timeout: int = STORE_TIMEOUT,
                 poll_interval: int = STORE_POLL_INTERVAL, use_breaker: bool = True,
                 run_logger: Optional[logging.Logger] = None) -> StoreQueryResult:
    """Scrapea UNA tienda de forma aislada. Es la pieza que reutiliza la
    consulta puntual desde el panel ("elige una tienda y consúltala ahora",
    ver docs/propuestas/propuesta-scraping-manual-panel.md punto 3): un
    timeout, una excepción o un selector roto en esa tienda concreta no
    puede tirar abajo nada más que esta llamada.

    Con use_breaker=True (por defecto) protege además contra insistir en una
    tienda ya confirmada caída: tras BREAKER_FAIL_MAX fallos seguidos, deja
    de intentarlo durante BREAKER_RESET_TIMEOUT segundos (status
    "circuit_open"), y lo prueba de nuevo una vez pasado ese tiempo."""
    if not use_breaker:
        try:
            return _attempt_scrape(config, timeout, poll_interval, run_logger)
        except _StoreScrapeFailed as e:
            return e.result

    breaker = _get_breaker(config.label)
    try:
        return breaker.call(_attempt_scrape, config, timeout, poll_interval, run_logger)
    except pybreaker.CircuitBreakerError:
        return StoreQueryResult(
            config.label, config.platform.value, "circuit_open",
            error=f"circuito abierto tras {BREAKER_FAIL_MAX} fallos seguidos -- "
                  f"se deja de reintentar automáticamente durante ~{BREAKER_RESET_TIMEOUT}s",
        )
    except _StoreScrapeFailed as e:
        return e.result


def _record_backoff_outcome(config: StoreConfig, result: "StoreQueryResult") -> None:
    """A.3: cuenta fallos seguidos ENTRE EJECUCIONES de run_all_stores (no
    dentro de la misma -- eso ya lo cubre request_with_retries con su propio
    backoff) y fija backoff_until al llegar a STORE_BACKOFF_FAILURE_THRESHOLD,
    para que el PRÓXIMO run_all_stores también respete la pausa. Un éxito
    resetea el contador; una exclusión por robots.txt no cuenta como fallo
    (ver _is_policy_exclusion)."""
    is_failure = result.status in ("timeout", "error") or (
        result.status == "empty" and result.error and not _is_policy_exclusion(result.error)
    )

    if not is_failure:
        store_state.update_state(config.domain, consecutive_failures=0, backoff_until=None)
        return

    state = store_state.get_state(config.domain)
    failures = state.consecutive_failures + 1
    backoff_until = state.backoff_until
    if failures >= STORE_BACKOFF_FAILURE_THRESHOLD:
        backoff_until = time.time() + STORE_BACKOFF_DEFAULT_SECONDS
    store_state.update_state(config.domain, consecutive_failures=failures, backoff_until=backoff_until)


def run_all_stores(stores: list[StoreConfig],
                    run_logger: Optional[logging.Logger] = None,
                    on_store_done: Optional[Callable[[str], None]] = None,
                    ) -> tuple[list[Product], list[tuple[str, str, str]]]:
    """Scrapea TODAS las tiendas en paralelo (un hilo por tienda, todas
    lanzadas a la vez) y devuelve (productos_combinados, tiendas_fallidas).
    Usado por main() para la ejecución batch completa -- a diferencia de
    query_store, no lleva circuit breaker en memoria (cada tienda se intenta
    una sola vez por ejecución, así que no hay nada que cortocircuitar
    DENTRO de esta llamada) y comparte un único activity_tracker entre todas
    para que STORE_TIMEOUT se calcule por tienda individualmente.

    Sí respeta el backoff persistido ENTRE ejecuciones (A.3, store_state.py):
    una tienda con backoff_until aún en el futuro (3+ fallos seguidos en
    ejecuciones anteriores) se salta sin intentarla, y cada resultado de esta
    ejecución actualiza ese estado para la siguiente vez que corra esto.

    También respeta `store.active` (API v1, PATCH /stores/{id}): una tienda
    desactivada a mano desde el panel se salta igual que una en backoff --
    antes de esto la columna existía en el esquema pero no tenía ningún
    efecto real.

    run_logger (docs/propuestas/propuesta-scraping-manual-panel.md punto 2):
    logger de la ejecución en curso -- si no se pasa, cae al logger de
    consola (mismo comportamiento que los print() de antes). on_store_done
    (punto 4): callback opcional invocado justo después de clasificar el
    resultado de CADA tienda, para que quien orqueste la ejecución (ver
    scheduler.py) pueda mantener scrape_run.stores_done al día sin tener que
    adivinarlo del log."""
    logger = run_logger or console_logger
    activity_tracker: dict[str, float] = {}
    loggers: dict[str, StoreLogger] = {}
    started_at: dict[str, float] = {}
    failed_stores: list[tuple[str, str, str]] = []
    all_products: list[Product] = []

    now = time.time()
    runnable_stores = []
    for config in stores:
        state = store_state.get_state(config.domain)
        if not state.active:
            logger.info("AVISO: desactivada (store.active = false), se omite", extra={"store": config.label})
            failed_stores.append((config.label, config.platform.value, "desactivada (store.active = false)"))
            continue
        if state.backoff_until and state.backoff_until > now:
            wait_min = round((state.backoff_until - now) / 60, 1)
            logger.info(f"AVISO: en backoff tras fallos repetidos, quedan ~{wait_min} min",
                        extra={"store": config.label})
            failed_stores.append((config.label, config.platform.value,
                                   f"en backoff tras fallos repetidos (~{wait_min} min restantes, ver A.3)"))
            continue
        runnable_stores.append(config)

    with ThreadPoolExecutor(max_workers=max(len(runnable_stores), 1)) as executor:
        futures = {}
        for config in runnable_stores:
            started_at[config.label] = time.time()
            store_logger = StoreLogger(config.label, activity_tracker, run_logger=run_logger)
            loggers[config.label] = store_logger
            futures[executor.submit(scrape_store, config, store_logger)] = config

        for future, config in futures.items():
            result = _build_query_result(config, future, loggers[config.label], activity_tracker,
                                          STORE_TIMEOUT, STORE_POLL_INTERVAL,
                                          started_at[config.label])
            _record_backoff_outcome(config, result)

            if result.status == "ok":
                logger.info(f"OK: {len(result.products)} productos en {result.elapsed_seconds:.1f}s",
                            extra={"store": result.label})
                all_products.extend(result.products)
            elif result.status == "empty":
                motivo = result.error or "sin productos (0 filas)"
                logger.info(f"VACÍO en {result.elapsed_seconds:.1f}s: {motivo}", extra={"store": result.label})
                failed_stores.append((result.label, result.platform, motivo))
            elif result.status == "timeout":
                logger.info(f"TIMEOUT en {result.elapsed_seconds:.1f}s: {result.error}",
                            extra={"store": result.label})
                failed_stores.append((result.label, result.platform, result.error))
            else:
                logger.info(f"ERROR en {result.elapsed_seconds:.1f}s: {result.error}", extra={"store": result.label})
                failed_stores.append((result.label, result.platform, f"error: {result.error}"))

            if on_store_done:
                on_store_done(result.label)

    total_elapsed = time.time() - now
    logger.info(f"{len(runnable_stores)} tiendas intentadas en {total_elapsed:.1f}s de reloj "
                f"(en paralelo, un hilo por tienda -- STORE_TIMEOUT={STORE_TIMEOUT}s por tienda)",
                extra={"store": "run_all_stores"})

    return all_products, failed_stores
