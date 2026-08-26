"""
Scraper unificado multi-tienda para TCG (One Piece Card Game).

Soporta tres plataformas -- Shopify, PrestaShop y WooCommerce -- a través de una
interfaz común (BaseStoreScraper) para que el resto del programa (dispatcher,
CSV, resumen) no tenga que saber nada de las diferencias entre ellas.

DISEÑO (por qué está organizado así, no como lista de parches):

1. StoreConfig es un dataclass que SE VALIDA A SÍ MISMO al crearse (__post_init__).
   Una tienda WooCommerce sin category_slug NI category_paths lanza un ValueError
   al arrancar el script, no un bug silencioso que se descubre semanas después
   viendo que una tienda tarda demasiado o mezcla productos de otras categorías.
   Esta es la causa estructural de los dos bugs de scoping que tuvimos con
   Arte9 y ZIAL: no había nada que impidiera configurar una tienda sin acotar
   su catálogo. Ahora sí lo hay.

2. Product es un dataclass explícito (no un dict ensamblado a mano en cada
   función). El campo `variant` existe desde el diseño para TODAS las
   plataformas, no como parche posterior sólo en Shopify -- así que si
   PrestaShop o WooCommerce alguna vez necesitan modelar variantes (idioma,
   grading...), el hueco ya está.

3. classify_product() intenta primero clasificar por el nombre del producto y,
   si no encuentra idioma, cae al título de la variante. Esto está integrado
   en la función de clasificación en sí (única fuente de verdad), no repetido
   en cada scraper.

4. request_with_retries() centraliza reintentos con backoff para TODAS las
   peticiones HTTP de las tres plataformas -- antes cada función manejaba sus
   propios try/except de forma distinta y sin reintentos, así que un timeout
   puntual de red tiraba la tienda entera a la papelera de "tiendas_fallidas".

5. StoreLogger junta "imprimir por consola" y "marcar actividad para el
   timeout por inactividad" en una sola llamada (`logger.log(...)`), evitando
   el patrón repetido `print(...); mark_activity(...)` que había en cada
   punto del código.

Requiere: pip install cloudscraper requests beautifulsoup4 --break-system-packages
"""

from __future__ import annotations

import sys

# Alias necesario para que scrapers/*.py puedan hacer "from base_script import ...":
# al ejecutar este archivo directamente (`python base_script.py`) su módulo real se
# llama "__main__", no "base_script". Sin este alias, el import circular haría que
# Python cargara una SEGUNDA copia de este archivo bajo el nombre "base_script"
# (duplicando StoreConfig, Product, etc. y rompiendo el import circular con
# scrapers/). Debe ir antes de cualquier import que acabe cargando scrapers/.
sys.modules.setdefault("base_script", sys.modules[__name__])

import csv
import email.utils
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional
from urllib.robotparser import RobotFileParser

import cloudscraper
import pybreaker
import requests

import store_state


# ===========================================================================
# Constantes de comportamiento
# ===========================================================================

# Tiempo máximo (segundos) SIN NINGUNA actividad (petición enviada o recibida)
# antes de dar una tienda por caída. No es un límite de duración total: mientras
# la tienda siga respondiendo (aunque tenga muchas páginas y tarde), no se corta.
STORE_TIMEOUT = 90
STORE_POLL_INTERVAL = 5  # cada cuánto se comprueba si ha habido actividad reciente

DEFAULT_DELAY = 1.0        # pausa entre páginas de una misma tienda
DEFAULT_TIMEOUT = 30       # timeout de socket por petición HTTP (verificado 2026-08-25:
                           # Arte9 tarda ~20-22s en responder de forma habitual -- no es un
                           # fallo puntual, es la latencia normal de esa tienda -- 20s se
                           # quedaba corto y provocaba fallos de red intermitentes reales)
MAX_RETRIES = 3            # reintentos por petición HTTP ante fallos transitorios
RETRY_BACKOFF_BASE = 1.6   # base del backoff exponencial

# Circuit breaker por tienda (usado por query_store, la consulta puntual "a
# dedo" pensada para un front -- ver StoreQueryResult). Tras BREAKER_FAIL_MAX
# fallos SEGUIDOS de la misma tienda, se deja de intentar scrapearla durante
# BREAKER_RESET_TIMEOUT segundos y se devuelve el fallo al instante en vez de
# esperar el timeout completo (hasta STORE_TIMEOUT) en cada consulta -- útil
# cuando una tienda concreta está caída/bloqueada (ej. WAF) y el usuario del
# front la sigue seleccionando mientras tanto. NO afecta a run_all_stores
# (el batch): cada tienda se scrapea una sola vez por ejecución ahí, así que
# el circuito no aporta nada y solo sumaría complejidad a un camino ya
# probado.
BREAKER_FAIL_MAX = 3
BREAKER_RESET_TIMEOUT = 300

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

# A.3: tope máximo de espera si un servidor pide un Retry-After absurdo (mal
# configurado u hostil) -- no se bloquea el proceso completo esperando, se
# corta ahí y se trata como fallo normal de esa tienda en este ciclo.
MAX_RETRY_AFTER_WAIT = 300  # 5 minutos

# A.2: cada cuánto se refresca la caché de robots.txt por tienda -- cambia
# poco, comprobarlo una vez por semana es razonable (no en cada scrape).
ROBOTS_CACHE_TTL_SECONDS = 7 * 24 * 3600

# A.3: al tercer fallo seguido ENTRE EJECUCIONES (ver store_state.py), se
# fija un backoff que también respeta el siguiente ciclo de scraping, no
# solo los reintentos de la ejecución actual.
STORE_BACKOFF_FAILURE_THRESHOLD = 3
STORE_BACKOFF_DEFAULT_SECONDS = 1800  # 30 min de cooldown si el servidor no dio su propio Retry-After

OUTPUT_CSV = "multi_tienda_one_piece.csv"
FAILED_STORES_CSV = "tiendas_fallidas.csv"


# ===========================================================================
# Configuración de tiendas
# ===========================================================================

