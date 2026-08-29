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

import logging
import os
import threading
from typing import Callable, Optional

from apscheduler.schedulers.blocking import BlockingScheduler

import base_script
import config
import dispatcher
import live_progress
import matcher
import persistence
import restock_notifier
import run_logging
import run_results
import sitemap_poller
from run_logging import build_run_logger, close_run_logger

# Frecuencias configurables por variable de entorno -- valores por defecto
# dentro del rango que fija E.1/modelo-datos-app-tcg.md ("cada 2-4h" /
# "cada 1-2h"), sin tener que tocar código para ajustarlos.
HOT_REFRESH_INTERVAL_HOURS = float(os.environ.get("HOT_REFRESH_INTERVAL_HOURS", 3))
SITEMAP_POLL_INTERVAL_HOURS = float(os.environ.get("SITEMAP_POLL_INTERVAL_HOURS", 1.5))
DAILY_SWEEP_HOUR = int(os.environ.get("DAILY_SWEEP_HOUR", 4))  # 04:00 -- fuera de horas punta


def _make_tracked_runner(job_type: str, work: Callable[[logging.Logger, Callable[[str], None]], None],
                          *, store_label: Optional[str] = None, stores_total: Optional[int] = None,
                          ) -> tuple[int, Callable[[], None]]:
    """Crea la fila scrape_run (status='running') + su FileHandler de
    ejecución (docs/propuestas/propuesta-scraping-manual-panel.md punto 2),
    y devuelve (run_id, run_and_finish) SIN ejecutar `work` todavía --
    run_and_finish() la ejecuta y cierra la fila como 'completed'/'failed'
    pase lo que pase. Deja a quien llama decidir si eso corre en el hilo
    actual (_run_tracked, bloqueante -- los jobs de APScheduler no deben
    solaparse) o en un hilo de fondo (launch_tracked_run, usado por el
    servicio HTTP del punto 3 para devolver {run_id} sin esperar)."""
    conn = persistence.get_connection()
    try:
        run_id = persistence.create_scrape_run(conn, job_type, store_label=store_label, stores_total=stores_total)
    finally:
        conn.close()

    run_logger = build_run_logger(run_id)

    def run_and_finish() -> None:
        progress_conn = persistence.get_connection()

        def on_store_done(label: str) -> None:
            persistence.increment_scrape_run_progress(progress_conn, run_id)
            # Punto 4: refresca la estimación cacheada de esta tienda con el
            # último recuento real de páginas -- de CUALQUIER scrape (manual
            # o de barrido), no solo el disparo puntual. live_progress ya
            # tiene la última página vista por StoreLogger.log() de esta
            # tienda concreta (indexado por label, no por run_id -- ver
            # live_progress.py, evita que dos hilos de run_all_stores se
            # pisen entre sí).
            progress = live_progress.get_current_page(label)
            if progress is not None:
                page, total = progress
                persistence.update_store_last_known_page_count(progress_conn, label, total or page)
                live_progress.clear_current_page(label)

        status = "completed"
        try:
            work(run_logger, on_store_done)
        except Exception as e:
            run_logger.info(f"ERROR: {type(e).__name__}: {e}", extra={"store": "scheduler"})
            status = "failed"
        finally:
            progress_conn.close()
            finish_conn = persistence.get_connection()
            try:
                persistence.finish_scrape_run(finish_conn, run_id, status)
            finally:
                finish_conn.close()
            close_run_logger(run_logger)

    return run_id, run_and_finish


def _run_tracked(job_type: str, work: Callable[[logging.Logger, Callable[[str], None]], None],
                  *, store_label: Optional[str] = None, stores_total: Optional[int] = None) -> int:
    """Ejecuta `work` de forma SÍNCRONA (bloquea hasta terminar) con
    seguimiento de scrape_run -- usado por los jobs de APScheduler
    (job_daily_sweep/job_hot_refresh/job_sitemap_poll/job_single_store), que
    deben bloquear para que dos disparos del mismo job nunca se solapen."""
    run_id, run_and_finish = _make_tracked_runner(job_type, work, store_label=store_label, stores_total=stores_total)
    run_and_finish()
    return run_id


def launch_tracked_run(job_type: str, work: Callable[[logging.Logger, Callable[[str], None]], None],
                        *, store_label: Optional[str] = None, stores_total: Optional[int] = None) -> int:
    """Como _run_tracked, pero NO bloquea: lanza `work` en un hilo de fondo y
    devuelve run_id de inmediato. Es lo que usa el servicio HTTP del punto 3
    (jobs_api.py) para contestar `{run_id}` sin esperar a que el scrape
    termine -- un barrido completo puede tardar minutos, y un disparo de
    tienda suelta hasta STORE_TIMEOUT (~90s)."""
    run_id, run_and_finish = _make_tracked_runner(job_type, work, store_label=store_label, stores_total=stores_total)
    threading.Thread(target=run_and_finish, daemon=True).start()
    return run_id


