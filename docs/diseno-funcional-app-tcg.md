# Diseño funcional — App de stock unificado TCG (Pokémon + One Piece)

## Núcleo (MVP)

La idea base: **un buscador que consulta el stock de producto sellado en todas las tiendas online españolas trackeadas, de forma unificada.**

1. **Buscador de producto** — por nombre, set (OP-16, SV-10...), o tipo (Booster Box, Booster Pack, Starter Deck, Illustration Box, Single...).
2. **Ficha de producto canónico** — agrupa el mismo producto ("OP-16 Booster Box EN") a través de todas las tiendas, mostrando: tienda, precio, stock (disponible/agotado), link directo de compra.
3. **Filtros** — por juego (Pokémon/One Piece), por idioma de producto (EN/JP/ES), por tipo de producto, por rango de precio.
4. **Indicador de stock claro** — disponible / agotado / stock bajo (si la tienda lo expone), con fecha de última comprobación.
5. **Comparación de precio entre tiendas** — para el mismo producto, ordenado de más barato a más caro.
6. **Ranking de mejores ofertas activas** — home con "chollos" del momento, basado en histórico de precios (no MSRP manual).
7. **Histórico de precio por producto** — gráfico de evolución, visible al usuario (decidido: 1 snapshot diario por producto/tienda, ver documento de modelo de datos).

Esto ya es un producto usable y con valor real: nadie más en España lo está haciendo agregado.

---

## Capas de valor añadido (ordenadas de más a menos evidentes)

### Nivel 1 — Lo más pedido por la comunidad de coleccionismo
- **Alertas de restock** — "avísame cuando vuelva a haber stock de X". Es el dolor #1 en TCG: sets populares (OP-16, OP-13...) se agotan en minutos y la gente vive refrescando webs. Decidido: notificación push (ver "Decisiones tomadas" más abajo), con detección diaria de base y refresco más frecuente para productos marcados como calientes (ver modelo de datos, punto 4).
- **Alertas de bajada de precio** — "avísame si baja de X€".
- **Feed de restocks recientes** — sección tipo "últimas 24h: qué ha vuelto a stock", muy consumible y compartible en redes/Discord de la comunidad.

### Nivel 2 — Comparación más justa/completa
- **Coste real con envío** — mostrar precio + gastos de envío estimados (o umbral de envío gratis), porque "más barato" sin envío puede no serlo.
- **Comparador de cesta** — si el usuario quiere varios productos, calcular en qué tienda(s) sale más barato el conjunto (útil cuando conviene concentrar la compra en una tienda para ahorrar envío).
- **Índice de fiabilidad de tienda** — basado en frecuencia con la que aparece "agotado" pese a figurar como disponible, tiempo medio de scrape, etc. (dato que tú ya generas sin darte cuenta con el histórico).

### Nivel 3 — Para ti como vendedor (doble uso)
- **Dashboard de posicionamiento** — cómo se sitúa tu tienda frente a la competencia por producto, con alertas cuando alguien baja de precio en algo que tú también vendes.
- **Sugerencia de precio** — basada en el rango de precios del mercado para ese producto en ese momento.

### Nivel 4 — Comunidad y descubrimiento
- **Mapa de tiendas** — reutilizando el dataset de coordenadas de TCG Map Spain, cruzado con qué tienen en stock online.
- **Valoraciones de tienda por usuarios** (fiabilidad, tiempo de envío) — cuidado, esto añade carga de moderación.
- **Tendencia de precio** — "este producto lleva subiendo/bajando X% en las últimas 2 semanas" (útil tanto para comprar como para que tú decidas cuándo reponer).

### Nivel 5 — Expansión de alcance
- **API pública / RSS** de restocks para que la comunidad construya bots encima (grupos de Discord/Telegram ya hacen esto de forma manual).
- **Extender a otros TCG** (Magic, Yu-Gi-Oh, Lorcana, Dragon Ball) una vez el motor esté maduro.

---

## Lo que probablemente NO conviene meter todavía
- Sistema de reviews/comunidad con moderación — mucho esfuerzo de mantenimiento para el valor que aporta al inicio.
- Predicción de precio con ML — el histórico aún es corto; con pocos meses de datos no hay señal suficiente.
- Multi-tienda propia de venta (marketplace) — cambia el modelo de negocio por completo, es una app distinta.

---

## Decisiones tomadas
✅ **Alertas de restock** confirmada como funcionalidad prioritaria de v1, junto al núcleo (buscador + comparación + histórico).
✅ Canal de alerta: **notificación push en la web/app** (no email ni Telegram para v1).
✅ Granularidad de suscripción: **producto específico** (ej. "OP-16 Booster Box EN"), no set completo.
✅ **Sin login** — la suscripción se identifica por dispositivo/navegador (vía el token que genera la Web Push API al aceptar el permiso), no por cuenta de usuario.

### Implicaciones funcionales de estas decisiones
- Sin login: la Web Push API ya genera un identificador único de "endpoint" por navegador/dispositivo al aceptar el permiso — ese token es suficiente para guardar qué productos sigue ese dispositivo, sin pedir email ni contraseña.
- Contrapartida a tener en cuenta: si el usuario borra el navegador, cambia de dispositivo, o revoca el permiso, pierde sus suscripciones sin forma de recuperarlas (no hay cuenta que las vincule). Aceptable para v1 dado que reduce fricción de entrada.
- Cada producto canónico necesita un botón claro tipo "avísame cuando vuelva" en su ficha, que registra el token del dispositivo junto al product_id.
- El motor de scraping pasa de "guardar histórico" a también **disparar eventos** cuando detecta transición agotado→disponible para un producto con suscriptores — esto es lógica nueva sobre el scraper actual, no solo guardar en SQLite.
