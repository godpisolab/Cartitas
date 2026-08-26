-- ============================================================
-- App de stock unificado TCG (Pokémon + One Piece) — Esquema PostgreSQL
-- ============================================================

-- Extensión para matching difuso de texto (raw_name de tienda vs name_canonical)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- Tipos ENUM — más seguros que VARCHAR libre, validados por la BBDD
-- ============================================================
CREATE TYPE product_language AS ENUM ('EN', 'JP', 'ES');
CREATE TYPE store_platform AS ENUM ('woocommerce', 'prestashop', 'shopify', 'odoo', 'opencart', 'custom');
-- 'not_applicable': asignado automáticamente por el pipeline de matching a LOTE_CARTAS/OTROS,
-- que nunca tendrán un producto canónico razonable, no entran en la cola de revisión
CREATE TYPE match_status_enum AS ENUM ('unmatched', 'needs_review', 'confirmed', 'not_applicable');
CREATE TYPE stock_status_enum AS ENUM ('disponible', 'agotado', 'desconocido');

-- ============================================================
-- game
-- ============================================================
CREATE TABLE game (
    id   SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    slug VARCHAR(50) NOT NULL UNIQUE
);

-- ============================================================
-- category (jerárquica)
-- ============================================================
CREATE TABLE category (
    id                  SERIAL PRIMARY KEY,
    parent_category_id  INTEGER REFERENCES category(id),
    name                VARCHAR(100) NOT NULL,
    slug                VARCHAR(100) NOT NULL UNIQUE
);

