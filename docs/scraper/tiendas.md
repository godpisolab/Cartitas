# Tiendas TCG España — Documento maestro (Pokémon + One Piece)

Recuento completo: tiendas ya integradas en `scraper_unificado.py` antes de esta sesión + tiendas nuevas verificadas (plataforma + stock real) a partir del dataset de TCG Map Spain filtrado a Pokémon + One Piece.

**Total: 53 tiendas** (36 ya integradas + 17 nuevas confirmadas)

---

## 0. Ya integradas en el scraper (36)

Según el historial del proyecto. No se ha vuelto a verificar plataforma/stock en esta sesión — ya estaban confirmadas y en uso.

| # | Tienda | Plataforma |
|---|---|---|
| 1 | Distrito Zero | PrestaShop |
| 2 | Cardzone | Shopify |
| 3 | Pokemillon | Shopify |
| 4 | La Escotilla | Shopify |
| 5 | Freakshow Store (Girona) | Shopify |
| 6 | Universe TCG (Mollet del Vallès) | Shopify |
| 7 | Gameria (Barcelona) | PrestaShop |
| 8 | Geekkaos (Mataró) | PrestaShop |
| 9 | Master of Games (Bilbao) | PrestaShop |
| 10 | Kurogami (Alicante) | Custom (agencia Perosio) |
| 11 | El Friki Bunker (Valencia) | PrestaShop |
| 12 | VersusTCG | WooCommerce |
| 13 | Arte9 | WooCommerce |
| 14 | ZIAL TCG | WooCommerce |
| 15 | UNIK | Shopify |
| 16 | SuperCollectors | WooCommerce |
| 17 | Monsters Collectors | WooCommerce |
| 18 | El Encuentro | WooCommerce (scoping por taxonomía Elementor) |
| 19 | TCG Legacy | Odoo |
| 20 | inGenio BCN Games | WooCommerce |
| 21 | Zentral Games | Odoo |
| 22 | Tsuki Center | WooCommerce |
| 23 | Júpiter Juegos | WooCommerce |
| 24 | HoloPlazaTCG | WooCommerce |
| 25 | VICOL TCG | WooCommerce |
| 26 | EGD Games | WooCommerce |
| 27 | Ulduar | WooCommerce |
| 28 | Epic Hit Store | WooCommerce |
| 29 | ManaVortex | WooCommerce |
| 30 | Cartinha Brilhante | WooCommerce |
| 31 | El Pilar Celeste | WooCommerce |
| 32 | DualCollect | WooCommerce |
| 33 | Ikigai Comics | Shopify |
| 34 | Tierra616 | Shopify |
| 35 | Saruman Games | Shopify |
| 36 | Minas de Moria / Santuario Arcano* | PrestaShop / OpenCart |

*Nota: el registro del proyecto lista 35 tiendas tras el penúltimo lote y 36 tras añadir Santuario Arcano (OpenCart, 5ª plataforma soportada); Minas de Moria se añadió en el lote anterior. Si el recuento exacto de esta tabla difiere en 1 respecto al total real del script, conviene contrastarlo directamente contra `scraper_unificado.py`.

---

## 1. Nuevas — listas para integrar ya (16)

Plataforma soportada, stock confirmado en vivo esta sesión.

| Tienda | URL | Plataforma | Evidencia de stock One Piece |
|---|---|---|---|
| CardCrack | https://cardcrack.com | WooCommerce | Categoría dedicada `/categoria/one-piece/` |
| TopDeck | https://topdeck.es | WooCommerce | Illustration Box IB-07/08 en stock |
| La Colmena TCG | https://lacolmenatcg.com | WooCommerce | OP-08, OP-09, OP-10 Booster Box en venta |
| AvalonBurgos | https://avalonburgos.es | PrestaShop | Starter Decks, Premium Card Collection Vol.3 |
| Estalia Córdoba | https://www.estaliacordoba.com | WooCommerce (probable) | Categoría `/shop/category/tcgs-one-piece-2567` |
| DRAGONCT | https://dragonct.com | WooCommerce | Entradas presentación OP-15, producto TCG activo |
| Bettoy Coleccionistas | https://bettoy.es | WooCommerce | Categoría "CARTAS ONE PIECE" dedicada |
| Mulligan | https://shop.tiendamulligan.com | WooCommerce | Premium Card Collection Vol.3 y más |
| Gladius Games | https://gladiusgames.net | PrestaShop | Devil Fruits Collection + torneos One Piece |
| JretroGame | https://jretrogame.com | PrestaShop (confirmado explícito) | Especialistas en singles/bulk |
| FreakCorp | https://freakcorp.com | Shopify | Starter Deck ST-31, Premium Card Collection Vol.6 |
| Puerto Fantasy | https://www.puertofantasy.es | PrestaShop | 8+ productos activos |
| Cartas Mencey | https://cartasmencey.es | Shopify | Tienda TCG en Tenerife |
| Galarian Cards | https://galariancards.es | WooCommerce (confirmado) | Categoría `/categoria-producto/one-piece/` |
| War Lotus | https://warlotus.com | PrestaShop | Categoría dedicada `/43-one-piece-tcg` |
| Kame House Cards | https://kamehousecards.com | Shopify | OP-15 Booster Box y Starter Decks |