class Platform(str, Enum):
    """Las plataformas de tienda que el dispatcher sabe scrapear -- cada una
    mapea a una clase en scrapers/ (ver SCRAPER_CLASSES)."""

    SHOPIFY = "shopify"
    PRESTASHOP = "prestashop"
    WOOCOMMERCE = "woocommerce"
    ODOO = "odoo"
    OPENCART = "opencart"
    GENERIC_JSONLD = "generic_jsonld"


@dataclass
class StoreConfig:
    """Configuración de una tienda. Se autovalida en __post_init__: si falta
    un campo imprescindible para su plataforma, falla en el arranque del
    script con un mensaje claro, en vez de scrapear mal (o de más) en
    silencio. Esto es intencional -- es la corrección estructural de los
    bugs de scoping de Arte9/ZIAL: antes nada impedía omitir el filtro de
    categoría; ahora es un error de configuración explícito."""

    label: str
    domain: str
    platform: Platform

    # Shopify: slug de la colección (lo que va después de /collections/)
    shopify_collection: Optional[str] = None

    # PrestaShop: URL completa de la categoría (listado de productos)
    prestashop_category_url: Optional[str] = None

    # WooCommerce:
    #   woocommerce_category_slug -> slug DEL TÉRMINO de categoría (no la ruta
    #     completa). Se manda como filtro de servidor a la Store API y ADEMÁS
    #     se usa como post-filtro local (ver WooCommerceScraper._product_in_scope)
    #     por si el servidor lo ignora. Tiendas 100% mono-producto (todo su
    #     catálogo es relevante) pueden omitirlo.
    #   woocommerce_name_must_include -> mecanismo de scoping ALTERNATIVO para
    #     tiendas que NO exponen una categoría estándar de WooCommerce fiable
    #     (p.ej. El Encuentro, que filtra por una taxonomía custom de Elementor
    #     en vez de la categoría nativa). Todas estas subcadenas deben aparecer
    #     en el nombre del producto (case-insensitive) para considerarlo en
    #     scope. MENOS RIGUROSO que category_slug -- solo usar cuando no hay
    #     alternativa, y solo en tiendas donde el riesgo de falso positivo sea
    #     bajo (catálogo enfocado, sin categorías de merchandising que
    #     compartan palabras con el nombre del juego).
    #   woocommerce_fallback_paths -> rutas de categoría relativas (sin dominio,
    #     sin barra inicial) usadas si la Store API no está disponible.
    woocommerce_category_slug: Optional[str] = None
    woocommerce_name_must_include: tuple[str, ...] = field(default_factory=tuple)
    woocommerce_fallback_paths: tuple[str, ...] = field(default_factory=tuple)

    # Odoo: URL completa de la categoría (listado de productos), estilo
    # /shop/category/<slug>-<id>
    odoo_category_url: Optional[str] = None

    # OpenCart: URL completa de la categoría (listado de productos), estilo
    # index.php?route=product/category&path=<id> o <id_padre>_<id_hijo>
    opencart_category_url: Optional[str] = None

    # Genérico JSON-LD: para tiendas con CMS a medida (ninguna de las 5
    # plataformas de arriba) que exponen JSON-LD schema.org de tipo Product en
    # sus páginas de producto. GenericJsonLdScraper recolecta URLs de producto
    # visitando jsonld_listing_urls (una o varias -- puede ser una categoría o
    # una URL de búsqueda interna) y siguiendo TODOS los enlaces de paginación
    # reales que encuentre (no construye URLs de página a mano: cada tienda
    # pagina de forma distinta). jsonld_product_link_selector es el selector
    # CSS que localiza los <a href> a páginas de producto dentro de esas
    # páginas de listado.
    jsonld_listing_urls: tuple[str, ...] = field(default_factory=tuple)
    jsonld_product_link_selector: Optional[str] = None

    # A.1: True SOLO para tiendas que confirmadamente bloquean
    # IDENTIFIABLE_USER_AGENT tras probarlo -- excepción documentada por
    # tienda (anotar el motivo al lado, como con woocommerce_name_must_include),
    # nunca el comportamiento por defecto.
    ua_exception: bool = False

    def __post_init__(self):
        """Normaliza domain y valida que la config trae lo imprescindible
        para su plataforma (ver docstring de la clase)."""
        self.domain = self.domain.rstrip("/")

        if self.platform == Platform.SHOPIFY and not self.shopify_collection:
            raise ValueError(f"{self.label}: falta 'shopify_collection'")

        if self.platform == Platform.PRESTASHOP and not self.prestashop_category_url:
            raise ValueError(f"{self.label}: falta 'prestashop_category_url'")

        if self.platform == Platform.ODOO and not self.odoo_category_url:
            raise ValueError(f"{self.label}: falta 'odoo_category_url'")

        if self.platform == Platform.OPENCART and not self.opencart_category_url:
            raise ValueError(f"{self.label}: falta 'opencart_category_url'")

        if self.platform == Platform.GENERIC_JSONLD:
            if not self.jsonld_listing_urls:
                raise ValueError(f"{self.label}: falta 'jsonld_listing_urls'")
            if not self.jsonld_product_link_selector:
                raise ValueError(f"{self.label}: falta 'jsonld_product_link_selector'")

        if self.platform == Platform.WOOCOMMERCE:
            has_scope = bool(self.woocommerce_category_slug or self.woocommerce_name_must_include)
            has_fetch_path = bool(self.woocommerce_category_slug or self.woocommerce_fallback_paths)

            if not has_scope:
                raise ValueError(
                    f"{self.label}: tienda WooCommerce sin 'woocommerce_category_slug' NI "
                    f"'woocommerce_name_must_include' -- sin uno de los dos no hay forma de "
                    f"acotar el catálogo, y el scraper acabaría trayéndose todo lo que vende "
                    f"la tienda (el bug original de Arte9/ZIAL)."
                )
            if not has_fetch_path:
                raise ValueError(
                    f"{self.label}: tienda WooCommerce sin 'woocommerce_category_slug' NI "
                    f"'woocommerce_fallback_paths' -- sin al menos uno de los dos no hay "
                    f"ruta de fallback HTML si la Store API falla. Si el scoping es solo por "
                    f"'woocommerce_name_must_include', añade igualmente una fallback_path "
                    f"(aunque sea una categoría amplia) para que el fallback HTML tenga "
                    f"algo que recorrer y filtrar por nombre."
                )


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
    # _solve_js_cookie_challenge más abajo, verificado 2026-08-25 -- la
    # cookie es estática, no hace falta navegador real ni curl_cffi). Sigue
    # sin poder activarse por el motivo ORIGINAL, ahora confirmado con la
    # tienda ya accesible: no existe ninguna categoría de One Piece (0
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


