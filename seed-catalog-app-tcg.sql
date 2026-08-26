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

-- Jerarquía real (D.2), no los 4-5 de ejemplo del diseño inicial.
INSERT INTO category (name, slug) VALUES
    ('Sellado', 'sellado'),
    ('Accesorios', 'accesorios')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO category (parent_category_id, name, slug)
SELECT (SELECT id FROM category WHERE slug = 'sellado'), name, slug
FROM (VALUES
    ('Booster Box', 'booster-box'),
    ('Booster Pack', 'booster-pack'),
    ('Starter Deck', 'starter-deck'),
    ('Illustration Box', 'illustration-box'),
    ('Premium Collection', 'premium-collection'),
    ('Double Pack', 'double-pack'),
    ('Mystery Pack', 'mystery-pack'),
    ('Devil Fruits Collection', 'devil-fruits-collection'),
    ('Learn Deck', 'learn-deck'),
    ('Promo Card', 'promo-card')
) AS t(name, slug)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO category (parent_category_id, name, slug)
SELECT (SELECT id FROM category WHERE slug = 'accesorios'), name, slug
FROM (VALUES
    ('Playmat', 'playmat'),
    ('Dice / Accessory', 'dice-accessory')
) AS t(name, slug)
ON CONFLICT (slug) DO NOTHING;

-- OJO: 'Lote de cartas' y 'Otros' (D.3) NO se siembran aquí a propósito --
-- quedan fuera de la jerarquía de categorías comparables. classify_product()
-- los marca LOTE_CARTAS/OTROS y el matcher los excluye del pipeline
-- (match_status='not_applicable') sin necesitar una categoría para ellos.
