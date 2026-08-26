"""Reflejo de la tabla `store` -- ver docstring de models/game.py.
`store_platform` ya existe como ENUM nativo de Postgres."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlmodel import Field, SQLModel


class StorePlatform(str, Enum):
    WOOCOMMERCE = "woocommerce"
    PRESTASHOP = "prestashop"
    SHOPIFY = "shopify"
    ODOO = "odoo"
    OPENCART = "opencart"
    CUSTOM = "custom"


class Store(SQLModel, table=True):
    __tablename__ = "store"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    website_url: str
    sitemap_url: str | None = None
    platform: StorePlatform = Field(
        sa_column=Column(
            PGEnum("woocommerce", "prestashop", "shopify", "odoo", "opencart", "custom",
                   name="store_platform", create_type=False),
        ),
    )
    active: bool = True
    has_structured_api: bool = False
    api_endpoint: str | None = None
    crawl_delay_seconds: int | None = None
    robots_checked_at: datetime | None = None
    disallowed: bool = False
    consecutive_failures: int = 0
    backoff_until: datetime | None = None
    last_scraped_at: datetime | None = None
    last_sitemap_checked_at: datetime | None = None
