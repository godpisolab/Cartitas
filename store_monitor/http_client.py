"""Infraestructura HTTP: sesión + reintentos con backoff, unificada para
las 6 plataformas soportadas (A.1/A.3/A.4 de cambios-necesarios-scraper.md).
Depende solo de domain.py y config.py -- nunca de store_state, del
dispatcher ni de los scrapers concretos (ver
docs/estandares_organizacion_codigo.md, sección 2).

request_with_retries() centraliza reintentos con backoff para TODAS las
peticiones HTTP de todas las plataformas -- antes cada función manejaba sus
propios try/except de forma distinta y sin reintentos, así que un timeout
puntual de red tiraba la tienda entera a la papelera de "tiendas_fallidas".

StoreLogger junta "imprimir por consola" y "marcar actividad para el
timeout por inactividad" en una sola llamada (`logger.log(...)`), evitando
el patrón repetido `print(...); mark_activity(...)` que había en cada
punto del código. Vive aquí (no en dispatcher.py) porque los scrapers
también la usan directamente y no deben depender del dispatcher.
"""

from __future__ import annotations

import email.utils
import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import cloudscraper
import requests

import live_progress
from config import BROWSER_LIKE_USER_AGENT, IDENTIFIABLE_USER_AGENT
from run_logging import console_logger
from shared.domain import StoreConfig

# Progreso de página en vivo (docs/propuestas/propuesta-scraping-manual-panel.md
# punto 4) -- todos los scrapers YA registran la página que están pidiendo en
# su mensaje normal ("...página 4...", "...página 3/12...", "...producto
# 5/23..."), así que se parsea de ahí en vez de tocar cada scraper para que
# reporte el progreso por un canal aparte.
_PAGE_PROGRESS_RE = re.compile(r"(?:página|producto)\s+(\d+)(?:/(\d+))?")

DEFAULT_DELAY = 1.0        # pausa entre páginas de una misma tienda
DEFAULT_TIMEOUT = 30       # timeout de socket por petición HTTP (verificado 2026-08-25:
                           # Arte9 tarda ~20-22s en responder de forma habitual -- no es un
                           # fallo puntual, es la latencia normal de esa tienda -- 20s se
                           # quedaba corto y provocaba fallos de red intermitentes reales)
MAX_RETRIES = 3            # reintentos por petición HTTP ante fallos transitorios
RETRY_BACKOFF_BASE = 1.6   # base del backoff exponencial

# A.3: tope máximo de espera si un servidor pide un Retry-After absurdo (mal
# configurado u hostil) -- no se bloquea el proceso completo esperando, se
# corta ahí y se trata como fallo normal de esa tienda en este ciclo.
MAX_RETRY_AFTER_WAIT = 300  # 5 minutos


class StoreLogger:
    """Une "imprimir por consola" y "marcar actividad" (para el timeout por
    inactividad del dispatcher) en una única llamada. Además recuerda el
    último mensaje de error/aviso (`last_error`) -- todos los scrapers ya
    prefijan sus mensajes de fallo con "ERROR"/"AVISO" por convención, así
    que esto no requiere tocarlos: permite que un resultado sin productos
    (StoreQueryResult.status == "empty") venga acompañado del motivo real
    (bloqueo anti-bot, página caída, categoría vacía...) en vez de un simple
    "0 productos" sin explicación, que es indistinguible entre un problema
    real y un catálogo legítimamente vacío."""

    def __init__(self, label: str, activity_tracker: dict[str, float],
                 *, run_logger: Optional[logging.Logger] = None):
        """activity_tracker es compartido con el dispatcher (wait_for_store lo
        lee para saber cuánto lleva la tienda sin actividad).

        run_logger (docs/propuestas/propuesta-scraping-manual-panel.md punto
        2): logger de la ejecución en curso (un FileHandler por run_id, ver
        run_logging.build_run_logger), para que este mensaje quede en
        logs/{run_id}.log en vez de solo en la terminal. Sin él, cae al
        logger de consola -- mismo comportamiento que el print() de antes,
        para no romper los usos sueltos (tests, query_store() sin ejecución
        asociada)."""
        self.label = label
        self._activity_tracker = activity_tracker
        self._logger = run_logger or console_logger
        self.last_error: Optional[str] = None
        self.touch()

    def touch(self) -> None:
        """Marca 'actividad ahora mismo' sin imprimir nada -- se llama antes
        de cada petición HTTP para que el timeout por inactividad no cuente
        el tiempo de red como tienda colgada."""
        self._activity_tracker[self.label] = time.time()

    def log(self, message: str) -> None:
        """Registra `message` con label=self.label en el logger de la
        ejecución (o consola si no hay ninguna asociada), marca actividad, y
        si el mensaje empieza por ERROR/AVISO lo recuerda en last_error."""
        self._logger.info(message, extra={"store": self.label})
        if message.startswith(("ERROR", "AVISO")):
            self.last_error = message
        match = _PAGE_PROGRESS_RE.search(message)
        if match:
            page = int(match.group(1))
            total = int(match.group(2)) if match.group(2) else None
            live_progress.set_current_page(self.label, page, total)
        self.touch()


