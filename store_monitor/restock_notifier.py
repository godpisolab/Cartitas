"""Disparador de notificación de restock (E.3 de cambios-necesarios-scraper.md).

Al detectar una transición agotado -> disponible (B.2, ya materializada como
fila en `restock_event` por persistence.py), este módulo:

1. Busca `restock_subscription` activas para ese product_id (cualquier
   tienda -- store_id NULL -- o específicamente la tienda que detectó el
   restock).
2. Envía un Web Push firmado con VAPID a cada una (`pywebpush`).
3. Registra cuántos envíos tuvieron éxito en `restock_event.subscribers_notified`.
4. Borra automáticamente las suscripciones que devuelven 410 Gone -- es el
   propio estándar Web Push diciendo "no vuelvas a intentarlo con este
   endpoint" (el usuario revocó el permiso o desinstaló), no una decisión
   nuestra.

Claves VAPID: `VAPID_PRIVATE_KEY_PATH` (ruta a un fichero .pem) y
`VAPID_CLAIMS_SUB` son variables de entorno. Sin fichero de claves, este
módulo no envía nada (con un AVISO) en vez de fallar -- todavía no hay un
frontend real registrando suscripciones, así que no tener claves configuradas
es la situación normal en desarrollo, no un error.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

from pywebpush import WebPushException, webpush

VAPID_PRIVATE_KEY_PATH = os.environ.get("VAPID_PRIVATE_KEY_PATH")
VAPID_CLAIMS_SUB = os.environ.get("VAPID_CLAIMS_SUB", "mailto:dev@example.com")


@dataclass
class NotifyResult             :
    sent: int
    dead_subscriptions_removed: int


def _send_one(endpoint: str, keys: dict, product_name: str) -> Optional[bool]:
    """True si se envió con éxito, False si falló por algo recuperable (se
    reintentará en el próximo restock del mismo producto, no ahora mismo),
    None si la suscripción está MUERTA (410 Gone -- ver E.3.4: hay que
    borrarla, es el estándar Web Push, no una decisión nuestra)."""
    try:
        webpush(
            subscription_info={"endpoint": endpoint, "keys": keys},
            data=json.dumps({"title": "¡De vuelta en stock!", "body": product_name}),
            vapid_private_key=VAPID_PRIVATE_KEY_PATH,
            vapid_claims={"sub": VAPID_CLAIMS_SUB},
        )
        return True
    except WebPushException as e:
        status = e.response.status_code if e.response is not None else None
        if status == 410:
            return None
        print(f"[restock] AVISO: envío fallido a {endpoint[:60]}... (status={status}): {e}")
        return False


def notify_for_restock_events(conn, restock_event_ids: list[int]) -> NotifyResult:
    """Punto de entrada llamado desde main() justo después de persistir
    (tanto tras el barrido diario como tras el refresco de calientes) con
    los `id` de restock_event recién creados -- evita tener que volver a
    consultar "qué es nuevo" desde cero."""
    if not restock_event_ids:
        return NotifyResult(0, 0)
    if not VAPID_PRIVATE_KEY_PATH:
        print("[restock] AVISO: VAPID_PRIVATE_KEY_PATH no configurada, no se envían notificaciones "
              "(normal si todavía no hay frontend registrando suscripciones)")
        return NotifyResult(0, 0)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT re.id, re.product_id, sp.store_id, p.name_canonical
            FROM restock_event re
            JOIN store_product sp ON sp.id = re.store_product_id
            JOIN product p ON p.id = re.product_id
            WHERE re.id = ANY(%s)
            """,
            (restock_event_ids,),
        )
        events = cur.fetchall()

    total_sent, total_dead = 0, 0

    for event_id, product_id, store_id, product_name in events:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, push_endpoint, push_keys FROM restock_subscription "
                "WHERE product_id = %s AND (store_id IS NULL OR store_id = %s)",
                (product_id, store_id),
            )
            subscriptions = cur.fetchall()

        sent, dead_ids = 0, []
        for sub_id, endpoint, keys in subscriptions:
            outcome = _send_one(endpoint, keys, product_name)
            if outcome is None:
                dead_ids.append(sub_id)
            elif outcome:
                sent += 1

        with conn.cursor() as cur:
            if dead_ids:
                cur.execute("DELETE FROM restock_subscription WHERE id = ANY(%s)", (dead_ids,))
            cur.execute("UPDATE restock_event SET subscribers_notified = %s WHERE id = %s", (sent, event_id))
        conn.commit()

        total_sent += sent
        total_dead += len(dead_ids)

    print(f"[restock] {total_sent} notificaciones enviadas, {total_dead} suscripciones muertas eliminadas")
    return NotifyResult(total_sent, total_dead)
