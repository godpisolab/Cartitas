"""Punto de entrada del scraper: scrapea todas las STORES (config.py) vía el
dispatcher, escribe los CSV de salida, imprime el resumen por consola, y
persiste en PostgreSQL (bloque B de cambios-necesarios-scraper.md).

Es el único módulo que puede importar de todas las capas de abajo a la vez
(domain, config, dispatcher, persistence, matcher, restock_notifier) --
ver docs/estandares_organizacion_codigo.md, sección 2. El resto de la
lógica (modelo de dominio, configuración de tiendas, clasificación, HTTP,
scrapers, orquestación) vive en su propio módulo; este archivo solo los
junta para la ejecución batch completa (`python base_script.py`).

Uso: python base_script.py
"""

from __future__ import annotations

import csv
import logging
from typing import Callable, Optional

import matcher
import persistence
import restock_notifier
from config import FAILED_STORES_CSV, OUTPUT_CSV, STORES
from dispatcher import run_all_stores
from shared.domain import CSV_FIELDNAMES, Product


def write_products_csv(products: list[Product], path: str = OUTPUT_CSV) -> None:
    """Vuelca todos los productos a un CSV, una fila por producto/variante."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for p in products:
            writer.writerow(p.to_dict())


def write_failed_stores_csv(failed: list[tuple[str, str, str]], path: str = FAILED_STORES_CSV) -> None:
    """Vuelca la lista (label, platform, motivo) de tiendas sin productos a
    un CSV aparte, para revisar qué falló sin tener que leer el log completo."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["store", "platform", "motivo"])
        writer.writerows(failed)


def print_summary(products: list[Product]) -> None:
    """Imprime por consola: filas por tienda, totales por tipo de producto,
    totales por disponibilidad, y el precio mínimo de cada set (OP-XX) tienda
    por tienda -- el "chollómetro" que motiva monitorizar varias tiendas a
    la vez."""
    from collections import Counter

    print("\nFilas por tienda:")
    for tienda, n in Counter(p.store for p in products).most_common():
        print(f"  {tienda}: {n}")

    print("\nResumen global por tipo de producto:")
    for tipo, n in Counter(p.product_type for p in products).most_common():
        print(f"  {tipo}: {n}")

    print("\nResumen global de disponibilidad:")
    for estado, n in Counter(p.stock_status for p in products).most_common():
        print(f"  {estado}: {n}")

    print("\nComparativa de precio mínimo por set (OP-XX) y tienda:")
    sets_vistos = sorted({p.main_set for p in products if p.main_set})
    for set_code in sets_vistos:
        print(f"  {set_code}:")
        por_tienda: dict[str, float] = {}
        for p in products:
            if p.main_set != set_code or p.price is None:
                continue
            if p.store not in por_tienda or p.price < por_tienda[p.store]:
                por_tienda[p.store] = p.price
        for tienda, precio in sorted(por_tienda.items(), key=lambda x: x[1]):
            print(f"    {tienda}: {precio:.2f} €")


def main(run_logger: Optional[logging.Logger] = None,
         on_store_done: Optional[Callable[[str], None]] = None) -> None:
    """Punto de entrada del script: scrapea todas las STORES, escribe los
    CSV de salida, imprime el resumen, y persiste en PostgreSQL (bloque B de
    cambios-necesarios-scraper.md -- store_product/price_history/
    restock_event, ver persistence.py). A diferencia del antiguo hook
    opcional a SQLite, esto ya no es un añadido que se pueda omitir en
    silencio: solo se protege contra un fallo de CONEXIÓN/escritura puntual
    (Postgres caído), no contra que falte la dependencia -- el CSV ya se
    guardó antes de llegar aquí, así que un fallo de BBDD no lo pierde.

    run_logger/on_store_done (docs/propuestas/propuesta-scraping-manual-panel.md
    puntos 2 y 4): pasados tal cual a run_all_stores() cuando esto se invoca
    desde scheduler.job_daily_sweep() con un scrape_run asociado -- sin
    ellos, se comporta igual que siempre (consola, sin seguimiento de
    progreso)."""
    all_products, failed_stores = run_all_stores(STORES, run_logger=run_logger, on_store_done=on_store_done)

    print(f"\nTotal productos combinados de todas las tiendas: {len(all_products)}")

    if failed_stores:
        write_failed_stores_csv(failed_stores)
        print(f"\n{len(failed_stores)} tienda(s) con problemas -> guardado en {FAILED_STORES_CSV}")
        for label, platform, motivo in failed_stores:
            print(f"  {label} ({platform}): {motivo}")

    if all_products:
        write_products_csv(all_products)
        print(f"Guardado en {OUTPUT_CSV}")
        print_summary(all_products)

    # Persistencia SIEMPRE se intenta, incluso con all_products vacío (todas
    # las tiendas fallaron este ciclo): persist_scrape_results() sincroniza
    # STORES -> tabla `store` (B.1) como primer paso, antes de tocar ningún
    # producto -- si esto se dejara dentro del `if all_products:`, una
    # ejecución en la que fallan todas las tiendas dejaría sin sincronizar
    # una tienda recién añadida a STORES ese ciclo (aunque el fallo no
    # tuviera nada que ver con ella).
    try:
        restock_event_ids = persistence.persist_scrape_results(all_products, STORES)
    except Exception as e:
        print(f"ERROR: no se pudo persistir en Postgres ({type(e).__name__}: {e}) "
              f"-- el CSV ya se guardó igualmente")
    else:
        # E.3: disparar notificaciones de los restocks detectados en ESTE
        # ciclo -- antes de matchear, para no depender de que el matcher
        # (que puede tardar/fallar) haya corrido ya.
        try:
            conn = persistence.get_connection()
            try:
                restock_notifier.notify_for_restock_events(conn, restock_event_ids)
            finally:
                conn.close()
        except Exception as e:
            print(f"ERROR: no se pudieron enviar notificaciones de restock ({type(e).__name__}: {e})")

        # Bloque C: solo tiene sentido re-matchear si la persistencia de
        # este ciclo salió bien -- si falló, los store_product de hoy ni
        # siquiera están actualizados en la BBDD todavía.
        try:
            conn = persistence.get_connection()
            try:
                counts = matcher.run_matching(conn)
            finally:
                conn.close()
            print(f"[matching] {counts}")
        except Exception as e:
            print(f"ERROR: no se pudo ejecutar el matcher ({type(e).__name__}: {e})")


if __name__ == "__main__":
    main()