def build_session(anti_bot: bool = False, *, config: Optional["StoreConfig"] = None) -> requests.Session:
    """anti_bot=True usa cloudscraper (necesario en tiendas PrestaShop/WooCommerce
    detrás de Cloudflare); Shopify normalmente no lo necesita para su JSON público.

    `config` decide el User-Agent (A.1): IDENTIFIABLE_USER_AGENT por defecto,
    o BROWSER_LIKE_USER_AGENT si config.ua_exception=True (excepción
    documentada de esa tienda concreta). Sin config (o con
    ua_exception=False) siempre se usa el identificable."""
    session = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    ) if anti_bot else requests.Session()

    user_agent = BROWSER_LIKE_USER_AGENT if (config and config.ua_exception) else IDENTIFIABLE_USER_AGENT
    session.headers.update({
        "User-Agent": user_agent,
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    })
    return session


def _backoff_delay(attempt: int) -> float:
    """Segundos a esperar antes del siguiente reintento: backoff exponencial
    (RETRY_BACKOFF_BASE ** attempt) + jitter aleatorio para no sincronizar
    reintentos si varias tiendas fallan a la vez."""
    return (RETRY_BACKOFF_BASE ** attempt) + random.uniform(0, 0.5)


# Algunas tiendas (verificado en AvalonBurgos, 2026-08-25) usan un reto
# anti-bot muy simple: una página intermedia (normalmente HTTP 202) con un
# <meta refresh> que fija una cookie vía JavaScript antes de recargar. No
# hace falta un navegador real para pasarlo -- se probó con curl_cffi
# (impersonando TLS de Chrome) y seguía bloqueado, pero la cookie en sí es
# ESTÁTICA (mismo valor en peticiones repetidas, sin nonce ni timestamp) --
# basta con leerla del HTML y fijarla a mano en la sesión antes de
# reintentar la MISMA petición, con requests normal y corriente.
_JS_COOKIE_CHALLENGE_RE = re.compile(
    r"document\.cookie\s*=\s*'([^=']+)=([^;']+);.*?domain=([^'\"]+)['\"]", re.DOTALL
)


def _solve_js_cookie_challenge(session: requests.Session, response: requests.Response) -> bool:
    """Si `response` es la página del reto anti-bot descrito arriba, fija la
    cookie en `session` y devuelve True (para que el llamador reintente la
    misma petición). Devuelve False si no es esa página -- no toca nada."""
    match = _JS_COOKIE_CHALLENGE_RE.search(response.text)
    if not match:
        return False
    name, value, domain = match.groups()
    session.cookies.set(name, value, domain=domain)
    return True


def _get_with_ssl_fallback(session: requests.Session, url: str, params: Optional[dict],
                            timeout: int, headers: Optional[dict] = None) -> requests.Response:
    """La huella TLS que usa cloudscraper (para parecer un Chrome real) a
    veces choca con la configuración TLS de un servidor concreto -- no es lo
    mismo que un bloqueo anti-bot deliberado. Verificado en Arte9
    (2026-08-25): cloudscraper falla ahí con SSLV3_ALERT_HANDSHAKE_FAILURE en
    el 100% de los intentos (reintentar con la MISMA sesión nunca lo
    arregla), mientras que un requests.Session normal conecta sin problema.
    Si pasa esto, se prueba una vez con una sesión de requests normal
    (heredando cookies/headers) antes de dar la petición por fallida."""
    try:
        return session.get(url, params=params, timeout=timeout, headers=headers)
    except requests.exceptions.SSLError:
        plain_session = requests.Session()
        plain_session.headers.update(dict(session.headers))
        plain_session.cookies.update(session.cookies)
        return plain_session.get(url, params=params, timeout=timeout, headers=headers)


