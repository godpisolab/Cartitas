"""Paginación compartida: envelope `{data, meta}` + `Link` header (RFC 8288)
-- ver docs/estandares-api-app-tcg.md sección 2 y
docs/estandares-implementacion-api.md sección 6. Un único sitio para el
límite máximo y el formato del `Link`; ningún router lo reimplementa."""

from __future__ import annotations

from fastapi import Request

MAX_LIMIT = 100


def build_link_header(request: Request, *, page: int, limit: int, total: int) -> str | None:
    """`rel="next"`/`rel="prev"` sobre la URL actual, cambiando solo `page`.
    None si no hay página siguiente ni anterior (caso típico: un único
    resultado que cabe en una sola página)."""
    last_page = max((total + limit - 1) // limit, 1)
    links = []

    def _url_for(target_page: int) -> str:
        params = request.query_params.multi_items()
        params = [(k, v) for k, v in params if k != "page"]
        params.append(("page", str(target_page)))
        query = "&".join(f"{k}={v}" for k, v in params)
        return f"{request.url.path}?{query}"

    if page < last_page:
        links.append(f'<{_url_for(page + 1)}>; rel="next"')
    if page > 1:
        links.append(f'<{_url_for(page - 1)}>; rel="prev"')

    return ", ".join(links) if links else None
