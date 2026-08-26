"""Tests de restock_notifier.py -- sin cobertura hasta ahora (bloque E).
Se mockea pywebpush.webpush directamente en vez de levantar un servidor
HTTP local (más rápido, mismo comportamiento verificado a mano durante el
desarrollo con un servidor mock real -- ver README)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest
from pywebpush import WebPushException

import persistence
import restock_notifier
from base_script import Platform, Product, StoreConfig


def make_config(label="Tienda", domain="https://tienda.example"):
    return StoreConfig(label, domain, Platform.SHOPIFY, shopify_collection="x")


def seed_restock_event(conn, push_endpoint="https://push.example/abc", store_id_scope=None) -> tuple[int, int]:
    """Crea game/category/product/store/store_product/restock_event +
    (opcional) restock_subscription mínimos. Devuelve (restock_event_id, store_id)."""
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
            "INSERT INTO product (game_id, category_id, name_canonical) VALUES (%s, %s, 'Producto Test') "
            "RETURNING id",
            (game_id, category_id),
        )
        product_id = cur.fetchone()[0]
    conn.commit()

    product = Product(store="Tienda", platform="shopify", id_product=None, name="Producto Test", variant=None,
                       product_type="", main_set=None, set_code=None, language=None, price=10.0,
                       stock_status="DISPONIBLE", url="https://tienda.example/p1", sku=None, image_url=None)
    persistence._save_one_store(conn, store_id, [product], date.today())
    with conn.cursor() as cur:
        cur.execute("UPDATE store_product SET product_id = %s", (product_id,))
        cur.execute(
            "INSERT INTO restock_event (store_product_id, product_id) "
            "SELECT id, %s FROM store_product LIMIT 1 RETURNING id",
            (product_id,),
        )
        restock_event_id = cur.fetchone()[0]
        if push_endpoint:
            cur.execute(
                "INSERT INTO restock_subscription (product_id, store_id, push_endpoint, push_keys) "
                "VALUES (%s, %s, %s, %s)",
                (product_id, store_id_scope, push_endpoint, '{"p256dh": "x", "auth": "y"}'),
            )
    conn.commit()
    return restock_event_id, store_id


class TestNotifyForRestockEvents:
    def test_lista_vacia_no_hace_nada(self, db_conn):
        result = restock_notifier.notify_for_restock_events(db_conn, [])
        assert result.sent == 0
        assert result.dead_subscriptions_removed == 0

    def test_sin_vapid_configurada_no_envia_y_avisa(self, db_conn, monkeypatch, capsys):
        monkeypatch.setattr(restock_notifier, "VAPID_PRIVATE_KEY_PATH", None)
        event_id, _ = seed_restock_event(db_conn)

        result = restock_notifier.notify_for_restock_events(db_conn, [event_id])

        assert result.sent == 0
        assert "AVISO" in capsys.readouterr().out

    def test_envio_exitoso_incrementa_sent_y_actualiza_subscribers_notified(self, db_conn, monkeypatch):
        monkeypatch.setattr(restock_notifier, "VAPID_PRIVATE_KEY_PATH", "/fake/key.pem")
        monkeypatch.setattr(restock_notifier, "webpush", MagicMock(return_value=None))
        event_id, _ = seed_restock_event(db_conn)

        result = restock_notifier.notify_for_restock_events(db_conn, [event_id])

        assert result.sent == 1
        assert result.dead_subscriptions_removed == 0
        with db_conn.cursor() as cur:
            cur.execute("SELECT subscribers_notified FROM restock_event WHERE id = %s", (event_id,))
            assert cur.fetchone()[0] == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM restock_subscription")
            assert cur.fetchone()[0] == 1  # sigue viva

    def test_410_gone_borra_la_suscripcion_y_no_cuenta_como_enviado(self, db_conn, monkeypatch):
        monkeypatch.setattr(restock_notifier, "VAPID_PRIVATE_KEY_PATH", "/fake/key.pem")
        fake_response = MagicMock(status_code=410)
        monkeypatch.setattr(restock_notifier, "webpush",
                             MagicMock(side_effect=WebPushException("gone", response=fake_response)))
        event_id, _ = seed_restock_event(db_conn)

        result = restock_notifier.notify_for_restock_events(db_conn, [event_id])

        assert result.sent == 0
        assert result.dead_subscriptions_removed == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM restock_subscription")
            assert cur.fetchone()[0] == 0  # borrada

    def test_error_no_410_no_borra_la_suscripcion_ni_cuenta_como_enviado(self, db_conn, monkeypatch, capsys):
        monkeypatch.setattr(restock_notifier, "VAPID_PRIVATE_KEY_PATH", "/fake/key.pem")
        fake_response = MagicMock(status_code=500)
        monkeypatch.setattr(restock_notifier, "webpush",
                             MagicMock(side_effect=WebPushException("boom", response=fake_response)))
        event_id, _ = seed_restock_event(db_conn)

        result = restock_notifier.notify_for_restock_events(db_conn, [event_id])

        assert result.sent == 0
        assert result.dead_subscriptions_removed == 0
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM restock_subscription")
            assert cur.fetchone()[0] == 1  # NO se borra -- puede ser un fallo recuperable
        assert "AVISO" in capsys.readouterr().out

    def test_suscripcion_de_otra_tienda_no_recibe_notificacion(self, db_conn, monkeypatch):
        # store_id IS NOT NULL y distinto de la tienda que detectó el
        # restock -- no debe recibir el push (WHERE store_id IS NULL OR
        # store_id = %s).
        monkeypatch.setattr(restock_notifier, "VAPID_PRIVATE_KEY_PATH", "/fake/key.pem")
        mock_webpush = MagicMock(return_value=None)
        monkeypatch.setattr(restock_notifier, "webpush", mock_webpush)

        event_id, store_id = seed_restock_event(db_conn, push_endpoint=None)
        otra_tienda_ids = persistence.sync_stores(db_conn, [make_config(label="OtraTienda",
                                                                         domain="https://otra-tienda.example")])
        db_conn.commit()
        otra_store_id = otra_tienda_ids["https://otra-tienda.example"]

        with db_conn.cursor() as cur:
            cur.execute("SELECT product_id FROM restock_event WHERE id = %s", (event_id,))
            product_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO restock_subscription (product_id, store_id, push_endpoint, push_keys) "
                "VALUES (%s, %s, %s, %s)",
                (product_id, otra_store_id, "https://push.example/otra-tienda", '{"p256dh":"x","auth":"y"}'),
            )
        db_conn.commit()

        result = restock_notifier.notify_for_restock_events(db_conn, [event_id])

        assert result.sent == 0
        mock_webpush.assert_not_called()
