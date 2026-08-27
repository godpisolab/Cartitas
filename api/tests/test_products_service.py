"""Tests de services/products.py -- integración contra Postgres real
(cartitas_test), sin FastAPI ni HTTP de por medio (ver
docs/estandares-implementacion-api.md, sección 8: el riesgo real está en
si la query hace lo que crees)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

import services.products as products_service
from errors import ConflictError, NotFoundError
from schemas.products import ProductCreate, ProductFilters, ProductPatch


class TestSoloConfirmedCuentan:
    def test_needs_review_no_aparece_en_absoluto(self, session, seed_listing):
        seed_listing(match_status="needs_review")

        result = products_service.search(session, ProductFilters())

        assert result.data == []
        assert result.meta.total == 0

    def test_unmatched_no_aparece(self, session, seed_listing):
        seed_listing(match_status="unmatched")

        result = products_service.search(session, ProductFilters())

        assert result.data == []

    def test_confirmed_si_aparece_con_los_agregados_correctos(self, session, seed_listing):
        seed_listing(match_status="confirmed", price=109.90, stock_status="disponible")

        result = products_service.search(session, ProductFilters())

        assert len(result.data) == 1
        item = result.data[0]
        assert item.min_price == 109.90
        assert item.store_count == 1
        assert item.any_in_stock is True


class TestAgregadosEntreVariasTiendas:
    def test_min_price_es_el_minimo_entre_tiendas_confirmadas(self, session, seed_listing):
        product_id = seed_listing(store_name="Cardzone", price=120.0)
        seed_listing(store_name="Pokemillon", price=99.90, product_id=product_id)

        result = products_service.search(session, ProductFilters())

        assert len(result.data) == 1  # sigue siendo UN producto, no dos
        assert result.data[0].min_price == 99.90

    def test_store_count_cuenta_tiendas_distintas_no_filas(self, session, seed_listing):
        product_id = seed_listing(store_name="Cardzone", price=100.0)
        # Segunda fila del MISMO product en la MISMA tienda (otra variante,
        # p.ej. idioma) -- no debe sumar una segunda tienda al recuento.
        seed_listing(store_name="Cardzone", price=105.0, product_id=product_id)

        result = products_service.search(session, ProductFilters())

        assert result.data[0].store_count == 1
        assert result.data[0].min_price == 100.0

    def test_any_in_stock_true_si_alguna_tienda_tiene_stock(self, session, seed_listing):
        product_id = seed_listing(store_name="Cardzone", price=100.0, stock_status="agotado")
        seed_listing(store_name="Pokemillon", price=120.0, stock_status="disponible", product_id=product_id)

        result = products_service.search(session, ProductFilters())

        assert result.data[0].any_in_stock is True

    def test_min_price_none_no_revienta_si_el_unico_confirmado_no_tiene_precio(self, session, seed_listing):
        # Bug real encontrado en producción (2026-08-27): un store_product
        # confirmado con current_price NULL (preventa, o el scraper no pudo
        # parsear el precio esa pasada) hacía que MIN() devolviera NULL, y
        # float(None) reventaba GET /products entero -- no solo este
        # producto, la petición completa.
        seed_listing(price=None)

        result = products_service.search(session, ProductFilters())

        assert len(result.data) == 1
        assert result.data[0].min_price is None


class TestFiltros:
    def test_filtro_por_game(self, session, seed_listing):
        seed_listing(game_slug="one-piece")
        seed_listing(game_slug="pokemon", name_canonical="Booster Pokemon", store_name="TiendaPoke")

        result = products_service.search(session, ProductFilters(game="one-piece"))

        assert len(result.data) == 1
        assert result.data[0].game == "one-piece"

    def test_filtro_por_category(self, session, seed_listing):
        seed_listing(category_slug="booster-box")
        seed_listing(category_slug="starter-deck", name_canonical="Starter Deck ST01",
                     store_name="TiendaStarter")

        result = products_service.search(session, ProductFilters(category="starter-deck"))

        assert len(result.data) == 1
        assert result.data[0].category == "starter-deck"

    def test_filtro_por_set_code(self, session, seed_listing):
        seed_listing(set_code="OP16")
        seed_listing(set_code="OP17", name_canonical="Booster Box OP17 EN", store_name="TiendaOP17")

        result = products_service.search(session, ProductFilters(set_code="OP17"))

        assert len(result.data) == 1
        assert result.data[0].set_code == "OP17"

    def test_filtro_por_language(self, session, seed_listing):
        from models.product import ProductLanguage
        seed_listing(language="EN")
        seed_listing(language="JP", name_canonical="Booster Box OP16 JP", store_name="TiendaJP")

        result = products_service.search(session, ProductFilters(language=ProductLanguage.JP))

        assert len(result.data) == 1
        assert result.data[0].language == ProductLanguage.JP

    def test_filtro_min_max_price(self, session, seed_listing):
        seed_listing(price=50.0, name_canonical="Barato", store_name="TiendaBarata")
        seed_listing(price=150.0, name_canonical="Caro", store_name="TiendaCara")

        result = products_service.search(session, ProductFilters(min_price=100.0))
        assert [item.min_price for item in result.data] == [150.0]

        result = products_service.search(session, ProductFilters(max_price=100.0))
        assert [item.min_price for item in result.data] == [50.0]

    def test_filtro_is_hot(self, session, seed_listing):
        seed_listing(is_hot=True, name_canonical="Caliente", store_name="TiendaHot")
        seed_listing(is_hot=False, name_canonical="Frio", store_name="TiendaFria")

        result = products_service.search(session, ProductFilters(is_hot=True))

        assert len(result.data) == 1
        assert result.data[0].name_canonical == "Caliente"

    def test_filtro_q_texto_libre_usa_similitud(self, session, seed_listing):
        seed_listing(name_canonical="Booster Box: The Time of Battle OP-16 EN")
        seed_listing(name_canonical="Playmat oficial One Piece", store_name="TiendaPlaymat",
                     category_slug="playmat")

        result = products_service.search(session, ProductFilters(q="Booster Box Time of Battle"))

        assert len(result.data) == 1
        assert "Time of Battle" in result.data[0].name_canonical


class TestPaginacion:
    def test_total_refleja_todas_las_paginas_no_solo_la_actual(self, session, seed_listing):
        for i in range(3):
            seed_listing(name_canonical=f"Producto {i}", store_name=f"Tienda{i}")

        result = products_service.search(session, ProductFilters(page=1, limit=2))

        assert len(result.data) == 2
        assert result.meta.total == 3

    def test_segunda_pagina_trae_el_resto(self, session, seed_listing):
        for i in range(3):
            seed_listing(name_canonical=f"Producto {i}", store_name=f"Tienda{i}")

        result = products_service.search(session, ProductFilters(page=2, limit=2))

        assert len(result.data) == 1


class TestGetById:
    def test_404_si_no_existe(self, session):
        with pytest.raises(NotFoundError):
            products_service.get_by_id(session, 999)

    def test_404_si_existe_pero_sin_ningun_confirmado(self, session, seed_listing):
        product_id = seed_listing(match_status="needs_review")

        with pytest.raises(NotFoundError):
            products_service.get_by_id(session, product_id)

    def test_listings_ordenados_de_mas_barato_a_mas_caro(self, session, seed_listing):
        product_id = seed_listing(store_name="Cara", price=150.0)
        seed_listing(store_name="Barata", price=90.0, product_id=product_id)

        detail = products_service.get_by_id(session, product_id)

        assert [listing.price for listing in detail.listings] == [90.0, 150.0]

    def test_solo_incluye_listings_confirmados(self, session, seed_listing):
        product_id = seed_listing(store_name="Confirmada", price=100.0, match_status="confirmed")
        seed_listing(store_name="Pendiente", price=50.0, match_status="needs_review", product_id=product_id)

        detail = products_service.get_by_id(session, product_id)

        assert len(detail.listings) == 1
        assert detail.listings[0].store_name == "Confirmada"


class TestGetPriceHistory:
    def test_404_si_producto_no_existe(self, session):
        with pytest.raises(NotFoundError):
            products_service.get_price_history(session, 999, None)

    def test_agregado_entre_tiendas_toma_el_minimo_por_dia(self, session, seed_listing):
        product_id = seed_listing(store_name="A")
        seed_listing(store_name="B", product_id=product_id)
        sp_ids = [row[0] for row in session.exec(
            text("SELECT id FROM store_product WHERE product_id = :p ORDER BY id"), params={"p": product_id},
        ).all()]

        today = date.today()
        session.exec(text(
            "INSERT INTO price_history (store_product_id, price, stock_status, scraped_date) "
            "VALUES (:sp, 100.0, 'disponible', :d)"
        ), params={"sp": sp_ids[0], "d": today})
        session.exec(text(
            "INSERT INTO price_history (store_product_id, price, stock_status, scraped_date) "
            "VALUES (:sp, 80.0, 'agotado', :d)"
        ), params={"sp": sp_ids[1], "d": today})
        session.commit()

        series = products_service.get_price_history(session, product_id, None)

        assert len(series.series) == 1
        assert series.series[0].min_price == 80.0
        # alguna tienda tenía stock ese día -- "disponible" gana aunque la
        # más barata estuviera agotada (docs/api-endpoints-v1.md sección 1).
        assert series.series[0].stock_status.value == "disponible"

    def test_con_store_id_devuelve_solo_esa_tienda(self, session, seed_listing):
        product_id = seed_listing(store_name="A", price=100.0)
        seed_listing(store_name="B", price=80.0, product_id=product_id)
        rows = session.exec(
            text("SELECT id, store_id FROM store_product WHERE product_id = :p ORDER BY id"),
            params={"p": product_id},
        ).all()
        sp_a_id, store_a_id = rows[0]

        session.exec(text(
            "INSERT INTO price_history (store_product_id, price, stock_status, scraped_date) "
            "VALUES (:sp, 100.0, 'disponible', :d)"
        ), params={"sp": sp_a_id, "d": date.today()})
        session.commit()

        series = products_service.get_price_history(session, product_id, store_a_id)

        assert series.store_id == store_a_id
        assert len(series.series) == 1
        assert series.series[0].min_price == 100.0

    def test_sin_historico_devuelve_serie_vacia(self, session, seed_listing):
        product_id = seed_listing()

        series = products_service.get_price_history(session, product_id, None)

        assert series.series == []


class TestCreateProduct:
    def _body(self, game_id, category_id, **overrides):
        defaults = dict(game_id=game_id, category_id=category_id, set_code="OP17", main_set="OP17",
                         language="EN", name_canonical="Booster Box OP17 EN", image_url=None,
                         is_hot=False, hot_until=None)
        defaults.update(overrides)
        return ProductCreate(**defaults)

    def _seed_game_and_category(self, session) -> tuple[int, int]:
        game_id = session.exec(
            text("INSERT INTO game (name, slug) VALUES ('One Piece', 'one-piece') RETURNING id"),
        ).first()[0]
        category_id = session.exec(
            text("INSERT INTO category (name, slug) VALUES ('Booster Box', 'booster-box') RETURNING id"),
        ).first()[0]
        session.commit()
        return game_id, category_id

    def test_crea_producto_nuevo(self, session):
        game_id, category_id = self._seed_game_and_category(session)

        detail = products_service.create_product(session, self._body(game_id, category_id))

        assert detail.name_canonical == "Booster Box OP17 EN"
        assert detail.listings == []

    def test_409_si_mismo_game_id_y_name_canonical(self, session):
        game_id, category_id = self._seed_game_and_category(session)
        products_service.create_product(session, self._body(game_id, category_id))

        with pytest.raises(ConflictError):
            products_service.create_product(session, self._body(game_id, category_id))


class TestPatchProduct:
    def test_404_si_no_existe(self, session):
        with pytest.raises(NotFoundError):
            products_service.patch_product(session, 999, ProductPatch(is_hot=True))

    def test_edita_solo_los_campos_pasados(self, session, seed_listing):
        product_id = seed_listing(is_hot=False)

        updated = products_service.patch_product(session, product_id, ProductPatch(is_hot=True))

        assert updated.name_canonical  # nombre original intacto
        # is_hot no forma parte de ProductDetail -- se verifica indirectamente
        # releyendo la fila.
        row = session.exec(text("SELECT is_hot FROM product WHERE id = :id"), params={"id": product_id}).first()
        assert row[0] is True

    def test_409_si_name_canonical_colisiona_con_otro_producto(self, session, seed_listing):
        product_a = seed_listing(name_canonical="Producto A", store_name="TiendaA")
        seed_listing(name_canonical="Producto B", store_name="TiendaB")

        with pytest.raises(ConflictError):
            products_service.patch_product(session, product_a, ProductPatch(name_canonical="Producto B"))

    def test_no_colisiona_consigo_mismo(self, session, seed_listing):
        product_id = seed_listing(name_canonical="Producto A")

        # Debe poder "editar" con el mismo nombre que ya tiene sin 409.
        updated = products_service.patch_product(session, product_id, ProductPatch(name_canonical="Producto A"))

        assert updated.name_canonical == "Producto A"