---

## 2. Nueva — requiere parser nuevo, pero mejor catálogo (1)

| Tienda | URL | Plataforma | Por qué vale la pena |
|---|---|---|---|
| Comic Stores / Freak Point | https://comicstores.es | Custom (GESLIB/WebLib, Grupo Trevenque, RFID) | 51 productos One Piece activos, 7 tiendas físicas bajo un solo dominio |

---

## 3. Buen stock, plataforma no soportada (2) — no cuentan en las 53

| Tienda | URL | Plataforma | Nota |
|---|---|---|---|
| Hotei Games | https://www.hoteigames.es | IONOS MyWebsite/Store | 6 Starter Decks ST-31 a ST-36 completos, en inglés |
| NIKOCHAN ARENA | https://nikochancomics.net | Custom (agencia Tufuturaweb) | Booster OP-17 disponible; torneos semanales |

---

## 4. Plataforma soportada, stock bajo ahora mismo (3) — no cuentan en las 53

| Tienda | URL | Plataforma | Nota |
|---|---|---|---|
| Shark Games Center | https://sharkgamescenter.es | Shopify | Todo lo visible agotado |
| Golden Pulls | https://goldenpullscards.com | Shopify | 1 Booster Box OP-11 disponible (€330) |
| Darkvault | https://darkvault.es | Shopify | 3 productos, todos agotados |

---

## 5. Plataforma sin identificar (2) — no cuentan en las 53

| Tienda | URL | Nota |
|---|---|---|
| Monarka Store | https://monarkatcg.store | Buen stock (LT-01 Starter Deck Pack activo) |
| ISEKAI | https://isekai-alcorcon.es | Muy activa: Booster Display OP-16, Starter Decks ST-31/36 |

---

## 6. Descartadas (11)

| Tienda | Motivo |
|---|---|
| Alpaca Chinchona | Sin tienda online propia, solo Cardmarket |
| Nekomics | Librería de cómics/manga, no vende TCG realmente |
| Imperio Friki | Ya no tiene sección One Piece |
| WiseCollectorTCG | Sumup (no soportada); singles gradeados, no sellado |
| Torredragón | Categoría One Piece no verificable directamente |
| Latveria Store | TCG secundario, sin sección One Piece visible |
| Eldritch Gate | Sin tienda propia, solo Linktree/Cardmarket |
| Hamelin Games | Sin tienda online ("Próximamente") |
| Shark Games Antequera | Web en construcción |
| ARCO TCG | WooCommerce válido, pero sin sellado de One Piece aún |
| WASABI MANGA | Dominio comprometido, sirve spam de casinos |

---

## 7. No verificadas en esta sesión (12)

El Turno Extra, Percalandia, ARCANE HALL, The Booster Box, La Cripta Blanes, G3TCG, Ruta151, Paladin Store, Tienda de Juegos Padis, Laluna, Norkkiu's, Shinigami Cómics.

---

## Resumen ejecutivo

| Categoría | Cantidad |
|---|---|
| Ya integradas en el scraper | 36 |
| Nuevas listas para integrar ya | 16 |
| Nueva que requiere parser (Comic Stores) | 1 |
| **Total combinado (activas o accionables)** | **53** |
| Buen stock, plataforma no soportada | 2 |
| Plataforma soportada, stock bajo ahora mismo | 3 |
| Plataforma sin identificar | 2 |
| Descartadas | 11 |
| No verificadas | 12 |
| **Total de tiendas mapeadas/evaluadas** | **60** |