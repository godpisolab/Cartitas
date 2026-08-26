"""Tests de routers/products.py vía TestClient contra Postgres real -- lo
que NO cubre test_products_service.py: serialización camelCase de verdad
en el JSON de respuesta, códigos de estado HTTP, auth, y el `Link` header
(docs/estandares-implementacion-api.md, sección 8)."""

from __future__ import annotations


class TestAuthRequerida:
    def test_sin_cabecera_authorization_devuelve_401_problem_json(self, client, seed_listing):
        seed_listing()

        resp = client.get("/products")

        assert resp.status_code == 401
        assert resp.headers["content-type"] == "application/problem+json"
        body = resp.json()
        assert body["status"] == 401
        assert body["title"]

    def test_api_key_sin_scope_read_devuelve_403(self, client, seed_listing, monkeypatch):
        import config
        monkeypatch.setattr(config, "API_KEYS", {"solo-escritura": frozenset({"write:subscriptions"})})
        seed_listing()

        resp = client.get("/products", headers={"Authorization": "Bearer solo-escritura"})

        assert resp.status_code == 403

    def test_api_key_desconocida_devuelve_401(self, client):
        resp = client.get("/products", headers={"Authorization": "Bearer no-existe"})
        assert resp.status_code == 401

    def test_con_scope_read_devuelve_200(self, client, seed_listing, auth_headers):
        seed_listing()

        resp = client.get("/products", headers=auth_headers)

        assert resp.status_code == 200


class TestSerializacionCamelCase:
    def test_json_de_respuesta_usa_camelcase_de_verdad(self, client, seed_listing, auth_headers):
        seed_listing(name_canonical="Booster Box OP16 EN", set_code="OP16")

        body = client.get("/products", headers=auth_headers).json()

        item = body["data"][0]
        assert "nameCanonical" in item
        assert "setCode" in item
        assert "storeCount" in item
        assert "anyInStock" in item
        # snake_case NO debe colarse en la respuesta real.
        assert "name_canonical" not in item
        assert "store_count" not in item

    def test_envelope_data_meta(self, client, seed_listing, auth_headers):
        seed_listing()

        body = client.get("/products", headers=auth_headers).json()

        assert "data" in body and "meta" in body
        assert set(body["meta"]) == {"page", "limit", "total"}


class TestQueryParamsCamelCase:
    def test_set_code_min_price_max_price_is_hot_en_camelcase(self, client, seed_listing, auth_headers):
        seed_listing(name_canonical="Barato", set_code="OP16", price=50.0, is_hot=True, store_name="TiendaA")
        seed_listing(name_canonical="Caro", set_code="OP17", price=200.0, is_hot=False, store_name="TiendaB")

        resp = client.get(
            "/products?setCode=OP16&minPrice=10&maxPrice=100&isHot=true", headers=auth_headers,
        )

        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["setCode"] == "OP16"


class TestLinkHeader:
    def test_sin_mas_paginas_no_hay_link_header(self, client, seed_listing, auth_headers):
        seed_listing()

        resp = client.get("/products?limit=20", headers=auth_headers)

        assert "Link" not in resp.headers

    def test_con_mas_paginas_el_link_header_apunta_a_next(self, client, seed_listing, auth_headers):
        for i in range(3):
            seed_listing(name_canonical=f"Producto {i}", store_name=f"Tienda{i}")

        resp = client.get("/products?page=1&limit=2", headers=auth_headers)

        assert 'rel="next"' in resp.headers["Link"]
        assert "page=2" in resp.headers["Link"]

    def test_pagina_intermedia_trae_next_y_prev(self, client, seed_listing, auth_headers):
        for i in range(6):
            seed_listing(name_canonical=f"Producto {i}", store_name=f"Tienda{i}")

        resp = client.get("/products?page=2&limit=2", headers=auth_headers)

        link = resp.headers["Link"]
        assert 'rel="next"' in link
        assert 'rel="prev"' in link


class TestValidacionQueryParams:
    def test_limit_por_encima_del_maximo_es_422_problem_json(self, client, auth_headers):
        resp = client.get("/products?limit=9999", headers=auth_headers)

        assert resp.status_code == 422
        assert resp.headers["content-type"] == "application/problem+json"

    def test_page_cero_es_422(self, client, auth_headers):
        resp = client.get("/products?page=0", headers=auth_headers)
        assert resp.status_code == 422
