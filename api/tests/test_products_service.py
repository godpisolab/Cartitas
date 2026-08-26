"""Tests de services/products.py -- integración contra Postgres real
(cartitas_test), sin FastAPI ni HTTP de por medio (ver
docs/estandares-implementacion-api.md, sección 8: el riesgo real está en
si la query hace lo que crees)."""

from __future__ import annotations

import services.products as products_service
from schemas.products import ProductFilters


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
