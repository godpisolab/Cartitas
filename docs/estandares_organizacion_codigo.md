# Estándares de organización de código — Cartitas

Guía de referencia para evitar los problemas de organización encontrados en `store_monitor` (concentrados casi enteros en `base_script.py`). No es una reescritura del proyecto — es la regla que aplicamos a partir de ahora, y el criterio para decidir cuándo un fichero existente necesita partirse.

---

## 1. Tamaño y responsabilidad de un módulo

**Regla de partida: si un fichero necesita más de un bloque de comentario tipo `# ====== Sección ======` para organizarse internamente, son varios ficheros, no una sección interna.**

Señales concretas de que un módulo ha crecido más de lo que debería (cualquiera de estas, no hace falta que se den todas):

- Supera ~300-400 líneas.
- Mezcla más de una de estas capas en el mismo fichero: **datos de configuración** (listas/constantes), **modelo de dominio** (dataclasses sin lógica), **lógica de negocio pura** (sin red ni BBDD), **infraestructura** (HTTP, BBDD, colas), **orquestación** (dispatchers, schedulers), **entry point** (`main()`, CLI).
- El nombre del fichero no basta para predecir qué hay dentro sin abrirlo (`base_script.py` es el ejemplo — no dice si ahí vive la config, el modelo, el HTTP o el `main()`).
- Hace falta un import **dentro de una función** (no arriba del fichero) para evitar un ciclo. Esto no es nunca una solución aceptable a largo plazo — es una señal de que dos módulos están mal cortados y de que en realidad forman un ciclo real de dependencias que hay que resolver partiendo uno de los dos, no aplazando el import.

**Regla de nombrado:** el nombre de un módulo debe decir su responsabilidad, no su origen histórico. `base_script.py` es un nombre de "es el único script que había al principio", no de "esto es lo que contiene". Un módulo que hoy se llama así y ha crecido es la señal más clara de que toca partirlo y renombrar lo que quede.

---

## 2. Dirección de dependencias (evitar ciclos por diseño, no por parches)

**Regla: las dependencias van en una sola dirección, de capas de bajo nivel a capas de alto nivel. Un módulo de una capa nunca importa de una capa superior.**

Capas, de más baja a más alta (cada una solo puede depender de las de su izquierda):

```
dominio  →  config  →  lógica pura  →  persistencia  →  estado/caché  →  infraestructura HTTP  →  dispatcher  →  scrapers  →  entry point
```

- **Dominio** (`Product`, `StoreConfig`, `Classification`, resultados de operación...): dataclasses puras. **Cero imports internos del proyecto.** Si una dataclase necesita importar otra cosa del propio proyecto para existir, probablemente no es dominio puro.
- **Config**: datos de configuración (listas de tiendas, constantes de negocio). Puede depender de dominio (para tipar), nada más.
- **Lógica pura**: funciones sin efectos secundarios (clasificación, parseo, cálculo). Sin HTTP, sin BBDD, sin logging con estado.
- **Persistencia**: lectura/escritura a BBDD. Depende de dominio, nunca de infraestructura HTTP ni del dispatcher.
- **Estado/caché**: cualquier estado que sobrevive entre ejecuciones. Puede depender de persistencia si vive en BBDD.
- **Infraestructura HTTP**: sesiones, reintentos, robots.txt. Puede depender de estado/caché (para no repetir comprobaciones), nunca al revés.
- **Dispatcher/orquestación**: junta las piezas de abajo para ejecutar el trabajo real.
- **Scrapers**: implementan el contrato de dominio + usan infraestructura HTTP. No deberían necesitar importar el dispatcher.
- **Entry point** (`main.py`, `scheduler.py`, CLI): el único sitio que puede importar de todo lo anterior a la vez, porque es el que las junta todas.

**Consecuencia práctica y verificable:** con esta dirección de dependencias, un import dentro de una función para "evitar un ciclo" no debería volver a hacer falta. Si aparece, es la señal de que algo de una capa alta se ha colado dentro de una capa baja (normalmente: una dataclase de dominio metida en el mismo fichero que el dispatcher o el HTTP client), y la solución es mover esa dataclase a su propio módulo de dominio, no diferir el import.

**Checklist antes de añadir un `import` nuevo:**
1. ¿El módulo al que importo está en una capa igual o inferior a la mía? Si es superior, algo está mal cortado — no lo soluciones con un import diferido, replantea qué fichero debería tener esa pieza.
2. Si Python se queja de un ciclo, la respuesta casi nunca es "diferir el import" — es "una de las dos piezas que se necesitan mutuamente en realidad no pertenece a ninguno de los dos módulos actuales, sino a uno nuevo más pequeño del que ambos puedan depender sin depender entre sí".

---

## 3. Datos de configuración separados del código que los usa

**Regla: una lista de configuración (tiendas, endpoints, credenciales de entorno) vive en su propio módulo/fichero, nunca mezclada con la lógica que la consume.**

Motivo: son cosas que cambian por razones distintas y las toca gente/momentos distintos — añadir una tienda nueva no debería requerir abrir el mismo fichero que el dispatcher HTTP. Mantenerlas separadas también hace que el fichero de configuración se pueda leer de un vistazo sin naufragar en 1000 líneas de lógica alrededor.

Esto no significa necesariamente sacarlo a YAML/JSON/BBDD — puede seguir siendo Python tipado (dataclasses con validación en `__post_init__`, como ya se hace aquí, es una buena práctica que se mantiene). Lo que cambia es que vive en su propio fichero, no dentro del módulo de 1500 líneas.

---

## 4. Documentación en el propio código — seguir haciendo esto, es un punto fuerte real

