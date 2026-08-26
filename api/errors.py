"""Excepciones de dominio + su mapeo a RFC 7807 (Problem Details), en un
único sitio -- ver docs/estandares-implementacion-api.md, sección 5.
`services/` lanza estas excepciones por nombre; nunca `HTTPException` a
mano. El mapeo a código HTTP + `application/problem+json` vive aquí y solo
aquí, registrado una vez en `main.py`.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Base de toda excepción de dominio de la API. `status_code`/`title`
    son de la SUBCLASE (nunca se instancia directamente) -- `detail` es el
    mensaje específico de este caso concreto."""

    status_code: int = 500
    title: str = "Error interno"

    def __init__(self, detail: str | None = None):
        self.detail = detail
        super().__init__(detail or self.title)


class UnauthorizedError(ApiError):
    status_code = 401
    title = "No autenticado"


class ForbiddenError(ApiError):
    status_code = 403
    title = "Prohibido"


class NotFoundError(ApiError):
    status_code = 404
    title = "No encontrado"


class ConflictError(ApiError):
    status_code = 409
    title = "Conflicto"


class UnprocessableEntityError(ApiError):
    status_code = 422
    title = "Entidad no procesable"


def _problem_response(*, status_code: int, title: str, detail: str | None, instance: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": "about:blank",
            "title": title,
            "status": status_code,
            "detail": detail,
            "instance": instance,
        },
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Registrado una vez desde main.py. Un único handler para TODA la
    jerarquía de ApiError (Starlette recorre la MRO de la excepción hasta
    encontrar un handler registrado, así que las subclases no necesitan
    handler propio) más un handler para los 422 que genera FastAPI solo
    validando query params/body -- sin esto, esos errores saldrían como
    `application/json` normal en vez de `problem+json`, rompiendo la
    promesa de RFC 7807 para el caso más común de error de cliente."""

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _problem_response(
            status_code=exc.status_code, title=exc.title, detail=exc.detail, instance=str(request.url),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem_response(
            status_code=422, title=UnprocessableEntityError.title, detail=str(exc.errors()),
            instance=str(request.url),
        )
