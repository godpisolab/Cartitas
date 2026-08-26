"""Datos de configuración: qué tiendas se scrapean y con qué identidad HTTP
se hacen las peticiones (A.1) -- ver docs/estandares_organizacion_codigo.md,
sección 3. Depende solo de domain.py (para tipar StoreConfig/Platform), nada
más: añadir una tienda nueva no debería requerir abrir ningún otro módulo.
"""

from __future__ import annotations

from domain import Platform, StoreConfig

# A.1 (estándares de scraping): UA propio e identificable, con URL de
# contacto -- en vez de imitar un navegador. Se evita a propósito nombrar el
# producto "Bot"/"Crawler"/"Spider": algunos filtros anti-bot simples
# bloquean por coincidencia de esas palabras en el User-Agent, y cambiarlo a
# ciegas podría romper tiendas que hoy funcionan. Es identificable y
# rastreable (nombre + contacto) sin activar los filtros más ingenuos.
BOT_CONTACT_URL = "https://TODO-dominio-cartitas.example/bot-info"
# TODO(Ruben): sustituir por la URL de contacto real en cuanto exista un dominio.
IDENTIFIABLE_USER_AGENT = f"CartitasPriceWatch/1.0 (+{BOT_CONTACT_URL})"

# UA que imita Chrome real -- NO es el comportamiento por defecto. Solo para
# tiendas marcadas como excepción documentada (StoreConfig.ua_exception=True)
# tras confirmar que bloquean el UA identificable de arriba (ver A.1): la
# excepción es por tienda y queda anotada en su StoreConfig, no un revert
# global a mentir sobre el UA por una tienda puntual.
BROWSER_LIKE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

OUTPUT_CSV = "multi_tienda_one_piece.csv"
FAILED_STORES_CSV = "tiendas_fallidas.csv"