# ===========================================================================
# Modelo de datos
# ===========================================================================

CSV_FIELDNAMES = [
    "store", "platform", "id_product", "name", "variant", "product_type",
    "main_set", "set_code", "language", "price", "stock_status", "url", "sku",
    "image_url",
]


@dataclass
class Product:
    """Fila normalizada, idéntica para las tres plataformas."""
    store: str
    platform: str
    id_product: Optional[str]
    name: Optional[str]
    variant: Optional[str]           # título de variante (idioma, grading...); None si N/A
    product_type: str
    main_set: Optional[str]
    set_code: Optional[str]
    language: Optional[str]
    price: Optional[float]
    stock_status: str
    url: Optional[str]
    sku: Optional[str]
    image_url: Optional[str]

    def to_dict(self) -> dict:
        """Convierte a dict plano -- lo que espera csv.DictWriter en write_products_csv."""
        return asdict(self)


# ===========================================================================
# Clasificación de producto (común a las tres plataformas)
# ===========================================================================

CLASSIFICATION_RULES = [
    ("STARTER_DECK", ["starter deck", "mazo de inicio"]),
    ("DOUBLE_PACK", ["double pack"]),
    ("MYSTERY_PACK", ["mystery pack", "mystery box"]),
    ("PREMIUM_COLLECTION", ["premium card collection", "the best vol", "the best "]),
    ("ILLUSTRATION_BOX", ["illustration box", "caja de ilustraciones"]),
    ("DEVIL_FRUITS_COLLECTION", ["devil fruits collection"]),
    ("LEARN_DECK", ["learn together", "learn to play"]),
    ("PLAYMAT", ["playmat", "tapete"]),
    ("PROMO_CARD", ["carta promo", "promo pack", "promotion pack"]),
    ("DICE_ACCESSORY", ["dice"]),
    ("LOTE_CARTAS", ["lote"]),
    ("BOOSTER_BOX", ["booster box", "caja de sobres", "caja one piece", "caja"]),
    ("BOOSTER_PACK", ["booster", "sobre "]),
]


@dataclass
class Classification:
    """Resultado de classify_product(): a qué categoría/set/idioma pertenece
    un producto, deducido de su nombre (y opcionalmente del título de
    variante)."""

    product_type: str
    set_code: Optional[str]
    language: Optional[str]
    main_set: Optional[str]


def _detect_language(text: Optional[str]) -> Optional[str]:
    """Detecta idioma en un fragmento de texto (nombre de producto o título de
    variante). El chequeo de "EN" es case-sensitive a propósito (sobre el texto
    ORIGINAL, no en minúsculas) para no confundir con la preposición española
    "en", que aparece constantemente en descripciones de productos."""
    if not text:
        return None
    lower = text.lower()
    if "japones" in lower or "japonés" in lower:
        return "JP"
    if "coreano" in lower:
        return "KR"
    if "ingles" in lower or "inglés" in lower or re.search(r"\bEN\b", text) or "- en" in lower:
        return "EN"
    if "castellano" in lower or "español" in lower:
        return "ES"
    return None


def classify_product(name: Optional[str], variant_title: Optional[str] = None) -> Classification:
    """Clasifica un producto por tipo/set/idioma a partir de su nombre y,
    opcionalmente, del título de su variante (usado como fallback de idioma
    cuando el nombre compartido del producto no lo delata -- p.ej. cuando
    "Inglés"/"Japonés" es una VARIANTE y no parte del título común, como pasa
    en Pokemillon: dos filas del mismo producto, una por variante, cada una
    con su propio stock)."""
    if not name:
        return Classification("OTROS", None, None, None)

    name_lower = name.lower()

    product_type = "OTROS"
    for tipo, keywords in CLASSIFICATION_RULES:
        if any(kw in name_lower for kw in keywords):
            product_type = tipo
            break

    set_match = re.search(r"\b([A-Z]{2,3}-?\d{1,3})\b", name)
    set_code = set_match.group(1).replace("-", "") if set_match else None

    main_set_match = re.search(r"\bOP[\s-]?0*(\d{1,2})\b", name, re.IGNORECASE)
    main_set = f"OP{int(main_set_match.group(1)):02d}" if main_set_match else None

    language = _detect_language(name)
    if language is None:
        language = _detect_language(variant_title)

    return Classification(product_type, set_code, language, main_set)


# ===========================================================================
# Parseo de precios (unificado -- antes había 3 variantes de esta misma lógica)
# ===========================================================================

