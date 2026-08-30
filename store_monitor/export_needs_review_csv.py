"""Exporta la cola de `needs_review` a CSV con el motivo exacto por el que
cada fila no auto-confirma -- pensado para auditorías manuales fila a fila
(mismo patrón que las auditorías históricas de `docs/matching/motor-matching.md`,
antes hechas contra un CSV exportado a mano de Postgres).

Reutiliza el mismo camino que sigue `matcher.run_matching()`
(classify_with_category -> _best_candidate -> build_evidence -> decide) para
que el motivo mostrado sea exactamente la razón real de la decisión, no una
heurística aparte que se pueda desincronizar del código real.

Uso:
    python export_needs_review_csv.py [ruta_salida.csv] [--status needs_review|unmatched|all]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import matcher
import persistence
from shared.classify import classify_with_category

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "needs_review.csv"


def _motivo(classification, candidate, es_fallback: bool, raw_name: str, is_single_sku: bool) -> str:
    if candidate is None:
        return "sin_candidato_en_la_categoria"

    evidence = matcher.build_evidence(classification, candidate, es_fallback, raw_name, is_single_sku)

    if evidence.exact_name_match:
        return "nombre_exacto (debería estar confirmed -- revisar)"
    if evidence.set_code_match is False:
        return "set_code_no_coincide"
    if evidence.is_single_sku_category and not evidence.is_fallback_candidate and (
        not evidence.language_known or evidence.language_match
    ):
        return "categoria_unico_sku (debería estar confirmed -- revisar)"
    if evidence.set_code_match is None:
        if evidence.similarity_score is None:
            return "sin_set_code_sin_candidato"
        if evidence.similarity_score < matcher.REVIEW_SIMILARITY_THRESHOLD:
            return f"sin_set_code_similarity_baja_{evidence.similarity_score:.2f}"
        if evidence.similarity_score > matcher.CONFIRMED_SIMILARITY_THRESHOLD and evidence.language_match:
            return "sin_set_code_deberia_confirmar (revisar)"
        return f"sin_set_code_similarity_media_{evidence.similarity_score:.2f}"
    if evidence.is_fallback_candidate:
        return "candidato_solo_por_fallback_cross_categoria"
    if not evidence.language_known:
        return "idioma_no_detectado_en_raw_name"
    if not evidence.language_match:
        return f"idioma_no_coincide(clasificado={classification.language},candidato={candidate[3]})"
    if not evidence.packaging_match:
        return f"packaging_no_coincide(clasificado={classification.packaging},candidato={candidate[4]})"
    if evidence.quantity_ambiguous:
        return "cantidad_ambigua"
    return "motivo_desconocido (revisar decide())"


def export(conn, output_path: Path, status: str) -> int:
    category_ids = matcher._category_ids(conn)
    single_sku_categories = matcher._single_sku_categories(conn)

    with conn.cursor() as cur:
        if status == "all":
            cur.execute(
                "SELECT sp.id, s.name, sp.raw_name, sp.raw_variant, sp.raw_tags, sp.current_price, sp.match_status "
                "FROM store_product sp JOIN store s ON s.id = sp.store_id "
                "WHERE sp.match_status IN ('needs_review', 'unmatched') ORDER BY sp.id"
            )
        else:
            cur.execute(
                "SELECT sp.id, s.name, sp.raw_name, sp.raw_variant, sp.raw_tags, sp.current_price, sp.match_status "
                "FROM store_product sp JOIN store s ON s.id = sp.store_id "
                "WHERE sp.match_status = %s ORDER BY sp.id",
                (status,),
            )
        rows = cur.fetchall()

    fieldnames = [
        "store_product_id", "tienda", "raw_name", "raw_variant", "raw_tags", "precio", "match_status",
        "product_type", "set_code", "language", "packaging", "category_slug",
        "candidato_id", "candidato_nombre", "candidato_set_code", "candidato_language", "candidato_packaging",
        "similarity_score", "es_fallback", "motivo",
    ]

    written = 0
    with conn.cursor() as cur, open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for sp_id, store_name, raw_name, raw_variant, raw_tags, price, match_status in rows:
            classification, category_slug = classify_with_category(raw_name, raw_variant, raw_tags)
            category_id = category_ids.get(category_slug) if category_slug else None

            candidate, es_fallback = (None, False)
            if category_id is not None:
                candidate, es_fallback = matcher._best_candidate(
                    cur, category_id, raw_name, classification.set_code, classification.language,
                    classification.packaging,
                )

            motivo = _motivo(
                classification, candidate, es_fallback, raw_name, category_id in single_sku_categories,
            )

            writer.writerow({
                "store_product_id": sp_id,
                "tienda": store_name,
                "raw_name": raw_name,
                "raw_variant": raw_variant or "",
                "raw_tags": raw_tags or "",
                "precio": price,
                "match_status": match_status,
                "product_type": classification.product_type,
                "set_code": classification.set_code or "",
                "language": classification.language or "",
                "packaging": classification.packaging or "",
                "category_slug": category_slug or "",
                "candidato_id": candidate[0] if candidate else "",
                "candidato_nombre": candidate[1] if candidate else "",
                "candidato_set_code": candidate[2] if candidate else "",
                "candidato_language": candidate[3] if candidate else "",
                "candidato_packaging": candidate[4] if candidate else "",
                "similarity_score": f"{candidate[5]:.4f}" if candidate else "",
                "es_fallback": es_fallback,
                "motivo": motivo,
            })
            written += 1

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", nargs="?", default=str(DEFAULT_OUTPUT), help="Ruta del CSV de salida.")
    parser.add_argument("--status", choices=["needs_review", "unmatched", "all"], default="needs_review",
                         help="Qué match_status exportar (default: needs_review).")
    args = parser.parse_args()

    conn = persistence.get_connection()
    try:
        count = export(conn, Path(args.output), args.status)
    finally:
        conn.close()

    print(f"{count} filas exportadas a {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
