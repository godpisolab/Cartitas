"""Orquestador persistente (E.1 de cambios-necesarios-scraper.md, decidido
2026-08-26): un único proceso Python con APScheduler en vez de tres entradas
de cron del sistema operativo -- ver la discusión completa de esa decisión
en el documento de cambios (control programático sobre los jobs, una sola
conexión/configuración compartida, a cambio de un proceso que hay que
mantener vivo con supervisión externa: systemd, `restart: always` en
Docker, etc.).

Tres jobs, cada uno con su propia frecuencia:

- barrido_diario        -- 1x/día. run_all_stores() + persistencia +
                            notificaciones de restock + matching. Es
                            literalmente lo que hace `python base_script.py`.
- refresco_calientes     -- cada 2-4h. Solo los store_product cuyo producto
                            canónico esté marcado is_hot (E.2).
- polling_sitemap        -- cada 1-2h. Descubre altas tempranas vía
                            sitemap.xml (E.1, solo tiendas con
                            store.sitemap_url configurado a mano).

Uso:
    python scheduler.py

Se queda en primer plano (blocking scheduler) -- pensado para correr dentro
de un proceso supervisado (systemd/Docker), no interactivamente."""

from __future__ import annotations

import os

from apscheduler.schedulers.blocking import BlockingScheduler

import base_script
import config
import matcher
import persistence
import restock_notifier
import sitemap_poller

# Frecuencias configurables por variable de entorno -- valores por defecto
# dentro del rango que fija E.1/modelo-datos-app-tcg.md ("cada 2-4h" /
# "cada 1-2h"), sin tener que tocar código para ajustarlos.
HOT_REFRESH_INTERVAL_HOURS = float(os.environ.get("HOT_REFRESH_INTERVAL_HOURS", 3))
SITEMAP_POLL_INTERVAL_HOURS = float(os.environ.get("SITEMAP_POLL_INTERVAL_HOURS", 1.5))
DAILY_SWEEP_HOUR = int(os.environ.get("DAILY_SWEEP_HOUR", 4))  # 04:00 -- fuera de horas punta


def job_daily_sweep() -> None:
    """Barrido completo -- reutiliza main() tal cual (mismo camino que
    `python base_script.py` a mano): scrape + CSV + persistencia +
    notificaciones + matching."""
    print("=== [scheduler] barrido diario: empezando ===")
    base_script.main()
    print("=== [scheduler] barrido diario: terminado ===")


def job_hot_refresh() -> None:
    """E.2: refresco individual de productos calientes."""
    print("=== [scheduler] refresco de calientes: empezando ===")
    conn = persistence.get_connection()
    try:
        _counts, restock_event_ids = persistence.refresh_hot_products(conn, config.STORES)
        restock_notifier.notify_for_restock_events(conn, restock_event_ids)
    except Exception as e:
        print(f"[scheduler] ERROR en refresco de calientes: {type(e).__name__}: {e}")
    finally:
        conn.close()
    print("=== [scheduler] refresco de calientes: terminado ===")


def job_sitemap_poll() -> None:
    """E.1: polling de sitemap para altas tempranas."""
    print("=== [scheduler] polling de sitemap: empezando ===")
    conn = persistence.get_connection()
    try:
        sitemap_poller.poll_sitemaps(conn, config.STORES)
    except Exception as e:
        print(f"[scheduler] ERROR en polling de sitemap: {type(e).__name__}: {e}")
    finally:
        conn.close()
    print("=== [scheduler] polling de sitemap: terminado ===")


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler()
    scheduler.add_job(job_daily_sweep, "cron", hour=DAILY_SWEEP_HOUR, id="barrido_diario")
    scheduler.add_job(job_hot_refresh, "interval", hours=HOT_REFRESH_INTERVAL_HOURS, id="refresco_calientes")
    scheduler.add_job(job_sitemap_poll, "interval", hours=SITEMAP_POLL_INTERVAL_HOURS, id="polling_sitemap")
    return scheduler


if __name__ == "__main__":
    sched = build_scheduler()
    print(f"[scheduler] arrancado -- barrido diario a las {DAILY_SWEEP_HOUR}:00, "
          f"calientes cada {HOT_REFRESH_INTERVAL_HOURS}h, sitemap cada {SITEMAP_POLL_INTERVAL_HOURS}h")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        print("[scheduler] detenido")