Esto **no** es una corrección, es una práctica que ya funciona bien en este proyecto y merece quedar escrita como estándar para que no se pierda al refactorizar:

- Cada decisión de diseño no obvia lleva su razón en un docstring/comentario junto al código, no solo en el documento de diseño aparte — quien lee `OpenCartScraper` entiende *por qué* el stock nunca se adivina sin tener que ir a buscar el documento de decisiones.
- Los bugs reales encontrados en producción (Arte9, Pokemillon, TCG Legacy...) se documentan con el nombre de la tienda y qué se vio exactamente — esto convierte el código en una base de casos de prueba reales, y es oro para escribir tests de regresión (ver `plan_de_pruebas.md`).
- Cuando algo es una limitación conocida y aceptada (no un bug pendiente), se dice explícitamente como tal ("decisión deliberada, no un descuido") — evita que alguien "arregle" en el futuro algo que era intencional.

**Regla a partir de ahora:** todo módulo nuevo lleva un docstring de cabecera que dice, en una o dos frases, qué responsabilidad tiene y por qué existe como módulo separado (no solo qué hace). Si cuesta escribir esa frase en una línea sin usar "y" tres veces, el módulo probablemente tiene más de una responsabilidad.

---

## 5. Testing — parte del mismo estándar, no una fase aparte

Ya definido en detalle en `plan_de_pruebas.md` — se resume aquí como regla de organización porque está directamente relacionado: **un módulo bien cortado por capas es automáticamente más fácil de testear**, y viceversa, la dificultad de testear algo es una señal temprana de mal corte.

- Lógica pura (dominio, clasificación, parseo) → tests unitarios rápidos, sin mocks de red/BBDD. Si escribir uno de estos tests obliga a importar `cloudscraper`/`psycopg2`, el módulo está mezclando capas.
- Infraestructura HTTP y scrapers → tests de integración con HTTP mockeado (`requests-mock`), nunca contra la red real.
- Persistencia → tests de integración contra Postgres real efímero (`testcontainers`), nunca mocks de `psycopg2` (el riesgo está en el SQL, no en la llamada a la librería).
- Todo módulo nuevo se acompaña de sus tests en el mismo commit/PR que lo introduce — no se aplaza "para después", porque después nunca llega a coste cero.

---

## 6. Evolución del esquema de base de datos: migraciones versionadas, no ediciones in-place

**Regla: a partir de ahora, ningún cambio de esquema se hace editando `schema-postgresql-app-tcg.sql` in-place sin más. Cada cambio de esquema es una migración numerada, con su propio fichero.**

Motivo directo, no hipotético: el cambio del `UNIQUE` de `store_product` (añadir `raw_variant`) ya necesitó esto en la práctica — el propio README del commit avisa de que aplicar el esquema nuevo sobre una BBDD ya creada requiere un `ALTER TABLE` a mano, sin ningún script que lo automatice ni registro de si ya se aplicó. Es exactamente el problema que una herramienta de migraciones existe para resolver.

- **Herramienta:** Alembic. Cada cambio de esquema (columna nueva, constraint modificada, valor añadido a un ENUM) es un fichero de migración con `upgrade()`/`downgrade()`, aplicado con `alembic upgrade head` — nunca recreando el volumen de Docker para forzar el esquema nuevo.
- **Cuándo aplica:** desde el primer cambio de esquema que se haga a partir de ahora — no hace falta migrar el esquema actual retroactivamente a Alembic si no ha cambiado, pero el *próximo* cambio sí debería ser ya una migración, no una edición directa del `.sql`.
- **Casos que requieren atención especial al escribir la migración** (aprendido del propio proyecto): cambios de `UNIQUE`/constraints sobre tablas con datos reales, y `ALTER TYPE ... ADD VALUE` sobre ENUMs (no es transaccional en todas las versiones de Postgres) — documentar el procedimiento manual en el `downgrade()` cuando no exista un downgrade automático limpio, en vez de fingir que lo hay.

---

## 7. Checklist antes de un commit/PR grande

Aplicable en particular a commits que tocan varios módulos a la vez (como el que introdujo scheduling/matching/persistencia junta) — no para bloquear el trabajo, sino como repaso de 2 minutos antes de dar el commit por cerrado:

- [ ] ¿Algún fichero tocado ha cruzado las ~300-400 líneas o ha ganado una sección nueva de responsabilidad? Si sí, ¿merece partirse ya o se anota para la siguiente pasada?
- [ ] ¿Se ha añadido algún `import` dentro de una función para evitar un ciclo? Si sí, es una señal a resolver, no a normalizar.
- [ ] ¿El cambio de esquema (si lo hay) es una migración versionada, o una edición directa del `.sql` que alguien con datos reales tendrá que reconciliar a mano?
- [ ] ¿Los módulos nuevos tienen su docstring de responsabilidad y sus tests correspondientes en el mismo commit?
- [ ] ¿La documentación de diseño (`docs/*.md`) sigue siendo consistente con lo que el código realmente hace, o quedó algo desactualizado (como pasó con `suggested_product_id` en `modelo-datos-app-tcg.md`, que el propio esquema ya había retirado)?

---

## Siguiente paso natural

Aplicar la sección 1-2 de esta guía al refactor de `base_script.py` ya propuesto (`domain.py` → `config.py` → `classify.py` → `persistence.py`/`store_state.py` sin tocar → `http_client.py` → `dispatcher.py`) es la forma más directa de convertir estas reglas de "documento" a "hecho" — es el caso de uso que las motivó, así que sirve de plantilla para la próxima vez que un módulo crezca de más.
