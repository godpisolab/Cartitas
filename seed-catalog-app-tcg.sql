-- ============================================================
-- Datos de referencia fijos: game + category (D.2)
-- ============================================================
-- A diferencia de `product` (que se siembra por lanzamiento de set, ver
-- modelo-datos-app-tcg.md), game/category son estructurales: el matcher
-- (bloque C de cambios-necesarios-scraper.md) no tiene nada contra qué
-- comparar sin esto. Idempotente (ON CONFLICT DO NOTHING) para poder
-- reaplicarse sin duplicar filas.

INSERT INTO game (name, slug) VALUES
    ('One Piece', 'one-piece'),
    ('Pokémon', 'pokemon')
ON CONFLICT (slug) DO NOTHING;

-- Jerarquía real (Recognition Pipeline, docs/propuestas/guia_nuevo_matcher.md):
-- 10 tipos matcheables, dos padres. Ya no hay padre 'single-card' -- su
-- único hijo (Promo Card) subió a Fase 0 del pipeline (not_applicable
-- siempre, ver más abajo), así que se queda sin categoría y el padre sin
-- hijos no aporta nada.
INSERT INTO category (name, slug) VALUES
    ('Sellado', 'sellado'),
    ('Accesorios', 'accesorios')
ON CONFLICT (slug) DO NOTHING;

-- BOOSTER_BOX/BOOSTER_PACK/BOOSTER_CASE (3 categorías separadas antes) se
-- funden en una sola ('One Piece', slug 'one-piece') -- la distinción
-- sobre/display/case pasa a ser la columna product.packaging, no la
-- categoría. Igual para Extra Booster (antes vivía sin categoría propia
-- dentro de booster-pack/box, vía fallback de código EB) y Premium
-- Booster Box (antes 'Premium Collection' junto con Premium Card
-- Collection, ahora son dos productos distintos con categoría propia cada
-- uno). Learn Deck se funde en Starter Deck (mismo tipo de producto, solo
-- cambia el nombre de línea). Sleeves es nueva (antes caía en OTROS, sin
-- categoría).
INSERT INTO category (parent_category_id, name, slug)
SELECT (SELECT id FROM category WHERE slug = 'sellado'), name, slug
FROM (VALUES
    ('One Piece', 'one-piece'),
    ('Extra Booster', 'extra-booster'),
    ('Premium Booster Box', 'premium-booster-box'),
    ('Premium Card Collection', 'premium-card-collection'),
    ('Starter Deck', 'starter-deck'),
    ('Illustration Box', 'illustration-box'),
    ('Double Pack', 'double-pack'),
    ('Devil Fruits Collection', 'devil-fruits-collection'),
    ('Sleeves', 'sleeves')
) AS t(name, slug)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO category (parent_category_id, name, slug)
SELECT (SELECT id FROM category WHERE slug = 'accesorios'), name, slug
FROM (VALUES
    ('Playmat', 'playmat')
) AS t(name, slug)
ON CONFLICT (slug) DO NOTHING;

-- OJO: LOTE_CARTAS/PROMO_CARD/MYSTERY_PACK/DICE_ACCESSORY/OTROS (Fase 0 del
-- pipeline + catch-all) NO se siembran aquí a propósito -- quedan fuera de
-- la jerarquía de categorías comparables. classify_product() los marca
-- not_applicable y el matcher los excluye del pipeline
-- (match_status='not_applicable') sin necesitar una categoría para ellos.
-- Dice/Accessory y Mystery Pack y Promo Card ya NO tienen categoría (antes
-- sí la tenían) -- decisión de diseño explícita: no existe un canónico de
-- producto sellado razonable con el que comparar precio para ninguno de
-- los cuatro (ver §2 de la guía).