def _work_daily_sweep(run_logger: logging.Logger, on_store_done: Callable[[str], None]) -> None:
    base_script.main(run_logger=run_logger, on_store_done=on_store_done)


def _work_hot_refresh(_run_logger: logging.Logger, _on_store_done: Callable[[str], None]) -> None:
    conn = persistence.get_connection()
    try:
        _counts, restock_event_ids = persistence.refresh_hot_products(conn, config.STORES)
        restock_notifier.notify_for_restock_events(conn, restock_event_ids)
    finally:
        conn.close()


def _work_sitemap_poll(_run_logger: logging.Logger, _on_store_done: Callable[[str], None]) -> None:
    conn = persistence.get_connection()
    try:
        sitemap_poller.poll_sitemaps(conn, config.STORES)
    finally:
        conn.close()


def job_daily_sweep() -> None:
    """Barrido completo -- reutiliza main() tal cual (mismo camino que
    `python base_script.py` a mano): scrape + CSV + persistencia +
    notificaciones + matching. stores_total se conoce desde el primer
    instante (E.1 de la propuesta de progreso) -- todas las tiendas
    configuradas, sin necesitar ningún descubrimiento previo. Bloqueante a
    propósito -- lo dispara APScheduler, dos barridos solapados no tiene
    sentido. El disparo manual desde el panel usa launch_daily_sweep()."""
    print("=== [scheduler] barrido diario: empezando ===")
    run_id = _run_tracked("daily_sweep", _work_daily_sweep, stores_total=len(config.STORES))
    print(f"=== [scheduler] barrido diario: terminado (run_id={run_id}) ===")


def job_hot_refresh() -> None:
    """E.2: refresco individual de productos calientes."""
    print("=== [scheduler] refresco de calientes: empezando ===")
    run_id = _run_tracked("hot_refresh", _work_hot_refresh)
    print(f"=== [scheduler] refresco de calientes: terminado (run_id={run_id}) ===")


def job_sitemap_poll() -> None:
    """E.1: polling de sitemap para altas tempranas."""
    print("=== [scheduler] polling de sitemap: empezando ===")
    run_id = _run_tracked("sitemap_poll", _work_sitemap_poll)
    print(f"=== [scheduler] polling de sitemap: terminado (run_id={run_id}) ===")


def launch_daily_sweep() -> int:
    return launch_tracked_run("daily_sweep", _work_daily_sweep, stores_total=len(config.STORES))


def launch_hot_refresh() -> int:
    return launch_tracked_run("hot_refresh", _work_hot_refresh)


def launch_sitemap_poll() -> int:
    return launch_tracked_run("sitemap_poll", _work_sitemap_poll)


def launch_single_store(label: str, *, persist: bool = False) -> int:
    """Disparo manual de UNA tienda desde el panel (docs/propuestas/
    propuesta-scraping-manual-panel.md punto 3, ampliado 2026-08-29 con la
    opción de persistir): lanza el scrape en background y devuelve run_id de
    inmediato -- una consulta puntual puede tardar hasta STORE_TIMEOUT
    (~90s), y el panel no debe bloquear la petición HTTP todo ese tiempo.
    Lanza ValueError si `label` no existe (el llamador HTTP lo convierte en
    404, ver jobs_api.py).

    persist=False (por defecto, más conservador): solo diagnóstico -- nada
    se escribe en store_product/price_history, igual que dispatcher.query_store()
    siempre ha hecho. persist=True reutiliza persistence.persist_scrape_results()
    (el mismo camino que el barrido diario) para que el resultado SÍ quede
    guardado y entre en la cola de matching, y dispara también las
    notificaciones de restock correspondientes.

    Los productos encontrados quedan en run_results.py (en memoria, run_id
    -> productos) tanto si se persiste como si no -- para que un disparo sin
    persistir sirva de algo: se puede ver qué se habría guardado."""
    store_config = dispatcher.find_store(label)
    if store_config is None:
        raise ValueError(f"tienda desconocida: {label}")

    def work(run_logger: logging.Logger, on_store_done: Callable[[str], None]) -> None:
        result = dispatcher.query_store(store_config, run_logger=run_logger)
        run_logger.info(f"resultado: {result.status} ({len(result.products)} producto(s))",
                         extra={"store": "scheduler"})

        run_id = run_logging.run_id_from_logger(run_logger)
        if run_id is not None:
            run_results.set_results(run_id, [p.to_dict() for p in result.products], persisted=False)

        if persist and result.products:
            restock_event_ids = persistence.persist_scrape_results(result.products, [store_config])
            conn = persistence.get_connection()
            try:
                restock_notifier.notify_for_restock_events(conn, restock_event_ids)
                matcher.run_matching(conn)
            finally:
                conn.close()
            if run_id is not None:
                run_results.set_results(run_id, [p.to_dict() for p in result.products], persisted=True)

        on_store_done(label)  # punto 4 -- refresca store.last_known_page_count con este scrape

    return launch_tracked_run("single_store", work, store_label=label)


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