STORES: list[StoreConfig] = [
    StoreConfig("Cardzone", "https://cardzone.es", Platform.SHOPIFY,
                shopify_collection="one-piece-tcg"),
    StoreConfig("Pokemillon", "https://www.pokemillon.com", Platform.SHOPIFY,
                shopify_collection="one-piece"),
    StoreConfig("La Escotilla", "https://laescotillajuegos.com", Platform.SHOPIFY,
                shopify_collection="one-piece-tcg"),
    StoreConfig("Freakshow Store", "https://www.freakshowstore.com", Platform.SHOPIFY,
                shopify_collection="one-piece-tcg"),
    StoreConfig("Universe TCG", "https://www.universetcg.com", Platform.SHOPIFY,
                shopify_collection="one-piece-card-game"),

    StoreConfig("Distrito Zero", "https://distritozero.es", Platform.PRESTASHOP,
                prestashop_category_url="https://distritozero.es/categoria/one-piece-632"),
    StoreConfig("Gameria", "https://gameria.es", Platform.PRESTASHOP,
                prestashop_category_url="https://gameria.es/47-one-piece-card-game"),
    StoreConfig("Geekkaos", "https://geekkaos.com", Platform.PRESTASHOP,
                prestashop_category_url="https://geekkaos.com/26-one-piece-tcg"),
    StoreConfig("Master of Games", "https://masterofgames.es", Platform.PRESTASHOP,
                prestashop_category_url="https://masterofgames.es/es/67-one-piece"),
    StoreConfig("Kurogami", "https://kurogami.com", Platform.PRESTASHOP,
                prestashop_category_url="https://kurogami.com/es/familia/one-piece-card-game"),
    # {"label": "El Friki Bunker", ...}
    # PrestaShop confirmado, pero no localicé la URL de categoría (listado) de One
    # Piece TCG, solo páginas de producto individuales, y usa un patrón de URL
    # distinto (producto-p-3-50-XXXX) -- revisar selectores a mano antes de activar.

    StoreConfig("VersusTCG", "https://versustcg.es", Platform.WOOCOMMERCE,
                woocommerce_category_slug="op",
                woocommerce_fallback_paths=("product-category/op",)),
    StoreConfig("Arte9", "https://arte9.com", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece",
                woocommerce_fallback_paths=("categoria-producto/juegos_de_cartas/one-piece",)),
    StoreConfig("ZIAL TCG", "https://www.zialtcg.com", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece",
                woocommerce_fallback_paths=("categoria-producto/one-piece",)),

    StoreConfig("UNIK", "https://theunikshop.com", Platform.SHOPIFY,
                shopify_collection="one-piece-tcg"),

    StoreConfig("SuperCollectors", "https://www.supercollectors.es", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece",
                woocommerce_fallback_paths=("categoria-producto/boxes-packs/one-piece",)),

    # Monsters Collectors separa la categoría de One Piece por idioma (no hay una
    # categoría "one-piece" única que las englobe a ambas) -- dos fallback_paths,
    # y category_slug apunta al slug del idioma inglés porque es donde más
    # catálogo tienen; si el post-filtro descarta de más los productos en
    # japonés vía Store API, revisar si también necesitan su propio category_slug
    # separado (limitación conocida: category_slug es un único valor por tienda).
    StoreConfig("Monsters Collectors", "https://monsterscollectors.com", Platform.WOOCOMMERCE,
                woocommerce_category_slug="ingles-one-piece",
                woocommerce_fallback_paths=(
                    "categoria-monsters/one-piece/ingles-one-piece",
                    "categoria-monsters/one-piece/japones-one-piece",
                )),

    # Pendientes de confirmar plataforma antes de añadir:
    # StoreConfig("TCG Factory", "https://tcgfactory.com", Platform.PRESTASHOP,
    #             prestashop_category_url="https://tcgfactory.com/es/one-piece-card-game"),

    # El Encuentro es WooCommerce, pero filtra su TCG por una TAXONOMÍA CUSTOM de
    # Elementor (ee_tcg_line), no por la categoría estándar (product_cat) que
    # filtra nuestra Store API -- no encontramos una categoría "one-piece" nativa.
    # Usamos woocommerce_name_must_include como scoping alternativo: la tienda
    # solo vende TCG/rol/juegos de mesa (sin categorías de merchandising que
    # puedan colisionar con "one piece" en el nombre), así que el riesgo de
    # falso positivo es bajo. El fallback HTML apunta a la categoría TCG general
    # (mezclada con Magic, Pokémon...) y se filtra por nombre tras el parseo.
    StoreConfig("El Encuentro", "https://elencuentro.infrecuentes.es", Platform.WOOCOMMERCE,
                woocommerce_name_must_include=("one piece",),
                woocommerce_fallback_paths=("tienda/categoria/tcg",)),

    # inGenio BCN Games no tiene categoría propia de One Piece: todo vive bajo
    # "jc-tcg" (juegos-de-cartas/jc-tcg), que mezcla Pokémon, Magic, Star Wars
    # Unlimited, Lorcana, Flesh and Blood, Altered... Se combinan AMBOS
    # mecanismos de scoping (category_slug + name_must_include) con AND: debe
    # estar en la categoría "jc-tcg" Y el nombre debe contener "one piece".
    StoreConfig("inGenio BCN", "https://www.ingeniobcn.com", Platform.WOOCOMMERCE,
                woocommerce_category_slug="jc-tcg",
                woocommerce_name_must_include=("one piece",),
                woocommerce_fallback_paths=("juegos-de-cartas/jc-tcg",)),

    # TCG Legacy corre sobre ODOO (no Shopify/PrestaShop/WooCommerce). Verificado
    # contra el HTML real de la tienda: OdooScraper visita cada producto
    # individualmente y lee el bloque JSON-LD (schema.org/Product) para obtener
    # precio y disponibilidad reales -- ver docstring de OdooScraper.
    StoreConfig("TCG Legacy", "https://www.tcg-legacy.com", Platform.ODOO,
                odoo_category_url="https://www.tcg-legacy.com/shop/category/one-piece-10"),

    # Zentral Games también corre sobre Odoo -- mismo patrón de URL que TCG
    # Legacy (/shop/category/<slug>-<id>), reutiliza OdooScraper directamente.
    # A diferencia de TCG Legacy, esta NO se ha verificado contra el HTML real
    # (solo confirmada la plataforma vía el patrón de URL en resultados de
    # búsqueda) -- si el JSON-LD de disponibilidad no aparece o cambia de
    # forma, revisar igual que se hizo con TCG Legacy.
    StoreConfig("Zentral Games", "https://www.zentralgames.es", Platform.ODOO,
                odoo_category_url="https://www.zentralgames.es/shop/category/tcgs-one-piece-tcg-10"),

    StoreConfig("Tsuki Center", "https://tsukicenter.com", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece",
                woocommerce_fallback_paths=("categoria-producto/one-piece",)),
    StoreConfig("Júpiter Juegos", "https://jupiterjuegos.com", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece",
                woocommerce_fallback_paths=("tcg/one-piece",)),
    StoreConfig("HoloPlazaTCG", "https://holoplazatcg.com", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece-tcg",
                woocommerce_fallback_paths=("categoria/one-piece-tcg",)),
    StoreConfig("VICOL TCG", "https://vicoltcg.com", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece",
                woocommerce_fallback_paths=("product-category/one-piece",)),
    StoreConfig("EGD Games", "https://egdgames.com", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece",
                woocommerce_fallback_paths=("comprar/tcg/one-piece",)),
    StoreConfig("Ulduar", "https://www.ulduar.es", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece-tcg",
                woocommerce_fallback_paths=("categoria-producto/tcg/one-piece-tcg",)),

    # Las siguientes 4 tiendas WooCommerce están confirmadas por el patrón de
    # URL "categoria-producto/..." (inequívoco de WooCommerce), pero NO se
    # pudieron verificar con un fetch directo (bloqueado o no intentado por
    # límite de tiempo) -- si alguna da 0 productos en la primera ejecución,
    # es la primera sospechosa a revisar.
    StoreConfig("Epic Hit Store", "https://epichitstore.es", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece",
                woocommerce_fallback_paths=("categoria/otros-tcg/one-piece",)),
    StoreConfig("ManaVortex", "https://manavortex.es", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece-tcg",
                # OJO: "cateogoria" (sic) -- typo real de la URL de esta tienda, no un error nuestro.
                woocommerce_fallback_paths=("cateogoria-producto/tcg/one-piece-tcg",)),
    StoreConfig("Cartinha Brilhante", "https://www.cartinhabrilhante.com", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece-tcg",
                woocommerce_fallback_paths=("categoria-producto/tcg/one-piece-tcg",)),
    StoreConfig("El Pilar Celeste", "https://elpilarceleste.es", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece",
                woocommerce_fallback_paths=("categoria-producto/material-tcg/one-piece",)),

    # DualCollect es WooCommerce, pero filtra por un ATRIBUTO de producto
    # personalizado ("juego") vía un widget de Elementor, no por categoría
    # estándar -- mismo enfoque que El Encuentro: scoping por nombre. Catálogo
    # centrado en TCG (Magic/One Piece/Riftbound/Lorcana) + accesorios, riesgo
    # de falso positivo bajo.
    StoreConfig("DualCollect", "https://dualcollect.com", Platform.WOOCOMMERCE,
                woocommerce_name_must_include=("one piece",),
                woocommerce_fallback_paths=("tienda-tcg",)),

    StoreConfig("Ikigai Comics", "https://ikigaicomicstienda.com", Platform.SHOPIFY,
                shopify_collection="ingles-op"),
    StoreConfig("Tierra616", "https://tierra616.es", Platform.SHOPIFY,
                shopify_collection="booster-box-2"),
    StoreConfig("Saruman Games", "https://sarumangames.es", Platform.SHOPIFY,
                shopify_collection="one-piece-juego-de-cartas-coleccionables"),

    StoreConfig("Minas de Moria", "https://minasdemoria.com", Platform.PRESTASHOP,
                prestashop_category_url="https://minasdemoria.com/75-cajas"),

    # Santuario Arcano corre sobre OPENCART (nueva plataforma soportada).
    # Verificación parcial: confirmé la navegación de categorías y el detalle
    # de un producto, pero no vi el HTML real de un LISTADO de categoría con
    # varios productos -- revisar los logs de la primera ejecución. El stock
    # se marca DESCONOCIDO por defecto (ver docstring de OpenCartScraper) en
    # vez de asumir DISPONIBLE a ciegas.
    StoreConfig("Santuario Arcano", "https://santuarioarcano.com", Platform.OPENCART,
                opencart_category_url="https://santuarioarcano.com/index.php?route=product/category&path=77_96"),

    # =======================================================================
    # Tiendas añadidas 2026-08-25 tras auditoría de tiendas.md (17 confirmadas
    # con al menos una petición HTTP real contra la categoría/colección de One
    # Piece -- ver el bloque "Pendientes" más abajo para las descartadas/no
    # viables encontradas en la misma auditoría).
    # =======================================================================

    StoreConfig("FreakCorp", "https://freakcorp.com", Platform.SHOPIFY,
                shopify_collection="one-piece-card-game"),
    StoreConfig("Cartas Mencey", "https://cartasmencey.es", Platform.SHOPIFY,
                shopify_collection="one-piece-tcg"),
    StoreConfig("Kame House Cards", "https://kamehousecards.com", Platform.SHOPIFY,
                shopify_collection="one-piece-card-game"),
    StoreConfig("Shark Games Center", "https://sharkgamescenter.es", Platform.SHOPIFY,
                shopify_collection="one-piece"),
    StoreConfig("Golden Pulls", "https://goldenpullscards.com", Platform.SHOPIFY,
                shopify_collection="one-piece"),

    # Darkvault divide su catálogo de One Piece en DOS colecciones de Shopify:
    # "booster-box-one-piece" (usada -- 3 productos verificados, todos
    # agotados, coincide con la pista) y "sobre-one-piece" (verificada también
    # en vivo pero con 0 productos ahora mismo). StoreConfig solo admite una
    # colección por tienda -- si "sobre-one-piece" se puebla en el futuro con
    # sobres sueltos, quedará fuera hasta revisar esto a mano.
    StoreConfig("Darkvault", "https://darkvault.es", Platform.SHOPIFY,
                shopify_collection="booster-box-one-piece"),

    StoreConfig("Gladius Games", "https://gladiusgames.net", Platform.PRESTASHOP,
                prestashop_category_url="https://gladiusgames.net/es/34-one-piece"),
    StoreConfig("JretroGame", "https://jretrogame.com", Platform.PRESTASHOP,
                prestashop_category_url="https://jretrogame.com/es/150-one-piece"),
    StoreConfig("War Lotus", "https://warlotus.com", Platform.PRESTASHOP,
                prestashop_category_url="https://warlotus.com/43-one-piece-tcg"),

    # Puerto Fantasy: la categoría se localizó y verificó SOLO contra una copia
    # archivada (Wayback Machine, 2026-04-18) -- la web en vivo bloquea todo
    # acceso automatizado (curl/urllib/WebFetch reciben 403/404 incluso en
    # robots.txt, patrón de WAF anti-bot). Si al ejecutar el scraper esta
    # tienda da 0 productos, es la sospechosa número uno: el WAF puede seguir
    # bloqueando en producción igual que bloqueó la verificación.
    StoreConfig("Puerto Fantasy", "https://www.puertofantasy.es", Platform.PRESTASHOP,
                prestashop_category_url="https://www.puertofantasy.es/252-one-piece-card-game"),

    StoreConfig("CardCrack", "https://cardcrack.com", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece",
                woocommerce_fallback_paths=("categoria/one-piece",)),
    StoreConfig("TopDeck", "https://topdeck.es", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece-card-game",
                woocommerce_fallback_paths=("comprar-cartas-coleccionables/one-piece-card-game",)),

    # OJO: la ruta de categoría real es "/cat/one-piece/" (permalink corto y
    # propio de esta tienda, no el prefijo "categoria-producto/" habitual de
    # WooCommerce) -- confirmado, no es un error nuestro.
    StoreConfig("La Colmena TCG", "https://lacolmenatcg.com", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece",
                woocommerce_fallback_paths=("cat/one-piece",)),

    StoreConfig("Mulligan", "https://shop.tiendamulligan.com", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece",
                woocommerce_fallback_paths=("categoria-producto/one-piece",)),

    # Galarian Cards: WooCommerce 10.8.1 confirmado por meta generator. La
    # categoría "one-piece" existe (count=2) pero AHORA MISMO ambos productos
    # están agotados -- la Store API sin forzar stock_status devuelve [], no
    # es un fallo de scoping.
    StoreConfig("Galarian Cards", "https://galariancards.es", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece",
                woocommerce_fallback_paths=("categoria-producto/one-piece",)),

    # Monarka Store: confirmado WordPress + WooCommerce pese a sus permalinks
    # personalizados (product_cat reescrito a "/c/", product a "/p/"). La
    # Store API responde con normalidad usando el slug real de categoría
    # ("one-piece"), verificado en vivo con productos e is_in_stock incluidos.
    StoreConfig("Monarka Store", "https://monarkatcg.store", Platform.WOOCOMMERCE,
                woocommerce_category_slug="one-piece",
                woocommerce_fallback_paths=("c/one-piece",)),

    # Estalia Córdoba: la pista original sugería WooCommerce, pero en realidad
    # corre sobre ODOO -- confirmado por el string "Odoo" en el HTML y por el
    # patrón de URL /shop/category/<slug>-<id>, idéntico a TCG Legacy/Zentral
    # Games. Reutiliza OdooScraper directamente.
    StoreConfig("Estalia Córdoba", "https://www.estaliacordoba.com", Platform.ODOO,
                odoo_category_url="https://www.estaliacordoba.com/shop/category/tcgs-one-piece-2567"),

    # NIKOCHAN ARENA: CMS a medida (agencia Tufuturaweb), sin ninguna de las 5
    # plataformas soportadas -- pero con JSON-LD schema.org de tipo Product
    # limpio y estándar en cada página de producto (offers.availability
    # directo). Categoría de listado verificada con petición real (200 OK,
    # 15 productos, incluye Booster OP-17), sin paginación real (?page=2
    # devuelve el mismo contenido que la página 1 -- confirmado comparando
    # los enlaces de producto de ambas peticiones).
    StoreConfig("NIKOCHAN ARENA", "https://nikochancomics.net", Platform.GENERIC_JSONLD,
                jsonld_listing_urls=("https://nikochancomics.net/categoria/one-piece-tcg",),
                jsonld_product_link_selector="a[href*='/producto/']"),

    # ISEKAI: plataforma SaaS ShopinCloud/LiveCommerce (meta generator
    # "livecommerce.es"), tampoco es ninguna de las 5 soportadas. El HTML de
    # listado es server-side (confirmado: 21 tarjetas article.product-
    # miniature = 21 enlaces /producto/ únicos, sin ruido), pero precio y
    # stock se renderizan por JavaScript -- de ahí depender del JSON-LD de la
    # página de producto. OJO: en esta tienda el "@type" viene en minúscula
    # ("product") y la oferta real está anidada un nivel más abajo dentro de
    # un AggregateOffer (offers.offers.price/availability, no offers.price/
    # availability directo) -- GenericJsonLdScraper ya contempla ambos casos.
    # Paginación real confirmada vía a[rel='next'] + enlaces numerados.
    StoreConfig("ISEKAI", "https://isekai-alcorcon.es", Platform.GENERIC_JSONLD,
                jsonld_listing_urls=("https://isekai-alcorcon.es/es/c/one-piece/90",),
                jsonld_product_link_selector="a[href*='/producto/']"),

    # Comic Stores / Freak Point: corre sobre GESLIB/WebLib (Grupo Trevenque,
    # app PHP a medida), tampoco es ninguna plataforma soportada. No tiene
    # categoría de listado dedicada a One Piece -- se descubre el catálogo
    # vía su buscador interno. La query "one piece card game" (verificada en
    # vivo) da resultados mucho más limpios que "one piece" a secas (que
    # mezcla tazas/llaveros/figuras): de 28 resultados, 27 son productos TCG
    # reales (boosters, starter decks, double packs, illustration box,
    # premium card collections); el único colado es una figura Ichibansho
    # que el propio buscador de la tienda etiqueta también como "one piece
    # card game" -- ruido conocido y aceptado, classify_product() la marcará
    # como OTROS. El JSON-LD de esta tienda NO incluye disponibilidad -- se
    # lee de `<span class="availability">InStock|OutOfStock</span>`
    # (verificado en un booster OP-19 agotado), que GenericJsonLdScraper ya
    # usa como fallback cuando falta en el JSON-LD.
    StoreConfig("Comic Stores / Freak Point", "https://comicstores.es", Platform.GENERIC_JSONLD,
                jsonld_listing_urls=(
                    "https://comicstores.es/busqueda/listaLibros.php?tipoBus=full&palabrasBusqueda=one+piece+card+game",
                ),
                jsonld_product_link_selector="a[href*='/producto/']"),

    # Pendientes -- necesitan más trabajo antes de poder añadirse:
    #
    # VTini Games (vtinigames.com) -- corre sobre HOSTINGER WEBSITE BUILDER
    # (constructor tipo Wix/Squarespace, sin categorías reales visibles, sin
    # API pública conocida). Además el enlace que compartiste es de un
    # producto de POKÉMON, no de One Piece -- revisar si tienen catálogo de
    # One Piece antes de intentar nada.
    #
    # Frikibunker (frikibunker.es) -- esquema de URL propietario
    # (idsite=/criterio_id=/filtros=clave;valor@clave;valor) que no coincide
    # con ninguna plataforma reconocida. Necesitaría investigación aparte
    # para identificar qué motor de tienda usan antes de poder scrapearla.
    #
    # Totem Comics (totemcomics.es) -- WooCommerce confirmado (categoria-
    # producto/...), pero solo localizamos categorías por marca (ej. "bandai"),
    # no una específica de One Piece. Pendiente de encontrar el slug correcto
    # (podría necesitar woocommerce_name_must_include como El Encuentro).
    #
    # Metropolis Center (metropolis-center.com) -- bloquea el acceso
    # automatizado (robots.txt) incluso para verificar la plataforma. URL con
    # patrón "/es/catalogo/...?q=Disponibilidad-Disponible" atípico, no
    # identificado con certeza.
    #
    # -- Auditoría de tiendas.md (2026-08-25) -- descartadas/pendientes --
    #
    # AvalonBurgos (avalonburgos.es) -- PrestaShop confirmado (menú de
    # categorías real). El reto anti-bot (HTTP 202 + meta-refresh) YA NO es
    # un bloqueo: request_with_retries lo resuelve solo (ver
    # _solve_js_cookie_challenge en http_client.py, verificado 2026-08-25 --
    # la cookie es estática, no hace falta navegador real ni curl_cffi).
    # Sigue sin poder activarse por el motivo ORIGINAL, ahora confirmado con
    # la tienda ya accesible: no existe ninguna categoría de One Piece (0
    # coincidencias de "piece" en las 673 URLs del menú completo) -- los
    # productos de la pista original deben vivir sueltos en alguna categoría
    # genérica de TCG/juegos de mesa. PrestaShopScraper no tiene un
    # equivalente a woocommerce_name_must_include para acotar por nombre
    # dentro de una categoría amplia -- habría que añadir ese mecanismo antes
    # de poder activar esta tienda con confianza.
    #
    # DRAGONCT (dragonct.com) -- WooCommerce confirmado, pero NO tiene
    # catálogo real de cartas de One Piece: la única categoría relacionada
    # son entradas de evento/torneo ("PREINSCRIPCIONES TORNEOS"), sin ningún
    # booster/sobre/mazo a la venta. Nada que monitorizar por ahora.
    #
    # Bettoy Coleccionistas (bettoy.es) -- WooCommerce confirmado, y existe
    # literalmente una categoría "CARTAS ONE PIECE" (slug "cartas-one-piece"),
    # pero sus 4 productos son accesorios genéricos (fundas, archivador,
    # protectores) cross-etiquetados también en Pokémon y Magic -- ni una
    # carta real de One Piece. Es una trampa de nombre, no un bug de scoping:
    # activarla solo traería accesorios compartidos.
    #
    # Hotei Games (hoteigames.es) -- WordPress con la tienda implementada vía
    # Ecwid embebido (widget que renderiza el catálogo 100% en el cliente con
    # JavaScript; el HTML servido por el servidor no contiene productos). La
    # API pública de Ecwid devuelve 403 sin token, y no hay ninguno expuesto
    # en la página. No viable sin un navegador headless (Playwright/Selenium)
    # o credenciales de la API de Ecwid que la tienda no ofrece.
    #
    # (NIKOCHAN ARENA, ISEKAI y Comic Stores/Freak Point, también detectadas
    # en esta auditoría como CMS a medida sin plataforma soportada, SÍ se
    # añadieron -- ver Platform.GENERIC_JSONLD / GenericJsonLdScraper más
    # arriba en esta misma lista.)
]
