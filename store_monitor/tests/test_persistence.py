"""Tests de persistence.py -- sección 3 del plan de pruebas. Integración
contra Postgres REAL (cartitas_test, ver tests/conftest.py) -- el riesgo
real aquí está en el SQL, no en la llamada a psycopg2, así que no se
mockea la base de datos."""

from __future__ import annotations

from datetime import date

import pytest
from freezegun import freeze_time

import persistence
from domain import Platform, Product, StoreConfig


def make_config(label="Tienda", domain="https://tienda.example", platform=Platform.SHOPIFY):
    return StoreConfig(label, domain, platform, shopify_collection="x")


def make_product(store="Tienda", url="https://tienda.example/p1", name="Producto",
                  variant=None, price=10.0, stock_status="DISPONIBLE", sku=None):
    return Product(store=store, platform="shopify", id_product=None, name=name, variant=variant,
                    product_type="OTROS", main_set=None, set_code=None, language=None,
                    price=price, stock_status=stock_status, url=url, sku=sku, image_url=None)


def seed_product(conn, is_hot=False) -> int:
    """Inserta un game/category/product mínimos, devuelve el id de product."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece', 'one-piece') "
                    "ON CONFLICT (slug) DO NOTHING")
        cur.execute("INSERT INTO category (name, slug) VALUES ('Booster Box', 'booster-box') "
                    "ON CONFLICT (slug) DO NOTHING")
        cur.execute("SELECT id FROM game WHERE slug = 'one-piece'")
        game_id = cur.fetchone()[0]
        cur.execute("SELECT id FROM category WHERE slug = 'booster-box'")
        category_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO product (game_id, category_id, name_canonical, is_hot) VALUES (%s, %s, %s, %s) "
            "RETURNING id",
            (game_id, category_id, "Producto canónico de prueba", is_hot),
        )
        product_id = cur.fetchone()[0]
    conn.commit()
    return product_id


# ===========================================================================
# 3.1 -- sync_stores()
# ===========================================================================

class TestSyncStores:
    def test_tienda_nueva_se_inserta(self, db_conn):
        store_ids = persistence.sync_stores(db_conn, [make_config()])
        db_conn.commit()
        assert "https://tienda.example" in store_ids
        with db_conn.cursor() as cur:
            cur.execute("SELECT name, platform FROM store WHERE website_url = %s", ("https://tienda.example",))
            name, platform = cur.fetchone()
        assert name == "Tienda"
        assert platform == "shopify"

    def test_tienda_existente_actualiza_name_sin_tocar_campos_dinamicos(self, db_conn):
        persistence.sync_stores(db_conn, [make_config(label="Nombre Viejo")])
        db_conn.commit()
        with db_conn.cursor() as cur:
            cur.execute(
                "UPDATE store SET crawl_delay_seconds = 7, backoff_until = now(), last_scraped_at = now() "
                "WHERE website_url = %s",
                ("https://tienda.example",),
            )
        db_conn.commit()

        persistence.sync_stores(db_conn, [make_config(label="Nombre Nuevo")])
        db_conn.commit()

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT name, crawl_delay_seconds, backoff_until IS NOT NULL, last_scraped_at IS NOT NULL "
                "FROM store WHERE website_url = %s",
                ("https://tienda.example",),
            )
            name, crawl_delay, has_backoff, has_scraped = cur.fetchone()
        assert name == "Nombre Nuevo"
        assert crawl_delay == 7  # sobrevive al sync
        assert has_backoff is True
        assert has_scraped is True

    def test_website_url_duplicada_en_el_mismo_lote_falla(self, db_conn):
        # execute_values con ON CONFLICT no admite dos filas del MISMO lote
        # apuntando al mismo conflict target -- comportamiento real de
        # Postgres ("cannot affect row a second time"), no un bug nuestro.
        configs = [make_config(label="A"), make_config(label="B")]  # mismo domain/website_url
        with pytest.raises(Exception, match="ON CONFLICT DO UPDATE"):
            persistence.sync_stores(db_conn, configs)
        db_conn.rollback()


# ===========================================================================
# 3.2 -- _save_one_store() / detección de restock (B.2)
# ===========================================================================

class TestSaveOneStoreRestock:
    def test_producto_nuevo_disponible_no_genera_restock(self, db_conn):
        store_id = list(persistence.sync_stores(db_conn, [make_config()]).values())[0]
        db_conn.commit()

        restock_ids = persistence._save_one_store(
            db_conn, store_id, [make_product(stock_status="DISPONIBLE")], date.today(),
        )
        db_conn.commit()
        assert restock_ids == []
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM restock_event")
            assert cur.fetchone()[0] == 0

    def test_agotado_a_disponible_con_product_id_genera_restock(self, db_conn):
        store_id = list(persistence.sync_stores(db_conn, [make_config()]).values())[0]
        product_id = seed_product(db_conn)
        db_conn.commit()

        persistence._save_one_store(db_conn, store_id, [make_product(stock_status="AGOTADO")], date.today())
        db_conn.commit()
        with db_conn.cursor() as cur:
            cur.execute("UPDATE store_product SET product_id = %s", (product_id,))
        db_conn.commit()

        restock_ids = persistence._save_one_store(
            db_conn, store_id, [make_product(stock_status="DISPONIBLE")], date.today(),
        )
        db_conn.commit()

        assert len(restock_ids) == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM restock_event")
            assert cur.fetchone()[0] == 1

    def test_agotado_a_disponible_sin_product_id_no_genera_restock(self, db_conn):
        store_id = list(persistence.sync_stores(db_conn, [make_config()]).values())[0]
        db_conn.commit()

        persistence._save_one_store(db_conn, store_id, [make_product(stock_status="AGOTADO")], date.today())
        db_conn.commit()
        # product_id sigue NULL -- sin match confirmado.

        restock_ids = persistence._save_one_store(
            db_conn, store_id, [make_product(stock_status="DISPONIBLE")], date.today(),
        )
        db_conn.commit()
        assert restock_ids == []

    def test_desconocido_a_disponible_no_genera_restock(self, db_conn):
        store_id = list(persistence.sync_stores(db_conn, [make_config()]).values())[0]
        product_id = seed_product(db_conn)
        db_conn.commit()

        persistence._save_one_store(db_conn, store_id, [make_product(stock_status="DESCONOCIDO")], date.today())
        db_conn.commit()
        with db_conn.cursor() as cur:
            cur.execute("UPDATE store_product SET product_id = %s", (product_id,))
        db_conn.commit()

        restock_ids = persistence._save_one_store(
            db_conn, store_id, [make_product(stock_status="DISPONIBLE")], date.today(),
        )
        db_conn.commit()
        assert restock_ids == []

    def test_disponible_a_agotado_no_genera_restock(self, db_conn):
        store_id = list(persistence.sync_stores(db_conn, [make_config()]).values())[0]
        product_id = seed_product(db_conn)
        db_conn.commit()

        persistence._save_one_store(db_conn, store_id, [make_product(stock_status="DISPONIBLE")], date.today())
        db_conn.commit()
        with db_conn.cursor() as cur:
            cur.execute("UPDATE store_product SET product_id = %s", (product_id,))
        db_conn.commit()

        restock_ids = persistence._save_one_store(
            db_conn, store_id, [make_product(stock_status="AGOTADO")], date.today(),
        )
        db_conn.commit()
        assert restock_ids == []


class TestPriceHistory:
    def test_re_ejecutar_el_mismo_dia_actualiza_una_sola_fila(self, db_conn):
        store_id = list(persistence.sync_stores(db_conn, [make_config()]).values())[0]
        db_conn.commit()

        persistence._save_one_store(db_conn, store_id, [make_product(price=10.0)], date.today())
        db_conn.commit()
        persistence._save_one_store(db_conn, store_id, [make_product(price=12.0)], date.today())
        db_conn.commit()

        with db_conn.cursor() as cur:
            cur.execute("SELECT price FROM price_history")
            rows = cur.fetchall()
        assert len(rows) == 1
        assert float(rows[0][0]) == 12.0

    def test_dos_dias_distintos_generan_dos_filas(self, db_conn):
        store_id = list(persistence.sync_stores(db_conn, [make_config()]).values())[0]
        db_conn.commit()

        with freeze_time("2026-01-01"):
            persistence._save_one_store(db_conn, store_id, [make_product(price=10.0)], date.today())
            db_conn.commit()
        with freeze_time("2026-01-02"):
            persistence._save_one_store(db_conn, store_id, [make_product(price=11.0)], date.today())
            db_conn.commit()

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM price_history")
            assert cur.fetchone()[0] == 2


# ===========================================================================
# 3.3 -- Atomicidad (B.3)
# ===========================================================================

class TestAtomicidad:
    def test_lote_con_conflicto_interno_no_guarda_nada_de_esa_tienda(self, db_conn):
        store_id = list(persistence.sync_stores(db_conn, [make_config()]).values())[0]
        db_conn.commit()

        productos_validos = [make_product(url=f"https://tienda.example/p{i}") for i in range(10)]
        # Duplicado real dentro del MISMO lote (mismo store_url+variant que
        # el primero) -- provoca el error real de Postgres, no simulado.
        productos = productos_validos + [make_product(url="https://tienda.example/p0")]

        with pytest.raises(Exception):
            persistence._save_one_store(db_conn, store_id, productos, date.today())
        db_conn.rollback()

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM store_product")
            assert cur.fetchone()[0] == 0  # NINGUNO de los 10 válidos quedó guardado

    def test_una_tienda_falla_la_otra_no_se_ve_afectada(self, db_conn, monkeypatch):
        cfg_ok = make_config(label="TiendaOk", domain="https://ok.example")
        cfg_falla = make_config(label="TiendaFalla", domain="https://falla.example")
        store_ids = persistence.sync_stores(db_conn, [cfg_ok, cfg_falla])
        db_conn.commit()

        original = persistence._save_one_store

        def flaky(conn, store_id, products, scraped_date, **kwargs):
            if store_id == store_ids["https://falla.example"]:
                raise RuntimeError("fallo simulado en TiendaFalla")
            return original(conn, store_id, products, scraped_date, **kwargs)

        monkeypatch.setattr(persistence, "_save_one_store", flaky)

        all_products = [
            make_product(store="TiendaOk", url="https://ok.example/p1"),
            make_product(store="TiendaFalla", url="https://falla.example/p1"),
        ]
        persistence.persist_scrape_results(all_products, [cfg_ok, cfg_falla])

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM store_product WHERE store_id = %s", (store_ids["https://ok.example"],)
            )
            assert cur.fetchone()[0] == 1  # TiendaOk SÍ se guardó
            cur.execute(
                "SELECT count(*) FROM store_product WHERE store_id = %s", (store_ids["https://falla.example"],)
            )
            assert cur.fetchone()[0] == 0  # TiendaFalla no dejó nada a medias


# ===========================================================================
# 3.4 -- Truncado defensivo
# ===========================================================================

class TestTruncadoDefensivo:
    def test_raw_name_exactamente_500_no_se_trunca(self, db_conn, capsys):
        store_id = list(persistence.sync_stores(db_conn, [make_config()]).values())[0]
        db_conn.commit()
        nombre_500 = "X" * 500

        persistence._save_one_store(db_conn, store_id, [make_product(name=nombre_500)], date.today())
        db_conn.commit()

        with db_conn.cursor() as cur:
            cur.execute("SELECT raw_name FROM store_product")
            assert cur.fetchone()[0] == nombre_500
        assert "truncado" not in capsys.readouterr().out

    def test_raw_name_501_se_trunca_a_500_con_aviso(self, db_conn, capsys):
        store_id = list(persistence.sync_stores(db_conn, [make_config()]).values())[0]
        db_conn.commit()
        nombre_501 = "X" * 501

        persistence._save_one_store(db_conn, store_id, [make_product(name=nombre_501)], date.today())
        db_conn.commit()

        with db_conn.cursor() as cur:
            cur.execute("SELECT raw_name FROM store_product")
            saved = cur.fetchone()[0]
        assert len(saved) == 500
        assert "truncado" in capsys.readouterr().out

    def test_resto_del_lote_se_guarda_pese_al_truncado(self, db_conn):
        store_id = list(persistence.sync_stores(db_conn, [make_config()]).values())[0]
        db_conn.commit()
        productos = [
            make_product(url="https://tienda.example/largo", name="X" * 600),
            make_product(url="https://tienda.example/normal", name="Producto normal"),
        ]
        persistence._save_one_store(db_conn, store_id, productos, date.today())
        db_conn.commit()

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM store_product")
            assert cur.fetchone()[0] == 2


# ===========================================================================
# 3.5 -- Filtrado de productos inválidos
# ===========================================================================

class TestFiltradoInvalidos:
    def test_producto_sin_url_se_descarta_resto_se_guarda(self, db_conn, capsys):
        store_id = list(persistence.sync_stores(db_conn, [make_config()]).values())[0]
        db_conn.commit()
        productos = [make_product(url=None), make_product(url="https://tienda.example/ok")]

        persistence._save_one_store(db_conn, store_id, productos, date.today())
        db_conn.commit()

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM store_product")
            assert cur.fetchone()[0] == 1
        assert "descartados" in capsys.readouterr().out

    def test_producto_sin_name_se_descarta(self, db_conn):
        store_id = list(persistence.sync_stores(db_conn, [make_config()]).values())[0]
        db_conn.commit()
        productos = [make_product(name=None), make_product(url="https://tienda.example/ok2")]

        persistence._save_one_store(db_conn, store_id, productos, date.today())
        db_conn.commit()

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM store_product")
            assert cur.fetchone()[0] == 1

    def test_todos_invalidos_no_ejecuta_ningun_insert_ni_revienta(self, db_conn):
        store_id = list(persistence.sync_stores(db_conn, [make_config()]).values())[0]
        db_conn.commit()
        productos = [make_product(url=None), make_product(name=None)]

        restock_ids = persistence._save_one_store(db_conn, store_id, productos, date.today())
        db_conn.commit()

        assert restock_ids == []
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM store_product")
            assert cur.fetchone()[0] == 0
