"""Tests de persistence.refresh_hot_products() (E.2) -- sin cobertura
automatizada hasta ahora (verificado a mano contra tiendas reales durante
el desarrollo, ver README). El scraper real se sustituye por un
refresh_product() mockeado -- lo que se prueba aquí es la orquestación
(selección de calientes, agrupación por URL, aplicar el resultado), no la
extracción HTTP en sí (eso ya lo cubren los tests de cada scraper)."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import persistence
from shared.domain import Platform, Product, RefreshedVariant, RefreshOutcome, StoreConfig


def make_config(label="Tienda", domain="https://tienda.example"):
    return StoreConfig(label, domain, Platform.SHOPIFY, shopify_collection="x")


def seed_hot_product(conn, *, is_hot=True, hot_until=None, stock_status="AGOTADO") -> tuple[int, int, int]:
    """Crea game/category/product(is_hot)/store/store_product mínimos.
    Devuelve (store_id, product_id, store_product_id)."""
    cfg = make_config()
    store_ids = persistence.sync_stores(conn, [cfg])
    conn.commit()
    store_id = store_ids[cfg.domain]

    with conn.cursor() as cur:
        cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') RETURNING id")
        game_id = cur.fetchone()[0]
        cur.execute("INSERT INTO category (name, slug) VALUES ('Booster Box','booster-box') RETURNING id")
        category_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO product (game_id, category_id, name_canonical, is_hot, hot_until) "
            "VALUES (%s, %s, 'Producto caliente', %s, %s) RETURNING id",
            (game_id, category_id, is_hot, hot_until),
        )
        product_id = cur.fetchone()[0]
    conn.commit()

    product = Product(store="Tienda", platform="shopify", id_product=None, name="Producto caliente",
                       variant=None, product_type="", main_set=None, set_code=None, language=None,
                       price=10.0, stock_status=stock_status, url="https://tienda.example/p1",
                       sku=None, image_url=None)
    persistence._save_one_store(conn, store_id, [product], date.today())
    with conn.cursor() as cur:
        cur.execute("UPDATE store_product SET product_id = %s RETURNING id", (product_id,))
        store_product_id = cur.fetchone()[0]
    conn.commit()
    return store_id, product_id, store_product_id


class TestRefreshHotProducts:
    def test_sin_productos_calientes_no_hace_nada(self, db_conn):
        counts, restock_ids = persistence.refresh_hot_products(db_conn, [make_config()])
        assert counts == {"modified": 0, "not_modified": 0, "error": 0, "not_supported": 0}
        assert restock_ids == []

    def test_producto_no_is_hot_se_ignora(self, db_conn):
        seed_hot_product(db_conn, is_hot=False)
        counts, _ = persistence.refresh_hot_products(db_conn, [make_config()])
        assert counts["modified"] == 0

    def test_hot_until_caducado_se_ignora(self, db_conn):
        seed_hot_product(db_conn, is_hot=True, hot_until=date.today() - timedelta(days=1))
        counts, _ = persistence.refresh_hot_products(db_conn, [make_config()])
        assert counts["modified"] == 0

    def test_hot_until_null_es_valido_indefinidamente(self, db_conn, monkeypatch):
        seed_hot_product(db_conn, is_hot=True, hot_until=None)
        cfg = make_config()
        fake_outcome = RefreshOutcome(status="modified", variants=[
            RefreshedVariant(variant=None, price=20.0, stock_status="DISPONIBLE"),
        ])
        from scrapers.shopify import ShopifyScraper
        monkeypatch.setattr(ShopifyScraper, "refresh_product", classmethod(lambda cls, *a, **kw: fake_outcome))

        counts, _ = persistence.refresh_hot_products(db_conn, [cfg])

        assert counts["modified"] == 1

    def test_modified_actualiza_precio_stock_y_detecta_restock(self, db_conn, monkeypatch):
        store_id, product_id, sp_id = seed_hot_product(db_conn, stock_status="AGOTADO")
        cfg = make_config()
        fake_outcome = RefreshOutcome(status="modified", etag="abc123", variants=[
            RefreshedVariant(variant=None, price=15.5, stock_status="DISPONIBLE"),
        ])
        from scrapers.shopify import ShopifyScraper
        monkeypatch.setattr(ShopifyScraper, "refresh_product", classmethod(lambda cls, *a, **kw: fake_outcome))

        counts, restock_ids = persistence.refresh_hot_products(db_conn, [cfg])

        assert counts["modified"] == 1
        assert len(restock_ids) == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT current_price, stock_status, last_etag FROM store_product WHERE id = %s", (sp_id,))
            price, stock_status, etag = cur.fetchone()
        assert float(price) == 15.5
        assert stock_status == "disponible"
        assert etag == "abc123"

    def test_not_modified_solo_actualiza_last_checked_at(self, db_conn, monkeypatch):
        store_id, product_id, sp_id = seed_hot_product(db_conn, stock_status="AGOTADO")
        cfg = make_config()
        from scrapers.shopify import ShopifyScraper
        monkeypatch.setattr(ShopifyScraper, "refresh_product",
                             classmethod(lambda cls, *a, **kw: RefreshOutcome(status="not_modified")))

        counts, restock_ids = persistence.refresh_hot_products(db_conn, [cfg])

        assert counts["not_modified"] == 1
        assert restock_ids == []
        with db_conn.cursor() as cur:
            cur.execute("SELECT stock_status FROM store_product WHERE id = %s", (sp_id,))
            assert cur.fetchone()[0] == "agotado"  # sin cambios, solo se tocó last_checked_at

    def test_error_del_scraper_se_cuenta_y_no_revienta(self, db_conn, monkeypatch):
        seed_hot_product(db_conn)
        cfg = make_config()
        from scrapers.shopify import ShopifyScraper
        monkeypatch.setattr(ShopifyScraper, "refresh_product",
                             classmethod(lambda cls, *a, **kw: RefreshOutcome(status="error", error="boom")))

        counts, _ = persistence.refresh_hot_products(db_conn, [cfg])

        assert counts["error"] == 1

    def test_excepcion_del_scraper_se_captura_como_error(self, db_conn, monkeypatch):
        seed_hot_product(db_conn)
        cfg = make_config()
        from scrapers.shopify import ShopifyScraper

        def raise_error(cls, *a, **kw):
            raise RuntimeError("fallo de red inesperado")

        monkeypatch.setattr(ShopifyScraper, "refresh_product", classmethod(raise_error))

        counts, _ = persistence.refresh_hot_products(db_conn, [cfg])

        assert counts["error"] == 1

    def test_store_no_en_stores_config_cuenta_como_error(self, db_conn):
        seed_hot_product(db_conn)
        counts, _ = persistence.refresh_hot_products(db_conn, [])  # STORES vacío -- no encuentra la config
        assert counts["error"] == 1
