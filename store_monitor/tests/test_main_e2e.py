"""Smoke test E2E de main() -- sección 5 del plan de pruebas. El único test
end-to-end real de todo el proyecto: 2 tiendas mockeadas por HTTP (Shopify +
WooCommerce) + Postgres real (cartitas_test). A propósito hay uno solo --
la pirámide de este proyecto invierte el peso hacia integración/scrapers,
no hacia E2E."""

from __future__ import annotations

import csv
import os

import pytest

import base_script
from domain import Platform, StoreConfig


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    for mod_name in ("base_script", "scrapers.shopify", "scrapers.woocommerce"):
        import importlib
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "time"):
            monkeypatch.setattr(mod.time, "sleep", lambda s: None)


@pytest.fixture
def two_mock_stores(requests_mock):
    shopify_cfg = StoreConfig("TiendaShopify", "https://shopify.example", Platform.SHOPIFY,
                               shopify_collection="one-piece")
    woo_cfg = StoreConfig("TiendaWoo", "https://woo.example", Platform.WOOCOMMERCE,
                           woocommerce_category_slug="one-piece",
                           woocommerce_fallback_paths=("categoria/one-piece",))

    requests_mock.get("https://shopify.example/robots.txt", status_code=404)
    requests_mock.get("https://woo.example/robots.txt", status_code=404)

    requests_mock.get(
        "https://shopify.example/collections/one-piece/products.json",
        [
            {"json": {"products": [{
                "id": 1, "title": "Booster Box OP16", "handle": "booster-op16",
                "images": [{"src": "https://shopify.example/img.jpg"}],
                "variants": [{"title": "Default Title", "price": "99.95", "available": True, "sku": "S1"}],
            }]}},
            {"json": {"products": []}},
        ],
    )
    requests_mock.get(
        "https://woo.example/wp-json/wc/store/v1/products",
        json=[{
            "id": 2, "name": "Starter Deck OP16", "permalink": "https://woo.example/producto/2",
            "sku": "W2", "categories": [{"slug": "one-piece"}],
            "prices": {"price": "1500", "currency_minor_unit": 2},
            "is_in_stock": True, "images": [{"src": "https://woo.example/img.jpg"}],
        }],
    )

    return [shopify_cfg, woo_cfg]


class TestMainE2E:
    def test_main_completo_csv_y_postgres(self, two_mock_stores, db_conn, monkeypatch, tmp_path):
        # write_products_csv(products, path=OUTPUT_CSV) vincula el default
        # en tiempo de DEFINICIÓN de la función -- parchear
        # base_script.OUTPUT_CSV después de importar no cambia ese default
        # ya vinculado. Se usa el cwd real en su lugar (main() escribe con
        # rutas relativas a la constante original, sin parchear).
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(base_script, "STORES", two_mock_stores)
        csv_path = tmp_path / base_script.OUTPUT_CSV

        # Categoría mínima para que el matcher no falle con "tabla category
        # vacía" (bloque C, ejecutado al final de main()) -- no es el foco
        # de este test, pero sin esto ensucia el resultado con un ERROR
        # capturado en vez de un pipeline limpio de principio a fin.
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO category (name, slug) VALUES ('Booster Box', 'booster-box')")
        db_conn.commit()

        base_script.main()

        # -- CSV --
        assert csv_path.exists()
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert {r["store"] for r in rows} == {"TiendaShopify", "TiendaWoo"}

        # -- Postgres --
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM store")
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT count(*) FROM store_product")
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT count(*) FROM price_history")
            assert cur.fetchone()[0] == 2

    def test_main_con_postgres_inaccesible_csv_se_genera_igual(self, two_mock_stores, monkeypatch, tmp_path):
        import persistence
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(base_script, "STORES", two_mock_stores)
        monkeypatch.setattr(persistence, "DATABASE_URL", "postgresql://x:x@localhost:1/no-existe")
        csv_path = tmp_path / base_script.OUTPUT_CSV

        base_script.main()  # no debe lanzar excepción pese a que Postgres es inalcanzable

        assert csv_path.exists()
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
