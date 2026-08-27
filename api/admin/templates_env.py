"""Instancia única de Jinja2Templates, compartida por todas las rutas de
`admin/routes/` -- mismo motivo que un único `engine` en `db.py`."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
