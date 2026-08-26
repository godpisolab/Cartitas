"""Estado dinámico por tienda entre ejecuciones -- columnas de la tabla
`store` en Postgres (crawl_delay_seconds, robots_checked_at, disallowed,
consecutive_failures, backoff_until), no un JSON local.

Migrado desde store_state.json (2026-08-26, tras revisión de la capa de
persistencia): unifica todo el estado runtime del scraper en una sola
fuente de verdad consultable por SQL, en vez de tenerlo repartido entre un
fichero local y la BBDD. Ver git history de este archivo para la versión
anterior basada en JSON, si hace falta compararlas.

Interfaz IDÉNTICA a la versión anterior (get_state/update_state por
dominio, misma StoreState) a propósito -- ningún punto de base_script.py
que ya use esta interfaz necesita cambiar. La conversión unix-timestamp
<-> TIMESTAMPTZ ocurre solo aquí dentro.

Degradación: si Postgres no está disponible, get_state() devuelve el
estado por defecto (permisivo -- sin backoff, sin caché de robots.txt) y
update_state() se limita a avisar por log en vez de fallar. Un Postgres
caído no debe impedir que el scraper funcione, igual que ya ocurre con el
resto de la persistencia (ver persist_scrape_results en persistence.py).

LIMITACIÓN conocida: en una base de datos recién creada, la fila de
`store` para una tienda no existe hasta que corre sync_stores() (al final
del primer ciclo completo) -- update_state() antes de eso no tiene fila
que actualizar (0 filas afectadas, sin error) y el resultado del chequeo
de robots.txt de ESE primer ciclo no queda cacheado. A partir del segundo
ciclo la fila ya existe y la caché funciona con normalidad."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class StoreState:
    robots_checked_at: Optional[float] = None   # unix timestamp de la última comprobación de robots.txt
    crawl_delay: Optional[float] = None          # Crawl-delay declarado por robots.txt, en segundos
    disallowed: bool = False                     # True si robots.txt prohíbe la URL que scrapeamos
    consecutive_failures: int = 0                # fallos seguidos entre ejecuciones (ver A.3)
    backoff_until: Optional[float] = None        # unix timestamp: no reintentar esta tienda antes de esto


# domain (StoreState.website_url) -> nombre de columna en `store`.
_COLUMNS = {
    "robots_checked_at": "robots_checked_at",
    "crawl_delay": "crawl_delay_seconds",
    "disallowed": "disallowed",
    "consecutive_failures": "consecutive_failures",
    "backoff_until": "backoff_until",
}
_TIMESTAMP_FIELDS = ("robots_checked_at", "backoff_until")


def _to_unix(value) -> Optional[float]:
    return value.timestamp() if value is not None else None


def _from_unix(value: Optional[float]):
    return datetime.fromtimestamp(value, tz=timezone.utc) if value is not None else None


def get_state(domain: str) -> StoreState:
    """Devuelve el estado guardado de `domain` (StoreConfig.domain, la
    misma clave estable que usa B.1 para el UPSERT de `store`), o los
    valores por defecto si la tienda no tiene fila todavía o Postgres no
    está disponible."""
    import persistence  # import diferido: evita el ciclo persistence -> base_script -> store_state

    try:
        conn = persistence.get_connection()
    except Exception as e:
        print(f"[store_state] AVISO: sin conexión a Postgres ({type(e).__name__}: {e}), "
              f"se asume estado por defecto para {domain}")
        return StoreState()

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT crawl_delay_seconds, backoff_until, robots_checked_at, disallowed, consecutive_failures "
                "FROM store WHERE website_url = %s",
                (domain,),
            )
            row = cur.fetchone()
    except Exception as e:
        print(f"[store_state] AVISO: fallo leyendo estado de {domain} ({type(e).__name__}: {e}), "
              f"se asume estado por defecto")
        return StoreState()
    finally:
        conn.close()

    if row is None:
        return StoreState()

    crawl_delay, backoff_until, robots_checked_at, disallowed, consecutive_failures = row
    return StoreState(
        robots_checked_at=_to_unix(robots_checked_at),
        crawl_delay=float(crawl_delay) if crawl_delay is not None else None,
        disallowed=bool(disallowed),
        consecutive_failures=consecutive_failures or 0,
        backoff_until=_to_unix(backoff_until),
    )


def update_state(domain: str, **fields) -> None:
    """Actualiza solo los campos pasados en la fila de `store` de `domain`.
    Si Postgres no está disponible, o la fila todavía no existe (ver
    LIMITACIÓN en el docstring del módulo), no falla -- avisa por log."""
    if not fields:
        return

    import persistence  # import diferido: evita el ciclo persistence -> base_script -> store_state

    try:
        conn = persistence.get_connection()
    except Exception as e:
        print(f"[store_state] AVISO: sin conexión a Postgres ({type(e).__name__}: {e}), "
              f"no se guarda el estado de {domain}")
        return

    try:
        set_clauses, values = [], []
        for key, value in fields.items():
            if key in _TIMESTAMP_FIELDS:
                value = _from_unix(value)
            elif key == "crawl_delay" and value is not None:
                value = round(value)  # crawl_delay_seconds es INTEGER -- ver comentario en el esquema
            set_clauses.append(f"{_COLUMNS[key]} = %s")
            values.append(value)
        values.append(domain)

        with conn.cursor() as cur:
            cur.execute(f"UPDATE store SET {', '.join(set_clauses)} WHERE website_url = %s", values)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[store_state] AVISO: fallo guardando estado de {domain} ({type(e).__name__}: {e})")
    finally:
        conn.close()