def _parse_retry_after(header_value: Optional[str]) -> Optional[float]:
    """Parsea la cabecera Retry-After de un 429/503 (RFC 7231): puede venir
    como segundos ('120') o como fecha HTTP ('Wed, 21 Oct 2026 07:28:00
    GMT'). Devuelve segundos a esperar desde ahora, o None si la cabecera no
    viene o no se puede interpretar (en ese caso el llamador cae al backoff
    exponencial de siempre)."""
    if not header_value:
        return None
    header_value = header_value.strip()
    if header_value.isdigit():
        return float(header_value)
    try:
        parsed = email.utils.parsedate_to_datetime(header_value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max((parsed - datetime.now(timezone.utc)).total_seconds(), 0.0)


def request_with_retries(
    session: requests.Session,
    url: str,
    *,
    params: Optional[dict] = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = MAX_RETRIES,
    heartbeat: Optional[Callable[[], None]] = None,
    headers: Optional[dict] = None,
) -> Optional[requests.Response]:
    """GET con reintentos y backoff exponencial + jitter.

    Reintenta en: errores de red/timeout, 429 (rate limit) y 5xx.
    NO reintenta en: 404 u otros 4xx de cliente (reintentar no cambiaría el resultado) --
    esto incluye el 304 Not Modified de las peticiones condicionales (A.4, ver
    conditional_headers()), que no es un error, es la respuesta esperada.
    Devuelve None solo si TODOS los intentos fallan por error de red (sin respuesta
    HTTP alguna) -- en cualquier otro caso devuelve la Response tal cual para que el
    llamador decida qué hacer con el status_code.
    """
    response: Optional[requests.Response] = None

    for attempt in range(1, max_retries + 1):
        if heartbeat:
            heartbeat()
        try:
            response = _get_with_ssl_fallback(session, url, params, timeout, headers)
        except requests.RequestException:
            if attempt < max_retries:
                time.sleep(_backoff_delay(attempt))
                continue
            return None

        if attempt < max_retries and _solve_js_cookie_challenge(session, response):
            continue  # cookie del reto ya fijada -- reintentar la misma petición

        if response.status_code == 429 or response.status_code >= 500:
            if attempt < max_retries:
                # A.3: un 429 con Retry-After trae una instrucción explícita del
                # servidor -- tiene prioridad sobre nuestra estimación genérica.
                retry_after = _parse_retry_after(response.headers.get("Retry-After")) \
                    if response.status_code == 429 else None
                if retry_after is not None:
                    if retry_after > MAX_RETRY_AFTER_WAIT:
                        # Pide esperar más de lo razonable (mal configurado o
                        # intencionadamente hostil) -- no se bloquea el proceso
                        # completo, se corta aquí y se trata como fallo normal
                        # de esta tienda en este ciclo.
                        return response
                    time.sleep(retry_after)
                else:
                    time.sleep(_backoff_delay(attempt))
                continue
            return response  # devolvemos la última respuesta (fallida) igualmente

        return response  # 2xx, 3xx, o 4xx no reintentable

    return response


# ===========================================================================
# Peticiones condicionales (A.4) -- ver domain.RefreshedVariant/RefreshOutcome
# ===========================================================================
#
# A diferencia del barrido completo por categoría (que siempre quiere el
# estado actual de TODO, sin condicionar -- ver A.4 del documento de
# cambios), el refresco de un producto caliente sí se beneficia de
# condicionar: si no ha cambiado desde la última vez, el servidor responde
# 304 sin cuerpo, mucho más barato que volver a parsear la página entera.

def conditional_headers(etag: Optional[str], last_modified: Optional[str]) -> dict:
    """Cabeceras If-None-Match / If-Modified-Since a partir del ETag/
    Last-Modified guardados en store_product de la vez anterior. Vacío si no
    hay nada guardado todavía (primera vez que se refresca este producto)."""
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    return headers