-- ============================================================
-- product (canónico)
-- ============================================================
CREATE TABLE product (
    id             SERIAL PRIMARY KEY,
    game_id        INTEGER NOT NULL REFERENCES game(id),
    category_id    INTEGER NOT NULL REFERENCES category(id),
    set_code       VARCHAR(20),
    main_set       VARCHAR(10),  -- set de lanzamiento (ej. OP16); distinto de set_code para Starter Deck/Illustration Box con código propio
    language       product_language,
    name_canonical VARCHAR(255) NOT NULL,
    image_url      TEXT,
    is_hot         BOOLEAN NOT NULL DEFAULT false,
    hot_until      DATE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Índice trigram: permite `similarity(name_canonical, 'texto raspado')` para el matching automático
CREATE INDEX idx_product_name_trgm ON product USING GIN (name_canonical gin_trgm_ops);
CREATE INDEX idx_product_set_code ON product (set_code);
CREATE INDEX idx_product_main_set ON product (main_set);
-- Índice parcial: solo indexa los calientes, que son pocos — barato y rápido de consultar
CREATE INDEX idx_product_is_hot ON product (is_hot) WHERE is_hot = true;

-- ============================================================
-- store
-- ============================================================
CREATE TABLE store (
    id                       SERIAL PRIMARY KEY,
    name                     VARCHAR(150) NOT NULL,
    website_url              TEXT NOT NULL UNIQUE,  -- clave estable para el UPSERT STORES (código) -> store (bloque B.1)
    sitemap_url              TEXT,
    platform                 store_platform NOT NULL,
    active                   BOOLEAN NOT NULL DEFAULT true,
    has_structured_api       BOOLEAN NOT NULL DEFAULT false,
    api_endpoint             TEXT,
    crawl_delay_seconds      INTEGER,
    robots_checked_at        TIMESTAMPTZ,  -- última vez que se refrescó la caché de robots.txt (no confundir con last_scraped_at)
    disallowed               BOOLEAN NOT NULL DEFAULT false,  -- robots.txt prohíbe la URL que scrapeamos (A.2, cacheado junto a robots_checked_at)
    consecutive_failures     INTEGER NOT NULL DEFAULT 0,  -- fallos seguidos ENTRE ejecuciones (A.3) -- ver backoff_until
    backoff_until            TIMESTAMPTZ,
    last_scraped_at          TIMESTAMPTZ,
    last_sitemap_checked_at  TIMESTAMPTZ
);

-- ============================================================
-- store_product (listado por tienda)
-- ============================================================
CREATE TABLE store_product (
    id                    SERIAL PRIMARY KEY,
    store_id              INTEGER NOT NULL REFERENCES store(id),
    product_id            INTEGER REFERENCES product(id),            -- NULL hasta confirmar
    match_confidence      REAL,                                      -- score de similarity() del match confirmado (auto o manual)
    match_status          match_status_enum NOT NULL DEFAULT 'unmatched',
    store_url             TEXT NOT NULL,
    store_sku             VARCHAR(255),                              -- id_product/sku de la tienda; clave de respaldo más estable que store_url
    raw_name              VARCHAR(500) NOT NULL,
    raw_variant           VARCHAR(255),                              -- título de variante tal cual (idioma, grading...) para depurar casos como Pokemillon
    current_price         NUMERIC(10,2),
    stock_status          stock_status_enum NOT NULL DEFAULT 'desconocido',
    last_etag             VARCHAR(255),
    last_modified_header  VARCHAR(255),
    last_checked_at       TIMESTAMPTZ,
    -- Incluye raw_variant (no solo store_url): varias variantes de un mismo
    -- producto Shopify (idioma, sobre/caja...) comparten la misma store_url
    -- con stock/precio independientes por variante (caso Pokemillon, D.5) --
    -- sin raw_variant aquí, la segunda variante pisaría a la primera en vez
    -- de convivir como fila propia.
    UNIQUE (store_id, store_url, raw_variant)
);

-- El top-3 de candidatos (C.3) ya NO se guarda como sugerencia única: se calcula en caliente en el
-- endpoint GET /matches/pending vía `ORDER BY similarity(name_canonical, raw_name) DESC LIMIT 3`,
-- apoyado en idx_product_name_trgm. Evita guardar una sugerencia que quedaría obsoleta en cuanto se
-- dé de alta un producto canónico nuevo más parecido.

CREATE INDEX idx_store_product_raw_name_trgm ON store_product USING GIN (raw_name gin_trgm_ops);
-- Índice parcial: la "cola de revisión" es justo esta consulta — solo indexa lo pendiente
CREATE INDEX idx_store_product_pending ON store_product (match_status) WHERE match_status != 'confirmed';
CREATE INDEX idx_store_product_product_id ON store_product (product_id);

-- ============================================================
-- price_history (1 fila por store_product y día)
-- ============================================================
CREATE TABLE price_history (
    id                BIGSERIAL PRIMARY KEY,
    store_product_id  INTEGER NOT NULL REFERENCES store_product(id),
    price             NUMERIC(10,2),
    stock_status      stock_status_enum NOT NULL,
    scraped_date      DATE NOT NULL,
    UNIQUE (store_product_id, scraped_date)  -- garantiza 1 fila/día; permite UPSERT si se reprocesa el mismo día
);

-- Cubre directamente la consulta "dame la curva de precio de este store_product, más reciente primero"
CREATE INDEX idx_price_history_curve ON price_history (store_product_id, scraped_date DESC);

-- ============================================================
-- restock_subscription
-- ============================================================
CREATE TABLE restock_subscription (
    id             SERIAL PRIMARY KEY,
    product_id     INTEGER NOT NULL REFERENCES product(id),
    store_id       INTEGER REFERENCES store(id),  -- NULL = cualquier tienda (comportamiento único en v1)
    push_endpoint  TEXT NOT NULL,
    push_keys      JSONB NOT NULL,  -- {p256dh, auth} que exige la Web Push API
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (product_id, store_id, push_endpoint)  -- evita suscripciones duplicadas del mismo dispositivo
);

CREATE INDEX idx_restock_subscription_product ON restock_subscription (product_id);

-- ============================================================
-- restock_event (log/auditoría)
-- ============================================================
CREATE TABLE restock_event (
    id                     BIGSERIAL PRIMARY KEY,
    store_product_id       INTEGER NOT NULL REFERENCES store_product(id),
    product_id             INTEGER NOT NULL REFERENCES product(id),
    detected_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    subscribers_notified   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_restock_event_product ON restock_event (product_id, detected_at DESC);
