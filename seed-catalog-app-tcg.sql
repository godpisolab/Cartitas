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
-- 'single-card' (implementacion-auto-confirmado-setcode.md 1.4): una carta
-- individual (promo o no) no es "producto sellado" en el mismo sentido que
-- una caja/sobre -- tercer padre junto a Sellado/Accesorios.
INSERT INTO category (name, slug) VALUES
    ('Sellado', 'sellado'),
    ('Accesorios', 'accesorios'),
    ('Single Card', 'single-card')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO category (parent_category_id, name, slug)
SELECT (SELECT id FROM category WHERE slug = 'sellado'), name, slug
FROM (VALUES
    ('Booster Box', 'booster-box'),
    ('Booster Pack', 'booster-pack'),
    ('Booster Case', 'booster-case'),
    ('Starter Deck', 'starter-deck'),
    ('Illustration Box', 'illustration-box'),
    ('Premium Collection', 'premium-collection'),
    ('Double Pack', 'double-pack'),
    ('Mystery Pack', 'mystery-pack'),
    ('Devil Fruits Collection', 'devil-fruits-collection'),
    ('Learn Deck', 'learn-deck')
) AS t(name, slug)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO category (parent_category_id, name, slug)
SELECT (SELECT id FROM category WHERE slug = 'accesorios'), name, slug
FROM (VALUES
    ('Playmat', 'playmat'),
    ('Dice / Accessory', 'dice-accessory')
) AS t(name, slug)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO category (parent_category_id, name, slug)
SELECT (SELECT id FROM category WHERE slug = 'single-card'), name, slug
FROM (VALUES
    ('Promo Card', 'promo-card')
) AS t(name, slug)
ON CONFLICT (slug) DO NOTHING;

-- Reaplicable sobre una BBDD ya sembrada con la jerarquía vieja (promo-card
-- como hija directa de Sellado, antes de 1.4): el INSERT de arriba no mueve
-- una fila ya existente, así que se corrige aquí explícitamente.
UPDATE category
SET parent_category_id = (SELECT id FROM category WHERE slug = 'single-card')
WHERE slug = 'promo-card';

-- OJO: 'Lote de cartas' y 'Otros' (D.3) NO se siembran aquí a propósito --
-- quedan fuera de la jerarquía de categorías comparables. classify_product()
-- los marca LOTE_CARTAS/OTROS y el matcher los excluye del pipeline
-- (match_status='not_applicable') sin necesitar una categoría para ellos.