def parse_price_text(text) -> Optional[float]:
    """Parsea un precio en texto libre o numérico directo. Soporta formato
    español ('1.234,56') y anglosajón/decimal simple ('1234.56' o 1234.56)."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    cleaned = re.sub(r"[^\d,.\-]", "", str(text))
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_price_minor_unit(raw, minor_unit: int = 2) -> Optional[float]:
    """Precio devuelto como entero en la unidad mínima (céntimos), típico de
    Store APIs modernas: '16000' con minor_unit=2 -> 160.00 (WooCommerce Store API)."""
    if raw is None:
        return None
    try:
        return round(int(raw) / (10 ** minor_unit), 2)
    except (TypeError, ValueError):
        return None


# ===========================================================================
# Capa HTTP: sesión + reintentos con backoff (unificada para las 3 plataformas)
# ===========================================================================

def build_session(anti_bot: bool = False, *, config: Optional["StoreConfig"] = None) -> requests.Session:
    """anti_bot=True usa cloudscraper (necesario en tiendas PrestaShop/WooCommerce
    detrás de Cloudflare); Shopify normalmente no lo necesita para su JSON público.

    `config` decide el User-Agent (A.1): IDENTIFIABLE_USER_AGENT por defecto,
    o BROWSER_LIKE_USER_AGENT si config.ua_exception=True (excepción
    documentada de esa tienda concreta). Sin config (o con
    ua_exception=False) siempre se usa el identificable."""
    session = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    ) if anti_bot else requests.Session()

    user_agent = BROWSER_LIKE_USER_AGENT if (config and config.ua_exception) else IDENTIFIABLE_USER_AGENT
    session.headers.update({
        "User-Agent": user_agent,
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    })
    return session


def _backoff_delay(attempt: int) -> float:
    """Segundos a esperar antes del siguiente reintento: backoff exponencial
    (RETRY_BACKOFF_BASE ** attempt) + jitter aleatorio para no sincronizar
    reintentos si varias tiendas fallan a la vez."""
    return (RETRY_BACKOFF_BASE ** attempt) + random.uniform(0, 0.5)


# Algunas tiendas (verificado en AvalonBurgos, 2026-08-25) usan un reto
# anti-bot muy simple: una página intermedia (normalmente HTTP 202) con un
# <meta refresh> que fija una cookie vía JavaScript antes de recargar. No
# hace falta un navegador real para pasarlo -- se probó con curl_cffi
# (impersonando TLS de Chrome) y seguía bloqueado, pero la cookie en sí es
# ESTÁTICA (mismo valor en peticiones repetidas, sin nonce ni timestamp) --
# basta con leerla del HTML y fijarla a mano en la sesión antes de
# reintentar la MISMA petición, con requests normal y corriente.
_JS_COOKIE_CHALLENGE_RE = re.compile(
    r"document\.cookie\s*=\s*'([^=']+)=([^;']+);.*?domain=([^'\"]+)['\"]", re.DOTALL
)


def _solve_js_cookie_challenge(session: requests.Session, response: requests.Response) -> bool:
    """Si `response` es la página del reto anti-bot descrito arriba, fija la
    cookie en `session` y devuelve True (para que el llamador reintente la
    misma petición). Devuelve False si no es esa página -- no toca nada."""
    match = _JS_COOKIE_CHALLENGE_RE.search(response.text)
    if not match:
        return False
    name, value, domain = match.groups()
    session.cookies.set(name, value, domain=domain)
    return True


def _get_with_ssl_fallback(session: requests.Session, url: str, params: Optional[dict],
                            timeout: int) -> requests.Response:
    """La huella TLS que usa cloudscraper (para parecer un Chrome real) a
    veces choca con la configuración TLS de un servidor concreto -- no es lo
    mismo que un bloqueo anti-bot deliberado. Verificado en Arte9
    (2026-08-25): cloudscraper falla ahí con SSLV3_ALERT_HANDSHAKE_FAILURE en
    el 100% de los intentos (reintentar con la MISMA sesión nunca lo
    arregla), mientras que un requests.Session normal conecta sin problema.
    Si pasa esto, se prueba una vez con una sesión de requests normal
    (heredando cookies/headers) antes de dar la petición por fallida."""
    try:
        return session.get(url, params=params, timeout=timeout)
    except requests.exceptions.SSLError:
        plain_session = requests.Session()
        plain_session.headers.update(dict(session.headers))
        plain_session.cookies.update(session.cookies)
        return plain_session.get(url, params=params, timeout=timeout)


def _parse_retry_after(header_value: Optional[str]) -> Optional[float]:
    """Parsea la cabecera Retry-After de un 429/503 (RFC 7231): puede venir
    como segundos ('120') o como fecha HTTP ('Wed, 21 Oct 2026 07:28:00
    GMT'). Devuelve segundos a esperar desde ahora, o None si la cabecera no
    viene o no se puede interpretar (en ese caso el llamador cae al backoff
    exponencial de siempre)."""
    if not header_value:
        return None
    header_value = header_value.strip()
    if header_value.isdigit():
        return float(header_value)
    try:
        parsed = email.utils.parsedate_to_datetime(header_value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max((parsed - datetime.now(timezone.utc)).total_seconds(), 0.0)


def request_with_retries(
    session: requests.Session,
    url: str,
    *,
    params: Optional[dict] = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = MAX_RETRIES,
    heartbeat: Optional[Callable[[], None]] = None,
) -> Optional[requests.Response]:
    """GET con reintentos y backoff exponencial + jitter.

    Reintenta en: errores de red/timeout, 429 (rate limit) y 5xx.
    NO reintenta en: 404 u otros 4xx de cliente (reintentar no cambiaría el resultado).
    Devuelve None solo si TODOS los intentos fallan por error de red (sin respuesta
    HTTP alguna) -- en cualquier otro caso devuelve la Response tal cual para que el
    llamador decida qué hacer con el status_code.
    """
    response: Optional[requests.Response] = None

    for attempt in range(1, max_retries + 1):
        if heartbeat:
            heartbeat()
        try:
            response = _get_with_ssl_fallback(session, url, params, timeout)
        except requests.RequestException:
            if attempt < max_retries:
                time.sleep(_backoff_delay(attempt))
                continue
            return None

        if attempt < max_retries and _solve_js_cookie_challenge(session, response):
            continue  # cookie del reto ya fijada -- reintentar la misma petición

        if response.status_code == 429 or response.status_code >= 500:
            if attempt < max_retries:
                # A.3: un 429 con Retry-After trae una instrucción explícita del
                # servidor -- tiene prioridad sobre nuestra estimación genérica.
                retry_after = _parse_retry_after(response.headers.get("Retry-After")) \
                    if response.status_code == 429 else None
                if retry_after is not None:
                    if retry_after > MAX_RETRY_AFTER_WAIT:
                        # Pide esperar más de lo razonable (mal configurado o
                        # intencionadamente hostil) -- no se bloquea el proceso
                        # completo, se corta aquí y se trata como fallo normal
                        # de esta tienda en este ciclo.
                        return response
                    time.sleep(retry_after)
                else:
                    time.sleep(_backoff_delay(attempt))
                continue
            return response  # devolvemos la última respuesta (fallida) igualmente

        return response  # 2xx, 3xx, o 4xx no reintentable

    return response


# ===========================================================================
# Logging con actividad integrada (evita el patrón repetido print+mark_activity)
# ===========================================================================

class StoreLogger:
    """Une "imprimir por consola" y "marcar actividad" (para el timeout por
    inactividad del dispatcher) en una única llamada. Además recuerda el
    último mensaje de error/aviso (`last_error`) -- todos los scrapers ya
    prefijan sus mensajes de fallo con "ERROR"/"AVISO" por convención, así
    que esto no requiere tocarlos: permite que un resultado sin productos
    (StoreQueryResult.status == "empty") venga acompañado del motivo real
    (bloqueo anti-bot, página caída, categoría vacía...) en vez de un simple
    "0 productos" sin explicación, que es indistinguible entre un problema
    real y un catálogo legítimamente vacío."""

    def __init__(self, label: str, activity_tracker: dict[str, float]):
        """activity_tracker es compartido con el dispatcher (wait_for_store lo
        lee para saber cuánto lleva la tienda sin actividad)."""
        self.label = label
        self._activity_tracker = activity_tracker
        self.last_error: Optional[str] = None
        self.touch()

    def touch(self) -> None:
        """Marca 'actividad ahora mismo' sin imprimir nada -- se llama antes
        de cada petición HTTP para que el timeout por inactividad no cuente
        el tiempo de red como tienda colgada."""
        self._activity_tracker[self.label] = time.time()

    def log(self, message: str) -> None:
        """Imprime `[label] message`, marca actividad, y si el mensaje
        empieza por ERROR/AVISO lo recuerda en last_error."""
        print(f"[{self.label}] {message}")
        if message.startswith(("ERROR", "AVISO")):
            self.last_error = message
        self.touch()


# ===========================================================================
# Scrapers por plataforma -- ver paquete scrapers/ (un módulo por plataforma)
# ===========================================================================

from scrapers import (
    BaseStoreScraper,
    GenericJsonLdScraper,
    OdooScraper,
    OpenCartScraper,
    PrestaShopScraper,
    ShopifyScraper,
    WooCommerceScraper,
)

SCRAPER_CLASSES: dict[Platform, type[BaseStoreScraper]] = {
    Platform.SHOPIFY: ShopifyScraper,
    Platform.PRESTASHOP: PrestaShopScraper,
    Platform.WOOCOMMERCE: WooCommerceScraper,
    Platform.ODOO: OdooScraper,
    Platform.OPENCART: OpenCartScraper,
    Platform.GENERIC_JSONLD: GenericJsonLdScraper,
}


# ===========================================================================
# robots.txt / Crawl-delay (A.2) -- se comprueba una vez por tienda y se
# cachea en store_state.py (no en cada scrape: robots.txt cambia poco).
# ===========================================================================

# Prefijo estable del mensaje de exclusión por robots.txt (ver scrape_store) --
# permite distinguir "excluida por política" de un fallo real de la tienda al
# clasificar resultados (_attempt_scrape, _record_backoff_outcome), sin
# necesitar un campo/estado nuevo en StoreQueryResult solo para esto.
ROBOTS_EXCLUSION_LOG_PREFIX = "AVISO: excluida por robots.txt"


def _is_policy_exclusion(error: Optional[str]) -> bool:
    """True si `error` es la exclusión por robots.txt de scrape_store, no un
    fallo real -- respetar robots.txt no debe abrir el circuito ni contar
    para el backoff entre ejecuciones (A.3)."""
    return bool(error) and error.startswith(ROBOTS_EXCLUSION_LOG_PREFIX)


@dataclass
class RobotsRules:
    disallowed: bool
    crawl_delay: Optional[float]


def _robots_check_target(config: StoreConfig) -> Optional[str]:
    """URL representativa que este scraper va a pedir de verdad, para
    comprobarla contra robots.txt -- una por plataforma, la ruta de listado
    principal (no cada URL de producto individual)."""
    if config.platform == Platform.SHOPIFY:
        return f"{config.domain}/collections/{config.shopify_collection}"
    if config.platform == Platform.PRESTASHOP:
        return config.prestashop_category_url
    if config.platform == Platform.WOOCOMMERCE:
        if config.woocommerce_fallback_paths:
            return f"{config.domain}/{config.woocommerce_fallback_paths[0].strip('/')}/"
        return None  # solo Store API, sin ruta HTML que comprobar
    if config.platform == Platform.ODOO:
        return config.odoo_category_url
    if config.platform == Platform.OPENCART:
        return config.opencart_category_url
    if config.platform == Platform.GENERIC_JSONLD:
        return config.jsonld_listing_urls[0] if config.jsonld_listing_urls else None
    return None


def _fetch_robots_rules(config: StoreConfig, target_url: str, logger: StoreLogger) -> RobotsRules:
    """Descarga y parsea robots.txt de config.domain con RobotFileParser
    (librería estándar). Si no se puede descargar (o no existe), se asume
    permisivo -- es la convención estándar cuando robots.txt no está
    disponible, no una forma de saltárselo."""
    robots_url = f"{config.domain}/robots.txt"
    session = build_session(anti_bot=False, config=config)

    try:
        resp = session.get(robots_url, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException:
        logger.log(f"AVISO: no se pudo descargar {robots_url}, se asume sin restricciones")
        return RobotsRules(disallowed=False, crawl_delay=None)

    parser = RobotFileParser()
    if resp.status_code == 200:
        parser.parse(resp.text.splitlines())
    else:
        parser.parse([])  # 404 u otro -- sin reglas, RobotFileParser permite todo por defecto

    disallowed = not parser.can_fetch("*", target_url)
    crawl_delay = parser.crawl_delay("*")
    return RobotsRules(disallowed=disallowed, crawl_delay=float(crawl_delay) if crawl_delay else None)


def get_robots_rules(config: StoreConfig, target_url: str, logger: StoreLogger) -> RobotsRules:
    """Rules cacheadas en store_state.py, refrescadas cada ROBOTS_CACHE_TTL_SECONDS."""
    state = store_state.get_state(config.domain)
    now = time.time()

    if state.robots_checked_at is not None and (now - state.robots_checked_at) < ROBOTS_CACHE_TTL_SECONDS:
        return RobotsRules(disallowed=state.disallowed, crawl_delay=state.crawl_delay)

    rules = _fetch_robots_rules(config, target_url, logger)
    store_state.update_state(
        config.domain,
        robots_checked_at=now,
        disallowed=rules.disallowed,
        crawl_delay=rules.crawl_delay,
    )
    return rules


# ===========================================================================
# Dispatcher / orquestación
# ===========================================================================

def scrape_store(config: StoreConfig, logger: StoreLogger) -> list[Product]:
    """Instancia el scraper de la plataforma de `config` y lo ejecuta. Punto
    de entrada síncrono y sin red de seguridad -- quien llame a esto (siempre
    dentro de un hilo de un ThreadPoolExecutor, ver run_all_stores/
    query_store) es responsable de aplicar timeout y capturar excepciones.

    Antes de scrapear, respeta robots.txt (A.2): si el Disallow cubre la URL
    que se va a pedir, la tienda se excluye este ciclo con motivo explícito
    en logs -- no se ignora robots.txt para seguir scrapeando de todos
    modos. El Crawl-delay declarado (si lo hay) sube el delay entre
    peticiones de esta tienda por encima de DEFAULT_DELAY, nunca por debajo."""
    logger.log(f"empezando ({config.platform.value})...")

    delay = DEFAULT_DELAY
    target_url = _robots_check_target(config)
    if target_url:
        rules = get_robots_rules(config, target_url, logger)
        if rules.disallowed:
            logger.log(f"{ROBOTS_EXCLUSION_LOG_PREFIX} (Disallow cubre {target_url})")
            return []
        if rules.crawl_delay:
            delay = max(DEFAULT_DELAY, rules.crawl_delay)

    scraper_cls = SCRAPER_CLASSES[config.platform]
    scraper = scraper_cls(config, logger, delay=delay)
    products = scraper.scrape()

    logger.log(f"terminado: {len(products)} productos")
    return products


def wait_for_store(future, label: str, activity_tracker: dict[str, float],
                    timeout: int, poll_interval: int) -> list[Product]:
    """Espera a que termine el future, pero solo lo da por caído si pasan
    `timeout` segundos SIN actividad (no por duración total del scraping)."""
    while True:
        try:
            return future.result(timeout=poll_interval)
        except FutureTimeoutError:
            inactive_for = time.time() - activity_tracker.get(label, 0)
            if inactive_for >= timeout:
                raise


@dataclass
class StoreQueryResult:
    """Resultado de intentar scrapear UNA tienda, siempre estructurado --
    nunca se lanza una excepción fuera de aquí. Pensado para ser el tipo de
    retorno que un futuro endpoint (consulta puntual de una tienda "a dedo"
    desde un front) pueda devolver tal cual, sin que el cliente tenga que
    conocer FutureTimeoutError ni ningún otro detalle interno: solo mira
    `status` y, según el caso, `products` o `error`.

    status:
        "ok"           -- terminó con al menos un producto.
        "empty"        -- terminó sin errores pero con 0 productos (posible
                          selector roto, categoría vacía, o scoping demasiado
                          estricto -- no se puede distinguir automáticamente,
                          hay que revisar a mano).
        "timeout"      -- sin actividad (peticiones enviadas/recibidas)
                          durante `STORE_TIMEOUT` segundos seguidos.
        "error"        -- excepción durante el scraping (red, parseo,
                          bug...); el mensaje queda en `error`, nunca se
                          propaga.
        "circuit_open" -- (solo vía query_store) esta tienda acumuló
                          BREAKER_FAIL_MAX fallos seguidos recientes y el
                          circuito está abierto: ni siquiera se ha intentado
                          la petición esta vez, para no insistir contra algo
                          ya confirmado caído/bloqueado."""
    label: str
    platform: str
    status: str
    products: list[Product] = field(default_factory=list)
    error: Optional[str] = None
    elapsed_seconds: float = 0.0


def _build_query_result(config: StoreConfig, future, logger: StoreLogger, activity_tracker: dict[str, float],
                         timeout: int, poll_interval: int, started: float) -> StoreQueryResult:
    """Espera el future de scrape_store (vía wait_for_store) y lo convierte
    en un StoreQueryResult -- lógica compartida por query_store y
    run_all_stores para que ambos caminos clasifiquen los fallos igual."""
    try:
        products = wait_for_store(future, config.label, activity_tracker, timeout, poll_interval)
    except FutureTimeoutError:
        return StoreQueryResult(config.label, config.platform.value, "timeout",
                                 error=f"sin actividad durante {timeout}s",
                                 elapsed_seconds=time.time() - started)
    except Exception as e:
        return StoreQueryResult(config.label, config.platform.value, "error",
                                 error=f"{type(e).__name__}: {e}",
                                 elapsed_seconds=time.time() - started)

    if products:
        status, error = "ok", None
    else:
        # "empty" a secas es ambiguo (¿categoría legítimamente vacía, o la
        # tienda bloqueó/falló a mitad de camino?) -- si el logger capturó
        # algún ERROR/AVISO durante el scraping, se adjunta como motivo.
        status, error = "empty", logger.last_error

    return StoreQueryResult(config.label, config.platform.value, status, products=products,
                             error=error, elapsed_seconds=time.time() - started)


def find_store(label: str) -> Optional[StoreConfig]:
    """Busca una tienda en STORES por su label exacto (case-sensitive).
    Devuelve None si no existe -- pensado para que un futuro endpoint pueda
    convertir eso directamente en un 404, sin excepciones que capturar."""
    return next((s for s in STORES if s.label == label), None)


class _StoreScrapeFailed(Exception):
    """Señal interna para que pybreaker cuente un StoreQueryResult "malo"
    (timeout/error, o empty con un motivo real) como fallo del circuito, sin
    que query_store() deje de devolver siempre un StoreQueryResult normal
    -- pybreaker solo sabe contar éxitos/fallos de una llamada a través de
    si lanza o no, así que esta excepción lleva el resultado ya calculado
    para que query_store lo recupere tal cual, en vez de tener que rehacer
    el scraping."""

    def __init__(self, result: StoreQueryResult):
        """Envuelve el StoreQueryResult ya calculado para recuperarlo intacto
        en el except de query_store."""
        self.result = result
        super().__init__(result.error or result.status)


# Un CircuitBreaker por tienda (así el bloqueo de una no afecta a las demás),
# creados perezosamente la primera vez que se consulta cada una.
_breakers: dict[str, pybreaker.CircuitBreaker] = {}


def _get_breaker(label: str) -> pybreaker.CircuitBreaker:
    """Devuelve el CircuitBreaker de esta tienda, creándolo la primera vez.
    Vive en memoria del proceso -- se reinicia si el proceso se reinicia."""
    if label not in _breakers:
        _breakers[label] = pybreaker.CircuitBreaker(
            fail_max=BREAKER_FAIL_MAX, reset_timeout=BREAKER_RESET_TIMEOUT,
        )
    return _breakers[label]


def _attempt_scrape(config: StoreConfig, timeout: int, poll_interval: int) -> StoreQueryResult:
    """Un intento real de scrapear `config`: crea su propio logger/activity_
    tracker/ThreadPoolExecutor de un solo hilo, y lanza _StoreScrapeFailed si
    el resultado cuenta como fallo (para que pybreaker.call() lo registre).
    Es el "trabajo real" que envuelve query_store; no se llama directamente
    desde fuera de este módulo."""
    activity_tracker: dict[str, float] = {config.label: time.time()}
    logger = StoreLogger(config.label, activity_tracker)
    started = time.time()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(scrape_store, config, logger)
        result = _build_query_result(config, future, logger, activity_tracker, timeout, poll_interval, started)

    # Cuenta como fallo del circuito: timeout, excepción, o "empty" con un
    # motivo real capturado (bloqueo/página caída). Un "empty" SIN motivo
    # (catálogo legítimamente vacío ahora mismo), o excluida por robots.txt
    # (decisión de política, no un problema de la tienda -- ver A.2), no
    # cuentan -- eso no debe abrir el circuito.
    is_real_failure = result.status in ("timeout", "error") or (
        result.status == "empty" and result.error and not _is_policy_exclusion(result.error)
    )
    if is_real_failure:
        raise _StoreScrapeFailed(result)
    return result


def query_store(config: StoreConfig, *, timeout: int = STORE_TIMEOUT,
                 poll_interval: int = STORE_POLL_INTERVAL, use_breaker: bool = True) -> StoreQueryResult:
    """Scrapea UNA tienda de forma aislada. Es la pieza que reutilizará una
    futura consulta puntual desde un front ("elige una tienda y consúltala
    ahora"): un timeout, una excepción o un selector roto en esa tienda
    concreta no puede tirar abajo nada más que esta llamada.

    Con use_breaker=True (por defecto) protege además contra insistir en una
    tienda ya confirmada caída: tras BREAKER_FAIL_MAX fallos seguidos, deja
    de intentarlo durante BREAKER_RESET_TIMEOUT segundos (status
    "circuit_open"), y lo prueba de nuevo una vez pasado ese tiempo."""
    if not use_breaker:
        try:
            return _attempt_scrape(config, timeout, poll_interval)
        except _StoreScrapeFailed as e:
            return e.result

    breaker = _get_breaker(config.label)
    try:
        return breaker.call(_attempt_scrape, config, timeout, poll_interval)
    except pybreaker.CircuitBreakerError:
        return StoreQueryResult(
            config.label, config.platform.value, "circuit_open",
            error=f"circuito abierto tras {BREAKER_FAIL_MAX} fallos seguidos -- "
                  f"se deja de reintentar automáticamente durante ~{BREAKER_RESET_TIMEOUT}s",
        )
    except _StoreScrapeFailed as e:
        return e.result


def _record_backoff_outcome(config: StoreConfig, result: "StoreQueryResult") -> None:
    """A.3: cuenta fallos seguidos ENTRE EJECUCIONES de run_all_stores (no
    dentro de la misma -- eso ya lo cubre request_with_retries con su propio
    backoff) y fija backoff_until al llegar a STORE_BACKOFF_FAILURE_THRESHOLD,
    para que el PRÓXIMO run_all_stores también respete la pausa. Un éxito
    resetea el contador; una exclusión por robots.txt no cuenta como fallo
    (ver _is_policy_exclusion)."""
    is_failure = result.status in ("timeout", "error") or (
        result.status == "empty" and result.error and not _is_policy_exclusion(result.error)
    )

    if not is_failure:
        store_state.update_state(config.domain, consecutive_failures=0, backoff_until=None)
        return

    state = store_state.get_state(config.domain)
    failures = state.consecutive_failures + 1
    backoff_until = state.backoff_until
    if failures >= STORE_BACKOFF_FAILURE_THRESHOLD:
        backoff_until = time.time() + STORE_BACKOFF_DEFAULT_SECONDS
    store_state.update_state(config.domain, consecutive_failures=failures, backoff_until=backoff_until)


def run_all_stores(stores: list[StoreConfig]) -> tuple[list[Product], list[tuple[str, str, str]]]:
    """Scrapea TODAS las tiendas en paralelo (un hilo por tienda, todas
    lanzadas a la vez) y devuelve (productos_combinados, tiendas_fallidas).
    Usado por main() para la ejecución batch completa -- a diferencia de
    query_store, no lleva circuit breaker en memoria (cada tienda se intenta
    una sola vez por ejecución, así que no hay nada que cortocircuitar
    DENTRO de esta llamada) y comparte un único activity_tracker entre todas
    para que STORE_TIMEOUT se calcule por tienda individualmente.

    Sí respeta el backoff persistido ENTRE ejecuciones (A.3, store_state.py):
    una tienda con backoff_until aún en el futuro (3+ fallos seguidos en
    ejecuciones anteriores) se salta sin intentarla, y cada resultado de esta
    ejecución actualiza ese estado para la siguiente vez que corra esto."""
    activity_tracker: dict[str, float] = {}
    loggers: dict[str, StoreLogger] = {}
    started_at: dict[str, float] = {}
    failed_stores: list[tuple[str, str, str]] = []
    all_products: list[Product] = []

    now = time.time()
    runnable_stores = []
    for config in stores:
        backoff_until = store_state.get_state(config.domain).backoff_until
        if backoff_until and backoff_until > now:
            wait_min = round((backoff_until - now) / 60, 1)
            print(f"[{config.label}] AVISO: en backoff tras fallos repetidos, quedan ~{wait_min} min")
            failed_stores.append((config.label, config.platform.value,
                                   f"en backoff tras fallos repetidos (~{wait_min} min restantes, ver A.3)"))
            continue
        runnable_stores.append(config)

    with ThreadPoolExecutor(max_workers=max(len(runnable_stores), 1)) as executor:
        futures = {}
        for config in runnable_stores:
            started_at[config.label] = time.time()
            logger = StoreLogger(config.label, activity_tracker)
            loggers[config.label] = logger
            futures[executor.submit(scrape_store, config, logger)] = config

        for future, config in futures.items():
            result = _build_query_result(config, future, loggers[config.label], activity_tracker,
                                          STORE_TIMEOUT, STORE_POLL_INTERVAL,
                                          started_at[config.label])
            _record_backoff_outcome(config, result)

            if result.status == "ok":
                all_products.extend(result.products)
            elif result.status == "empty":
                motivo = result.error or "sin productos (0 filas)"
                failed_stores.append((result.label, result.platform, motivo))
            elif result.status == "timeout":
                print(f"[{result.label}] TIMEOUT: {result.error}")
                failed_stores.append((result.label, result.platform, result.error))
            else:
                print(f"[{result.label}] ERROR: {result.error}")
                failed_stores.append((result.label, result.platform, f"error: {result.error}"))

    return all_products, failed_stores


# ===========================================================================
# Salida: CSV + resumen por consola
# ===========================================================================

def write_products_csv(products: list[Product], path: str = OUTPUT_CSV) -> None:
    """Vuelca todos los productos a un CSV, una fila por producto/variante."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for p in products:
            writer.writerow(p.to_dict())


def write_failed_stores_csv(failed: list[tuple[str, str, str]], path: str = FAILED_STORES_CSV) -> None:
    """Vuelca la lista (label, platform, motivo) de tiendas sin productos a
    un CSV aparte, para revisar qué falló sin tener que leer el log completo."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["store", "platform", "motivo"])
        writer.writerows(failed)


def print_summary(products: list[Product]) -> None:
    """Imprime por consola: filas por tienda, totales por tipo de producto,
    totales por disponibilidad, y el precio mínimo de cada set (OP-XX) tienda
    por tienda -- el "chollómetro" que motiva monitorizar varias tiendas a
    la vez."""
    from collections import Counter

    print("\nFilas por tienda:")
    for tienda, n in Counter(p.store for p in products).most_common():
        print(f"  {tienda}: {n}")

    print("\nResumen global por tipo de producto:")
    for tipo, n in Counter(p.product_type for p in products).most_common():
        print(f"  {tipo}: {n}")

    print("\nResumen global de disponibilidad:")
    for estado, n in Counter(p.stock_status for p in products).most_common():
        print(f"  {estado}: {n}")

    print("\nComparativa de precio mínimo por set (OP-XX) y tienda:")
    sets_vistos = sorted({p.main_set for p in products if p.main_set})
    for set_code in sets_vistos:
        print(f"  {set_code}:")
        por_tienda: dict[str, float] = {}
        for p in products:
            if p.main_set != set_code or p.price is None:
                continue
            if p.store not in por_tienda or p.price < por_tienda[p.store]:
                por_tienda[p.store] = p.price
        for tienda, precio in sorted(por_tienda.items(), key=lambda x: x[1]):
            print(f"    {tienda}: {precio:.2f} €")


# ===========================================================================
# main
# ===========================================================================

def main() -> None:
    """Punto de entrada del script: scrapea todas las STORES, escribe los
    CSV de salida, imprime el resumen, y actualiza el histórico de precios en
    SQLite si price_history.py está disponible (import diferido y opcional,
    ver el try/except de más abajo)."""
    all_products, failed_stores = run_all_stores(STORES)

    print(f"\nTotal productos combinados de todas las tiendas: {len(all_products)}")

    if failed_stores:
        write_failed_stores_csv(failed_stores)
        print(f"\n{len(failed_stores)} tienda(s) con problemas -> guardado en {FAILED_STORES_CSV}")
        for label, platform, motivo in failed_stores:
            print(f"  {label} ({platform}): {motivo}")

    if all_products:
        write_products_csv(all_products)
        print(f"Guardado en {OUTPUT_CSV}")
        print_summary(all_products)

        # Persistencia en SQLite para histórico de precios (ranking de chollos,
        # tendencias...). Import diferido para que scraper_unificado.py se
        # pueda seguir usando solo con el CSV si price_history.py no está
        # disponible en algún entorno (p.ej. una ejecución puntual de debug).
        try:
            from price_history import save_snapshot
            run_id = save_snapshot(all_products)
            print(f"Histórico de precios actualizado (run_id={run_id})")
        except ImportError:
            print("AVISO: price_history.py no encontrado, no se guarda histórico de precios")


if __name__ == "__main__":
    main()