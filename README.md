# Estimador CAG

Estimador de proyectos de software basado en **CAG (Cache-Augmented Generation)**. A partir de la descripción de un proyecto, genera una estimación **estructurada**: fases, duración en semanas, costo en euros y nivel de confianza.

El contexto (estimaciones de ejemplo en `app/prompts/estimation/v1/examples.j2`) se inyecta directamente en el system prompt de cada llamada al LLM, sin base de datos ni búsqueda vectorial.

El proyecto tiene dos piezas:

- **Servicio IA** (FastAPI): expone `POST /api/v1/estimate`, es el único que habla con el LLM y devuelve **datos tipados**, no prosa.
- **Cliente** (Streamlit): un formulario que arma un `EstimationRequest` tipado, lo envía al servicio por HTTP y dibuja el resultado.

```
Navegador → Streamlit (formulario) → POST /api/v1/estimate → FastAPI → Instructor + LiteLLM → LLM
                    ▲                                                          │
                    └──────────── EstimationResult (JSON validado) ◄───────────┘
```

## Características

- **Contrato tipado con Pydantic v2** (`app/schemas/estimation.py`): request, response y el resultado estructurado (`EstimationResult`), con enums y validaciones. El cliente reutiliza las mismas clases.
- **Salida estructurada con Instructor + LiteLLM:** el LLM devuelve JSON que cumple `EstimationResult`. Si no lo cumple, Instructor le reenvía el error al modelo y reintenta.
- **Una sola interfaz para todos los proveedores:** cambiar de `openai/gpt-4o-mini` a `anthropic/claude-haiku-4-5` es una línea en `.env`.
- **Streaming parcial con Instructor** (`POST /api/v1/estimate/stream`): el `EstimationResult` llega campo por campo y la UI muestra las fases a medida que se generan; el resultado final se valida igual que sin streaming.
- **Guardrails de salida** con políticas declaradas: *fix · retry* (Instructor reintenta) y *filter* (respuesta degradada "Out of scope").
- **Caching exact-match:** una solicitud idéntica (mismo modelo, versión de prompt, tipo, nivel de detalle y descripción) sale de una caché en memoria (LRU) sin llamar al LLM. La respuesta lo indica con `cached`.
- **Observabilidad con structlog:** se registra cada llamada al LLM (modelo, versión del prompt, opciones elegidas, tokens, duración, acierto de caché, errores) y cada request HTTP de la API.
- **Prompts versionados con Jinja2** (`app/prompts/`): el system y el user prompt viven en plantillas `.j2`. La versión activa se elige con `PROMPT_VERSION`.

## Salida estructurada

Una definición, tres usos:

```
EstimationResult (Pydantic)
   │  .model_json_schema()
   ▼
JSON Schema ──► Instructor + LiteLLM se lo mandan al LLM con el mecanismo nativo de cada proveedor
   │
   ▼
JSON del LLM ──► Pydantic valida (tipos, rangos y que los totales cierren con las fases)
   │                 └─ si falla: Instructor reenvía el error y el modelo corrige (LLM_MAX_RETRIES)
   ▼
EstimationResult ──► caché ──► EstimationResponse ──► Streamlit dibuja tabla, lista o narrativa
```

- **El orden de los campos importa:** `phases` va antes que los totales, así el modelo detalla las fases y recién después suma.
- **`@model_validator`** comprueba que `total_duration_weeks` y `total_cost_eur` sean exactamente la suma de las fases.
- **`output_format` es una decisión de la UI:** el prompt y los datos son los mismos para los tres formatos; Streamlit elige cómo dibujarlos.

## Prompts

```
app/prompts/
├── loader.py                 # render_estimation_prompt(request, version="v1") -> (system, user)
└── estimation/
    └── v1/
        ├── system.j2         # Rol, reglas de la estimación y bloque según detail_level
        ├── user.j2           # Envuelve el tipo de proyecto y la descripción del usuario
        └── examples.j2       # Ejemplos few-shot en el mismo JSON que EstimationResult
```

- `system.j2` arma el rol y las reglas, y con `{% if %}` incluye **solo** las instrucciones del nivel de detalle elegido. Inserta los ejemplos con `{% include "examples.j2" %}`.
- Los ejemplos están escritos en el mismo formato JSON que debe devolver el modelo, y un test verifica que cumplan `EstimationResult`.
- El loader usa `Environment` de Jinja2 con `StrictUndefined`, `trim_blocks` y `lstrip_blocks`.

### Crear una versión nueva del prompt

1. Copiá la carpeta `app/prompts/estimation/v1` como `v2` y editá las plantillas.
2. Cambiá `PROMPT_VERSION=v2` en `.env` y reiniciá el servicio IA.

No hace falta tocar código. Cada respuesta indica en `prompt_version` qué versión la generó.

## Contrato del servicio

`EstimationRequest`:

| Campo | Tipo | Valores |
|---|---|---|
| `description` | texto | entre 20 y 2000 caracteres |
| `project_type` | enum | `mobile_app`, `web_saas`, `internal_tool`, `data_pipeline` |
| `detail_level` | enum | `summary`, `medium`, `detailed` |
| `output_format` | enum | `phases_table`, `line_items`, `narrative` |

`EstimationResponse`:

| Campo | Tipo | Descripción |
|---|---|---|
| `result` | `EstimationResult` | La estimación estructurada |
| `prompt_version` | texto | Versión del prompt que la generó |
| `cached` | booleano | Si salió de la caché sin llamar al LLM |

`EstimationResult`:

| Campo | Tipo | Reglas |
|---|---|---|
| `summary` | texto | Resumen y supuestos |
| `phases` | lista de `Phase` | Al menos una; cada fase con `name`, `description`, `duration_weeks` (≥ 1), `cost_eur` (≥ 0) y `confidence_pct` (0-100) |
| `total_duration_weeks` | entero | Igual a la suma de las fases |
| `total_cost_eur` | entero | Igual a la suma de las fases |
| `confidence_pct` | entero | 0-100 |

Errores:

- **422:** el request no cumple el contrato.
- **502:** el proveedor falló (red, API key, límites) o el modelo no devolvió un resultado válido después de los reintentos.

### Pipeline y políticas de fallo

`POST /api/v1/estimate`: el router (`app/routers/estimations.py`) solo valida el body, delega en el caso de uso y traduce errores a HTTP. El flujo vive en `app/services/estimation.py`, y **cada paso declara qué pasa cuando falla**:

| Paso | Qué hace | Política |
|---|---|---|
| 1. Input guardrails | Toxicidad, prompt injection, PII | **exception** (pendiente: otro bloque del curso) |
| 2. Cache lookup | Busca un pedido idéntico | **return on hit** |
| 3. Render prompt | `render_estimation_prompt(request)` → `(system, user)` | determinístico |
| 4. LLM + Instructor | `role: "system"` y `role: "user"` separados, exigiendo `EstimationResult` | **fix · retry**: si Pydantic rechaza la respuesta, el modelo la corrige |
| 5. Output guardrail | `enforce_scope_response` (`app/guardrails/output.py`) | **filter**: degrada a `"Out of scope: ..."` |
| 6. Cache write | Guarda el resultado | **solo si es válido** |

### Streaming parcial

`POST /api/v1/estimate/stream` recibe el mismo `EstimationRequest` y responde **NDJSON** (`application/x-ndjson`): una línea JSON por evento, con la forma de `EstimationStreamEvent`.

```
{"type": "partial", "partial": {"summary": "SaaS B2B...", "phases": [{"name": "Discovery"}], "total_cost_eur": null, ...}}
{"type": "partial", "partial": {"summary": "SaaS B2B de reservas...", "phases": [{"name": "Discovery", "duration_weeks": 4}, ...]}}
{"type": "final",   "response": {"result": {...}, "prompt_version": "v1", "cached": false}}
```

- **partial:** Instructor (`create_partial`) entrega el objeto a medio completar. Mientras el JSON está incompleto **no se valida**; los campos que faltan llegan en `null`.
- **final:** el último parcial se valida contra `EstimationResult` completo (tipos, rangos y `model_validator`). Si no pasa, el servicio cae al camino **fix · retry** sin streaming, y después aplica el output guardrail y la caché como siempre.
- **error:** como el `200` se envía al empezar el stream, un error posterior llega como `{"type": "error", "detail": "..."}`.
- Un acierto de caché se entrega directamente como un único evento `final`.

Guardrails de salida:

- **Fix · retry** (`model_validator` en `EstimationResult`): los totales tienen que cerrar con las fases, y si `confidence_pct` es menor a 40, el `summary` tiene que empezar con `"Out of scope:"`. Así el modelo declara él mismo cuándo no puede estimar en lugar de inventar números.
- **Filter** (`enforce_scope_response`): si el modelo declaró *out of scope*, o el proyecto supera `SCOPE_MAX_WEEKS`, se devuelve un `EstimationResult` con la forma de siempre y el prefijo en el `summary` (HTTP 200). Cada disparo queda en los logs como `guardrail_triggered` con su política y motivo.

### Guardrail de entrada: moderación, prompt injection y PII

`app/guardrails/input.py` (basado en el del curso) corre tres capas **antes del cache y del LLM**, en los tres endpoints de estimación:

| Capa | Cómo | Si falla |
|---|---|---|
| Moderación | Moderation API de OpenAI vía LiteLLM (`MODERATION_MODEL`) | Solo con `MODERATION_ENABLED` y `OPENAI_API_KEY`; si la llamada falla, deja pasar (fail open) y loguea `moderation_call_failed` |
| Prompt injection | Regex del curso ("ignore previous instructions", `</system>`, "you are now"...) y equivalentes en español | — |
| PII | Regex de email, IBAN y teléfono (10+ dígitos, para no marcar montos ni fechas) | — |

Política **exception**, nunca fix-and-retry: la primera capa que dispara lanza `InputGuardrailViolation` y la API responde **400** con `{"reason": "moderation" | "prompt_injection" | "pii", "message": ...}`. En `/estimate/stream` se chequea antes de abrir el stream, así que también es un 400 y no un evento de error. En sesiones se revisa el texto del turno más el texto extraído de los adjuntos; el turno bloqueado no entra al historial. Los PDFs que viajan por el camino multimodal no se pueden revisar en local.

Va antes del cache para que un pedido malicioso o con datos personales nunca quede guardado. En los tests la moderación está apagada y cualquier llamada real falla (`tests/conftest.py`).

## Observabilidad del wrapper LLM

Cada llamada del wrapper (`app/services/llm_wrapper.py`) devuelve un `LLMCall` con el resultado validado y sus métricas (`LLMCallMetrics`):

| Campo | Qué es |
|---|---|
| `purpose` | Para qué se hizo la llamada (`estimation`, `metadata_extraction`) |
| `model` / `provider` | Modelo usado y proveedor detectado por LiteLLM |
| `latency_ms` | Duración de la llamada, incluidos los reintentos de Instructor |
| `tokens_in` / `tokens_out` | Tokens informados por el proveedor |
| `cost_usd` | Costo calculado con la tabla `MODEL_COSTS` (USD por millón de tokens de entrada y de salida) |

Las métricas se registran en el log `llm_request_completed`, y los turnos de sesión las devuelven en `llm_calls` (una entrada por cada llamada del turno). Si un modelo no está en `MODEL_COSTS`, `cost_usd` es `null`: hay que agregarlo a la tabla.

## Sesiones y adjuntos

### Sesiones

`POST /api/v1/sessions` crea una sesión vacía y devuelve `{"session_id": "<uuid4>"}`. El cliente reenvía ese id en cada petición posterior.

Cada sesión (`app/sessions.py`) separa:

- **Historial** (`ConversationHistory`): el array `messages` que viaja a la API, con ventana deslizante. Un turno es un par user + assistant; se conservan los últimos `MAX_TURNS = 6` (ajustable con `SESSION_MAX_TURNS`) y, al superarlo, se descarta el par más antiguo.
- **Memoria** (`ProjectMetadata`): hechos del proyecto (nombre, tamaño de equipo, tecnologías, alcance) que se conservan aunque la ventana descarte turnos.

En cada turno, `history.to_messages_list(project_metadata, build_system_prompt)` arma el array para la API:

```
[ system  ← regenerado con el project_metadata actual (invariante: siempre primero)
  user    ┐
  assistant┘ par más antiguo de la ventana
  ...
  user    ┐
  assistant┘ par más reciente
] + user actual (prompt + adjuntos)
```

El system prompt no se guarda en la ventana: se regenera en cada turno. Así, un hecho mencionado en el turno 1 sigue llegando al modelo en el turno 10 por el bloque `<project_metadata>`, aunque ese turno ya haya salido de la ventana. Al historial va el texto del usuario con los **nombres** de los adjuntos, no su contenido (que ya quedó destilado en la metadata), y un turno solo se guarda si se completó sin errores.

Las sesiones viven en un diccionario del proceso: se pierden al reiniciar el servicio. Es una decisión consciente para esta fase (ver el docstring de `app/sessions.py`).

### Anclas: turnos que no salen de la ventana

La ventana deslizante descarta por **antigüedad**, no por importancia. Un turno donde se firmó el contrato o se fijó el presupuesto no debería perderse por ser viejo. Las anclas (`app/anchors.py`) resuelven eso: un turno anclado, al salir de la ventana, se conserva aparte y sigue viajando al LLM.

**Ocho reglas, cero LLM.** Solo se analiza el mensaje del **usuario**; la respuesta del assistant entra por arrastre, porque se ancla el turno completo.

| Regla | Ejemplos que la disparan |
|---|---|
| `nda` | "under embargo", "acuerdo de confidencialidad" |
| `signed_contract` | "signed the contract", "firmamos el contrato" |
| `scope_frozen` | "scope is frozen", "el alcance quedó cerrado" |
| `budget_locked` | "budget is capped at", "presupuesto máximo de" |
| `compliance` | HIPAA, GDPR/RGPD, SOX, PCI-DSS, FDA, ISO 27001 |
| `deadline_hard` | "hard deadline", "tiene que salir en producción antes del" |
| `contractual` | "contractually required", "obligación contractual" |
| `explicit_commitment` | "we agreed to", "el cliente se comprometió a" |

Las regex en inglés son las del curso, y cada regla suma su equivalente en español. `AnchorMatch` devuelve **todas** las reglas que dispararon, y cada anclaje deja el log `anchor_detected` con ellas.

**Mejor quedarse corto que anclar de más.** Un falso negativo termina en el resumen; un falso positivo queda literal en el prompt para siempre. Por eso las reglas son frases específicas, no palabras sueltas ("final" o "required" solas no anclan), y las anclas tienen su propio límite (`MAX_ANCHORS`).

```
messages = system + anclas que salieron de la ventana + ventana + user actual   (orden cronológico)
```

`ANCHOR_DETECTION_MODE=llm` cambia las reglas por un clasificador LLM, para paráfrasis densas ("no nos podemos pasar de 40 mil, ni un euro más"). El clasificador (`_AnchorClassification`) devuelve `is_anchor` y un `reason` de hasta 200 caracteres; en ese modo `matched_rules` es `["llm"]` y el motivo va al log y a `anchor_reason`. Agrega una llamada por turno, que aparece en `llm_calls` con `purpose=anchor_detection`; si el clasificador falla, se usan las reglas.

Cada turno informa sus reglas en `anchor_rules`, y `GET /sessions/{id}` cuenta las anclas en `anchors_count`.

### Compresión híbrida: resumen + anclas + ventana

| Estrategia | Qué conserva | Qué arriesga |
|---|---|---|
| Ventana deslizante | Los últimos N turnos | Olvida lo que se pactó al principio |
| Resumen acumulado | Lo antiguo, condensado | Pierde la letra exacta de lo crítico |
| Anclas | Turnos críticos, literales | Si se ancla de más, el prompt crece |
| **Híbrida (la de este servicio)** | **Resumen + anclas + ventana** | Una llamada extra al comprimir |

Lo crítico se queda **literal** (ancla), lo demás se **resume** y lo reciente sigue **vivo** en la ventana. El array que viaja al LLM:

```
[ system                                  ← regenerado con project_metadata
  user: "[Earlier conversation summary]   ← resumen acumulado
         ..."
  turnos anclados (user + assistant)      ← anclas, literales
  turnos de la ventana                    ← los últimos SESSION_MAX_TURNS
  user actual ]
```

- **El resumen entra como `user`**, no como un segundo `system`: ya hay un system que se regenera en cada turno, y un segundo rompería el contrato. El encabezado entre corchetes le indica al modelo que es contexto.
- **`append()` ya no recorta.** Solo agrega el turno; el servicio llama justo después a `apply_compression()` (`app/compression.py`), así queda a la vista **cuándo** se paga la llamada al LLM.
- **Un solo resumen por pasada**, rehecho entero a partir del anterior: nada de resumen de resúmenes.
- **Si solo salen anclas**, se mueven sin llamar al LLM.
- **Si el summarizer falla**, no se recorta: los turnos quedan en la ventana y se reintenta en el próximo turno (log `summary_failed`). No se pierde información.
- `SUMMARY_MODEL` permite resumir con un modelo más barato que `LLM_MODEL`. La llamada aparece en `llm_calls` con `purpose=summary` y su costo.

Demo del curso, reproducida en `tests/test_compression.py` (`max_turns=6`, NDA en el turno 1):

| Turno | `message_count` | `anchors_count` | Resumen | Qué pasa |
|---|---|---|---|---|
| 1-6 | 2 → 12 | 0 | 0 | La ventana se llena |
| 7 | 12 | 2 | 0 | Sale el turno 1: es ancla (`nda`) |
| 8 | 12 | 2 | > 0 | Sale el turno 2: va al resumen |

### Tier dinámico: la misma estimación para audiencias distintas

Un solo prompt (`app/prompts/estimation/v2/system.j2`) con un bloque `<audience>` que cambia según el tier. Es Jinja en lugar de cuatro prompts: un arreglo común se hace una sola vez.

| Tier | Para quién | Cómo cambia la respuesta |
|---|---|---|
| `executive` | Sponsor, dirección | Cifra defendible, riesgos primero, 3-4 fases, sin jerga |
| `pm` | Jefe de proyecto | Hitos, quién entrega qué, máximo 4 fases, confianza prudente |
| `developer` | Tech lead | Stack, integraciones, riesgo técnico por fase, hasta 6-8 fases |
| `default` | Audiencia mixta | Equilibrio entre negocio y técnica, sin ajustes |

**Dinámico, no estático.** Si el cliente fijara el tier, fallaría en conversaciones largas: empieza el comercial y termina el CTO. `resolve_tier` (`app/tiers.py`) lo deduce en cada turno a partir del **texto del turno y del `project_metadata`**, no de las anclas. Gana la primera regla que aplica:

| Regla | Tier | Pista |
|---|---|---|
| `nda_detected` | `executive` | NDA, confidencialidad o embargo en el turno o en `agreed_scope` |
| `regulatory_context` | `executive` | Normativa (GDPR, HIPAA, CCPA...) en el turno, `agreed_scope` o `mentioned_technologies` |
| `technical_audience` | `developer` | El turno nombra al menos `TIER_TECHNICAL_TERMS` (2) términos técnicos distintos |
| `low_budget_pm` | `pm` | `assumed_team_size` es como mucho `TIER_SMALL_TEAM_SIZE` (2): proyecto chico |
| `default` | `default` | Ninguna pista |

Las reglas, el orden y los umbrales son los del curso (se ajustan por `.env`); la lista de términos técnicos y las regex en español son propias. Antes de la cadena, un `override` explícito gana (`explicit_override`), y un predicado que falla se loguea (`tier_rule_predicate_failed`) y se salta. Cada turno devuelve `tier` y `tier_rule`, el log `tier_resolved` los registra, y `GET /sessions/{id}` muestra los del último turno.

**Override humano.** Como en el curso, `POST /api/v1/sessions/{id}/estimate` acepta un campo opcional `tier` (`executive`, `pm`, `developer` o `default`) en el multipart. Si viene, gana sobre el resolver y la regla es `explicit_override`; un valor inválido da 422. Streamlit tiene el selector «Audiencia (tier)»: «Auto» no manda el campo. El `/estimate` sin sesión no lo acepta (tampoco en el curso), así que el tier no necesita estar en la clave del cache. El runner de evals manda `case.tier` cuando el caso no es `auto`.

La estructura sigue al `tier_resolver.py` del curso: enum `Tier`, `ResolutionContext` (transcript, metadata, override opcional) y una lista de `TierRule(name, tier, predicate)`. Un NDA del turno 1 sigue resolviendo `executive` en los turnos siguientes solo si el extractor lo dejó en `agreed_scope`; si no, vuelve a `default` aunque esté anclado. Queda abierta la pregunta de la clase: ¿debería el resolver mirar también las anclas?

Cada familia de prompts se versiona por separado: `PROMPT_VERSION=v2` cambia el prompt de estimación, pero el extractor de metadata y el summarizer siguen en su propia `v1`.

### Debug: `GET /api/v1/sessions/{session_id}`

Expone cómo se está gestionando la memoria de la sesión, sin el contenido de los mensajes:

| Campo | Qué es |
|---|---|
| `total_turns` | Turnos completados desde que se creó la sesión |
| `window_turns` / `max_turns` | Turnos que hoy están en la ventana deslizante y su tamaño máximo |
| `message_count` | Mensajes de la ventana (sin system, resumen ni anclas) |
| `anchors_count` | Mensajes anclados que ya salieron de la ventana y viajan literales |
| `summary_chars` | Largo del resumen acumulado |
| `last_resolved_tier` / `last_tier_rule` | Audiencia resuelta en el último turno y la regla que la eligió |
| `project_metadata` | La memoria estructurada actual |
| `last_llm_calls` | Métricas de las llamadas al LLM del último turno |

### `project_metadata` en el system prompt

En cada turno, el system prompt (`app/prompts/estimation/v1/system.j2`) incluye un bloque con los hechos conocidos del proyecto:

```
<project_metadata>
- Nombre del proyecto: Turnito
- Tamaño de equipo asumido: 3 personas
- Tecnologías mencionadas: Flutter, Stripe
- Alcance acordado: Incluye pagos online, sin app de escritorio.
</project_metadata>
```

En el primer turno de la sesión (o en `POST /api/v1/estimate`, sin sesión) el bloque va vacío. Solo se listan los campos que tienen valor.

### Cómo se actualiza: extractor LLM (decisión)

Después de cada estimación, `app/services/metadata_extractor.py` hace **una segunda llamada al LLM** con su propio prompt (`app/prompts/metadata_extraction/v1/`). Recibe la ficha actual, el contenido del turno (transcripción y adjuntos) y la estimación devuelta, y devuelve la ficha actualizada validada contra `ProjectMetadata` con Instructor.

| Opción | A favor | En contra |
|---|---|---|
| Heurística (regex) | Gratis e instantánea | Frágil en español: "lo hacemos en Flutter", "el front va con React", "somos tres devs"... cada forma de decirlo es otra regex. No distingue un hecho de una hipótesis, ni detecta correcciones ("al final no usamos Stripe") |
| **Extractor LLM (elegida)** | Entiende lenguaje natural y documentos adjuntos, distingue hechos de supuestos y aplica correcciones. Reutiliza Instructor, así que la salida está tipada y validada | Una llamada más por turno: más costo y más latencia |

Por qué el extractor:

- **La memoria es lo que hace útil una sesión:** un hecho mal extraído contamina todos los turnos siguientes. Vale más la precisión que ahorrar una llamada.
- **Los adjuntos:** los hechos suelen estar en el PDF o el Word, en texto libre. Una regex no los encuentra con fiabilidad.
- **Costo acotado:** el extractor recibe poco contexto y devuelve un objeto chico. Con un modelo económico como `claude-haiku-4-5`, el costo extra por turno es bajo.
- **Política de fallo declarada (degradar):** si el extractor falla, se conserva la ficha anterior y se registra `metadata_extraction_failed`. La estimación del usuario no se pierde por un error de la memoria.

La ficha la devuelve completa el modelo, no solo los cambios, para que pueda aplicar correcciones. El prompt le indica conservar los hechos existentes salvo que la interacción los cambie explícitamente, y no convertir en hechos los supuestos de la estimación.

Alternativa evaluada para más adelante: pedir los hechos en la **misma** llamada de la estimación (un `response_model` con `project_facts` y `estimation`). Ahorra la segunda llamada, pero mezcla dos responsabilidades en un mismo prompt.

### Origen de cada hecho (auditabilidad)

Para poder responder "¿por qué el LLM asumió X?", cada cambio de `project_metadata` se registra con su turno y sus fuentes (`MetadataChange` en `app/sessions.py`):

```json
{"turn": 3, "field": "mentioned_technologies", "action": "added", "value": "Stripe",
 "previous": null, "sources": ["mensaje del usuario", "requisitos.pdf"]}
```

- Después de cada extracción, `diff_metadata` compara la ficha anterior con la nueva: `set` (primera vez), `changed` (corregido, con el valor anterior), `added` / `removed` (listas).
- `sources` son las entradas del turno: el mensaje y los adjuntos. Indica de qué turno salió el hecho, no si vino del mensaje o de un adjunto en particular (eso requeriría que el extractor atribuya cada hecho).
- Cada turno devuelve sus cambios en `metadata_changes`, y `GET /sessions/{id}` devuelve el registro completo en `metadata_log`. Streamlit lo muestra en "Origen de cada hecho".

### Estimar con adjuntos

`POST /api/v1/sessions/{session_id}/estimate` recibe `multipart/form-data`:

| Campo | Tipo | Notas |
|---|---|---|
| `transcript` | texto | La transcripción de la reunión (20 a 2000 caracteres) |
| `project_type` | enum | Obligatorio |
| `detail_level` | enum | Opcional, por defecto `medium` |
| `output_format` | enum | Opcional, por defecto `phases_table` |
| `attachments` | archivos | Opcional, PDF o Word (`.docx`), hasta `ATTACHMENT_MAX_FILES` |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/sessions/<session_id>/estimate \
  -F "transcript=El cliente quiere una app de turnos para su cadena de peluquerías." \
  -F "project_type=mobile_app" \
  -F "attachments=@requisitos.pdf" \
  -F "attachments=@minuta.docx"
```

JSON y archivos no pueden viajar en el mismo body, por eso los campos tipados llegan como campos de formulario. FastAPI los valida igual (los enums rechazan valores desconocidos con 422) y el endpoint arma con ellos el mismo `EstimationRequest` de siempre.

### Adjuntos: los dos caminos, elegidos por documento

El servicio implementa **los dos caminos** y un **router** (`app/attachments/router.py`) decide, documento por documento, cuál usar.

| Camino | Herramienta | Cuándo |
|---|---|---|
| B: extracción local | **pypdf** | PDF de texto simple: lo más liviano |
| B: extracción local | **PyMuPDF** (`pymupdf4llm`) | PDF con tablas (las convierte a Markdown) o largo (más de `ATTACHMENT_LARGE_PDF_PAGES` páginas) |
| B: extracción local | **python-docx** | Siempre para Word: las APIs multimodales no aceptan `.docx` |
| A: multimodal | El LLM lee el PDF | PDF casi sin texto (escaneado) o dominado por imágenes y diagramas |

Antes de decidir, `analyze_pdf` (PyMuPDF) mide en una pasada las páginas, el texto por página, las imágenes y las tablas. La decisión (`decide_pdf`) es una función pura, sin I/O, así que se testea con perfiles sintéticos. Cada adjunto informa en la respuesta qué herramienta se usó y por qué (`attachments[].tool` y `attachments[].reason`), y el log `attachment_routed` lo registra.

Si el modelo configurado no acepta PDF, el camino A no está disponible y el router cae a PyMuPDF con un aviso.

**Configuración en `.env`:**

| Variable | Valores | Efecto |
|---|---|---|
| `ATTACHMENT_STRATEGY` | `auto` (por defecto) | El router elige por documento |
| | `local_text` | Siempre camino B (pypdf o PyMuPDF según el documento) |
| | `multimodal` | Los PDFs siempre van por el camino A |
| `ATTACHMENT_MULTIMODAL_MODE` | `inline` (por defecto) | El PDF viaja en base64 dentro del mensaje. Sin estado en el proveedor; se reenvía en cada llamada |
| | `files_api` | El PDF se sube a la Files API del proveedor y el mensaje lleva solo su `file_id`. Se borra al terminar el turno, aunque el turno falle |

Umbrales del router: `ATTACHMENT_MIN_CHARS_PER_PAGE` (debajo = escaneado), `ATTACHMENT_IMAGE_HEAVY_PER_PAGE` y `ATTACHMENT_VISUAL_MAX_CHARS_PER_PAGE` (documento visual), `ATTACHMENT_LARGE_PDF_PAGES` (documento largo).

**Por qué este diseño:** ninguno de los dos caminos es mejor en todos los casos. El camino B es barato, controlable (sabemos qué texto entra y lo recortamos con `ATTACHMENT_MAX_CHARS`), independiente del proveedor y prepara RAG; pero pierde la información visual y no lee escaneados. El camino A entiende imágenes y diagramas, pero consume más tokens y depende del proveedor. El router usa B cuando alcanza y A solo cuando el contenido es visual.

En el camino B el texto va a continuación de la transcripción con un separador (`--- attachment: requisitos.pdf ---`). En el camino A va un bloque `{"type": "file"}` que LiteLLM traduce al formato de cada proveedor (un bloque `document` en Anthropic).

Docling se evaluó para documentos con estructura compleja, pero quedó afuera por su peso (PyTorch y modelos de visión). Si se suma, sería otra herramienta del router, sin tocar el resto.

### Bloques de contenido: cómo conviven los dos caminos

El mensaje `user` no se arma concatenando strings, sino como una **lista de bloques de contenido** en el formato estándar de LiteLLM/OpenAI:

```python
{
    "role": "user",
    "content": [
        {"type": "text", "text": "<prompt con la transcripción>"},
        {
            "type": "text",
            "text": "--- attachment: requisitos.pdf ---\n<texto>",
        },  # camino B
        {"type": "file", "file": {"file_id": "file_abc123"}},  # camino A
    ],
}
```

| Pieza | Rol |
|---|---|
| `AttachmentProcessor` (`app/attachments/__init__.py`) | Interfaz: recibe el archivo crudo y devuelve `content_parts` |
| `LocalTextExtractor` | Camino B: devuelve un bloque `text` con el separador |
| `build_user_content` | Arma el mensaje `user` con el prompt y los bloques de cada adjunto, sin saber de qué camino vienen |
| `generate_structured_from_messages` (`llm_wrapper`) | Recibe el array `messages` completo, con `content` como string o como lista de bloques |
| `ATTACHMENT_STRATEGY` | Elige el procesador |

## Actor-Critic-Boss: auditar en vez de inventar

`POST /api/v1/sessions/{id}/estimate-acb` tiene el mismo contrato multipart que `/estimate` (incluido el `tier` opcional), pero la estimación pasa por tres roles con separación estricta (patrón de «Building Effective Agents», Anthropic):

| Rol | Qué hace | Dónde |
|---|---|---|
| **Actor** | Produce un borrador; no decide si vale | El mismo pipeline de `/estimate` |
| **Critic** | Audita y señala defectos; no reescribe | `app/services/critic.py`, `app/prompts/critic/v1/` |
| **Boss** | Decide qué hacer con la auditoría | `app/services/boss.py` |

Evita los dos fallos del patrón: el **bucle infinito** (el crítico siempre encuentra algo; el Boss pone el límite `BOSS_MAX_ITERATIONS=3` y el plan B) y el **sesgo de confirmación** (con crítica en texto libre el actor se justifica; con crítica estructurada sabe qué campo rehacer).

**Actor: la pieza que ya teníamos, con otro contrato.** No hay código nuevo de actor. El prompt `estimation/v3` es la v2 más un bloque `<critic_feedback>` en `user.j2` que Jinja agrega solo cuando hay feedback: cero ramas en Python. Orden del turno: guardrail de entrada → tier → prompt → LLM → guardrail de salida (en cada borrador) → metadata → historial + compresión. A la sesión llega solo el resultado final: los borradores son de usar y tirar.

**Critic: feedback estructurado, nunca texto libre.** `CriticFeedback` (`app/schemas/critic.py`) tiene un veredicto (`accept`, `needs_iteration`, `reject`) e issues con `category` (7: `math_error`, `hallucination`, `scope_mismatch`, `phase_imbalance`, `missing_assumption`, `unrealistic_estimate`, `tier_mismatch`), `severity`, `field_path` (`phases[2].cost_eur`) y `suggested_fix`. Iterar exige al menos un issue critical o major: por detalles menores no se gasta otra llamada. Si el crítico falla, devuelve `accept` con `confidence_in_review=0` y el log `critic_failed_fallback_accept` lo delata. `CRITIC_MODEL` permite un modelo distinto (vacío = `LLM_MODEL`).

**Boss: tres salidas.**

| Crítico dice | Rondas | Boss decide | Qué pasa |
|---|---|---|---|
| accept | — | accept | Devuelve el borrador |
| reject | — | synthesize | No tiene arreglo con este actor |
| needs_iteration | quedan | iterate | Vuelve al actor con el feedback |
| needs_iteration | no quedan | synthesize | Lo intentó y no convergió |

Es stateless y recibe **funciones** (actor y crítico), así los tests inyectan falsos sin mocks. *Synthesize* no tira el trabajo: devuelve el último borrador con «⚠ Advertencias abiertas…» delante y la confianza a la mitad, con piso en `LOW_CONFIDENCE_PCT` (40) para no caer en «Out of scope».

La respuesta trae `acb` (`final_decision`, `iterations_run` y por ronda el veredicto, la confianza y los issues) y `llm_calls` con cada llamada del actor (`estimation`) y del crítico (`critic`). Streamlit tiene el selector «Modo de estimación» y muestra la traza desplegable bajo el resultado. `python -m evals.run --mode acb` corre el dataset por este camino y guarda `evals/acb.json`.

**Trade-offs.** Cuesta 3-7 llamadas por turno en lugar de 2. Conviene para clientes importantes, proyectos con NDA o normativa, o cualquier agente que escribe y puede contradecirse; no para estimaciones rápidas, conversaciones de 1-2 turnos o cuando el costo por llamada pesa más que la mejora.

Diferencias con el curso: las respuestas de ambos endpoints comparten `SessionEstimationResponse` (con `acb` vacío en modo actor) en lugar de un `ACBResponse` aparte; el piso de confianza es nuestro umbral (40, el curso usa 30); y si el borrador es «Out of scope», las advertencias van detrás del prefijo y la confianza no se toca.

## Evals: golden dataset

`evals/golden_dataset.json` tiene 16 casos que se evalúan como una suite de tests. Es el dataset del curso (rama `session_05_live`): casos felices de los cuatro tipos de proyecto, dos fuera de scope (pedido vago y adversarial), casos con NDA, HIPAA y GDPR que esperan tier `executive`, uno de equipo chico (`pm`), uno de microservicios (`developer`) y un borde con deadline apretado.

```json
{
  "id": "case-001-web-saas-crm",
  "transcript": "We want a B2B CRM called Nimbus for the sales team. ...",
  "project_type": "web_saas",
  "expected_in_summary": ["CRM"],
  "expected_technologies_any_of": ["React", "Postgres"],
  "expected_phase_count_range": [3, 6],
  "expected_cost_range_eur": [15000, 80000],
  "expected_duration_weeks_range": [4, 16]
}
```

**Rangos, no números exactos.** El modelo varía de forma legítima entre ejecuciones; el caso detecta lo que se sale de proporción, no cada euro.

Tres reglas de oro:

- El dataset vive comiteado: los cambios se ven en un diff.
- Métricas deterministas siempre que se pueda.
- Un juez LLM complementa, nunca es el primer escalón.

`evals/dataset.py` valida cada caso con Pydantic (`GoldenCase`) y `load_dataset()` los carga. Las expectativas son opcionales: un caso solo evalúa lo que declara (`expected_out_of_scope`, `expected_tier`, rangos de fases, costo y semanas, términos y tecnologías). `tests/test_golden_dataset.py` lo corre en CI sin llamar al LLM.

Único cambio propio sobre el dataset del curso: en `expected_in_summary`, un término `"a|b"` acepta cualquiera de las alternativas. Nuestro prompt hace que el modelo responda en español y el dataset espera términos en inglés; en la primera corrida, los casos 002, 008, 010 y 011 fallaban solo por eso (`mobile`, `patient`, `booking`, `microservices`).

### Tres métricas binarias y deterministas

`evals/metrics.py` (portado del curso) evalúa cada par (caso, estimación) sin LLM y dice por qué falló:

| Métrica | Qué verifica |
|---|---|
| `schema_adherence` | Las fases suman el total, hay prefijo `Out of scope:` si la confianza es baja, la cantidad de fases está en rango |
| `cost_bounds` | Costo y semanas en rango; un caso fuera de scope sale declarado como tal |
| `content_recall` | Aparecen los términos esperados y al menos una de las tecnologías |

`schema_adherence` repite lo que ya garantizan Instructor y los validadores a propósito: si un cambio borra un validador, la eval lo detecta. Una diferencia con el curso: allí un caso fuera de scope espera costo 0 y 1 semana; nuestro contrato pide el prefijo `Out of scope:` y una única fase de relevamiento, y eso es lo que verifica `cost_bounds`. El umbral de confianza baja es el del proyecto (`LOW_CONFIDENCE_PCT = 40`; el curso usa 30).

### Correr las evals

`evals/run.py` corre el dataset contra el servicio y puntúa cada caso con las tres métricas. Por defecto usa `TestClient` en el mismo proceso (no hace falta levantar el servidor); con `--http` apunta a uno real.

```bash
uv run python -m evals.run --mode actor --limit 8
```

**Llama al LLM real y tiene costo.** Cada caso usa una sesión nueva para que no se contaminen. Imprime una fila por caso y un resumen por métrica, y guarda el reporte en `evals/actor.json` (latencia, métricas, tier resuelto contra `expected_tier`, llamadas y costo en USD) para hacer diff entre corridas. Un caso que falla (502, timeout) se registra como error y la corrida sigue. Sale con código 1 si algún caso no pasa todo.

Sigue al runner del curso con dos diferencias: un rechazo del guardrail de entrada en un caso que espera fuera de scope se puntúa como respuesta `Out of scope:` con nuestro contrato (en el curso, costo 0 / 1 semana), y el tier se registra pero no cuenta para pasar o fallar.

## Stress test del CAG (Sesión 6, ejercicio)

Mide con números dónde se rompe el CAG: ventana de contexto, costo, latencia o *lost in the middle*. Esos números justifican el paso a RAG del módulo 3.

**Telemetría por turno.** Cada respuesta de `POST /sessions/{id}/estimate` (y `estimate-acb`) trae `observation` (`TurnObservation`): turno, caracteres del mensaje y de los adjuntos, mensajes en la ventana, anclas, largo del resumen, tokens, latencia, costo, cantidad de llamadas y tier. El log `turn_observed` registra lo mismo. `tokens_in`, `tokens_out` y `latency_ms` son de la llamada de estimación (la que recibe todo el contexto); `cost_usd` suma todas las llamadas del turno. `GET /sessions/{id}` devuelve además el texto del resumen.

**Escenarios** (`evals/stress/scenarios.py`, del curso): tres conversaciones de 20 turnos en una misma sesión.

| Escenario | Qué estresa |
|---|---|
| `growing` | Los requisitos crecen; ¿el nombre del turno 1 sobrevive a la ventana? |
| `pivot` | En el turno 5 el stack pasa de React a Flutter; ¿la metadata pierde React? |
| `contradiction` | Turno 3: presupuesto 30k; turno 8: 80k. ¿Qué versión queda? |

**Métricas** (`evals/stress/metrics.py`, deterministas): `latency_budget` (latencia del turno contra un SLA, 8000 ms), `cost_budget` (costo del turno contra USD 0,02) y `memory_drift` (¿el dato del turno 1 sigue en el snapshot de la sesión? Búsqueda de texto acotada a un campo; el log de cambios de la metadata no cuenta, porque guarda valores que ya no llegan al prompt).

**Adjuntos de prueba.** `evals/stress/fixtures/build_pdfs.py` (con `fpdf2`, dependencia de desarrollo) genera PDFs de 5, 20, 50 y 100 KB de texto. Con nuestro tope de 20.000 caracteres por adjunto, los de 50 y 100 KB llegan truncados al largo del de 20 KB.

**Runner** (`evals/stress/run.py`): escenarios × tamaños × repeticiones, una fila de CSV por turno (`evals/stress/results.csv`, no se commitea) y un resumen con P50/P95, costo y % de drift. **Llama al LLM real**: `--max-total-usd` (por defecto 5) corta la corrida si el gasto acumulado lo supera. Con el CSV se completa `evals/stress/REPORT.md`, que tiene los comandos para cada tamaño de corrida.

## Sesión 6: Data-driven AI

Preparar el corpus antes de vectorizar: decidir (CAG, RAG o híbrido) → catalogar → ingerir → limpiar → proteger (PII y GDPR). Hoy no se vectoriza nada: se preparan los datos para que merezcan serlo. Referencia: rama `session_06_live` del curso.

### Pre-flight: el stack sano antes de construir

```bash
uv run python -m scripts.preflight_s06
```

`scripts/preflight_s06.py` corre una comprobación por función y sale con código 1 si alguna falla, para no descubrir el problema a mitad de una demo:

| Comprobación | Qué verifica |
|---|---|
| Versión de Python | 3.11 o más |
| Paquetes requeridos | pandas, Pandera, PyYAML, openpyxl, Presidio, spaCy, Faker y el modelo `es_core_news_md`, con versión mínima |
| spaCy `es_core_news_md` | Que el modelo en español reconozca «Laura Fernández» como persona |
| Corpus de ejemplo | Que `data/seed/` tenga JSON, TXT y XLSX, y que los textos sean UTF-8 |
| Catálogo válido | Que `data/catalog/catalog.yaml` valide |
| Estimador (health) | Que el servicio responda en `ESTIMATOR_API_URL` |
| Almacén de seudónimos | Que el mismo valor devuelva el mismo seudónimo |

**La más importante es la de spaCy.** Presidio detecta personas con spaCy y por defecto usa el modelo en inglés: sobre nombres hispanos devuelve cero personas **sin avisar**, y la pseudonimización parecería funcionar con los nombres en claro. Por eso `es_core_news_md` es una dependencia fija del proyecto (el wheel oficial de explosion), no un paso manual.

Las piezas que todavía no existen salen como ⏳ pendientes, con el paso que las crea, y no cuentan como falla: el pre-flight crece con la sesión. Errores típicos que cubre o anticipa: archivos en Latin-1 en lugar de UTF-8, el modelo de spaCy ausente y las versiones viejas de Pandera (se importa como `pandera.pandas`).

Diferencias con el curso: no instalamos `unstructured` (cientos de MB; los PDFs ya los resuelven pypdf y PyMuPDF) ni Postgres (la tabla de seudónimos vive en memoria por ahora); el health check es `GET /`.

### Paso 2.1 · ¿CAG, RAG o híbrido?

```bash
uv run python -m app.ingestion.architecture                       # con nuestro modelo
uv run python -m app.ingestion.architecture --model gpt-4o-mini   # con el del curso
```

`app/ingestion/architecture.py` (portado del curso) convierte la decisión en código que se ejecuta. No forma parte de la API: es un análisis que se corre **antes** de ingerir nada.

**Las cuatro restricciones del CAG son un AND** (`CAGViability.viable` es un `all([...])`): con una sola en rojo, el CAG no es viable.

| Restricción | Cuándo está en verde |
|---|---|
| Ventana de contexto | El corpus entra en el 70% de la ventana (el resto es para system, historial y respuesta) |
| Costo | El modelo tiene caché de prefijo y el corpus cambia como mucho una vez por semana |
| Latencia | El corpus tiene 100.000 tokens o menos |
| Lost in the middle | Ídem: por encima, el modelo atiende peor lo que queda en el centro |

**Los cuatro ejes de la recomendación** (`recommend_architecture`): volumen (¿cabe?), frecuencia de refresco (¿es estable?), trazabilidad (¿hay que citar la fuente?) y control de acceso (¿hay que filtrar por usuario?). Si se cumplen los cuatro → `CAG`. Si cabe y es estable pero hay que citar o filtrar → `Hybrid` (lo estable por CAG, lo volátil o sensible por RAG). Si no → `RAG`.

**Resultado para el Proyecto 2 del curso** (250.000 tokens, refresco semanal, trazabilidad y control de acceso obligatorios): **RAG**, con tres restricciones en rojo (ventana, latencia y lost in the middle). Pasa igual con los 200.000 tokens de Haiku 4.5: la ventana útil es de 140.000.

El fine-tuning no está en el árbol: enseña *cómo* responder, no aporta datos ni trazabilidad. Diferencia con el curso: sumamos el perfil de nuestro modelo (`claude-haiku-4-5`) y el CLI elige el modelo con `--model`.

### Paso 2.2 · Catálogo de fuentes

**Antipatrón: vectorizar primero, mirar después.** Los errores no salen el día 1, salen el día 60: versiones mezcladas (la propuesta inicial y la firmada conviven y se recupera la equivocada), fuentes podridas (tarifas viejas que dan respuestas seguras y falsas) y huecos donde el modelo inventa algo plausible. Las tres causas tienen la misma raíz: nadie miró lo que había. Por eso se empieza por el **inventario**.

**Datos de ejemplo** (`data/seed/`, copiados del curso, con suciedad a propósito): 6 presupuestos JSON en `budgets/` (dos versiones del mismo, monedas `eur`/`EUR`, fechas en dos formatos, un monto negativo), 3 transcripciones en `transcripts/` (una de 2023 sin etiquetas de hablante) y el Excel de tarifas `rate_card_2024.xlsx`.

**Inspeccionar: hechos antes que opiniones.**

```bash
uv run python -m app.ingestion.catalog.inspect data/seed
```

`inspect.py` cuenta archivos, tamaño, formatos y la última modificación de cada carpeta. No puntúa la calidad: el censo mide lo medible y la calidad la decide una persona. Limitación: la fecha de modificación es la del sistema de archivos y git no la conserva (un clon nuevo muestra la del checkout), así que es una pista y no una prueba; lo observado queda escrito en `refresh_observed`.

**El catálogo es código, no documentación.** `data/catalog/catalog.yaml` se carga con `yaml.safe_load` y se valida con Pydantic (`app/ingestion/catalog/models.py`):

```bash
uv run python -m app.ingestion.catalog.loader data/catalog/catalog.yaml
```

| Fuente | Decisión | Motivo |
|---|---|---|
| `presupuestos_json` | **include** | Fuente principal; tiene PII, así que se seudonimiza (paso 2.5) |
| `transcripciones_txt` | **review** | Un archivo de 2023 sin etiquetas de hablante rompe el parser por defecto |
| `rate_card_xlsx` | **exclude** | `actuality=1`: tarifas de enero de 2024 |

Reglas del contrato: tres decisiones (`review` es la tercera vía, para no colapsar todo a sí o no); si no es `include`, `decision_reason` es obligatorio; nombres en snake_case y únicos (son el identificador del endpoint de ingesta); `extra="forbid"`. La calidad tiene cuatro dimensiones de 1 a 5 (completitud, consistencia, actualidad, fiabilidad) que **no se promedian**: completitud 5 y fiabilidad 1 no es «calidad 3», son datos completos que pueden ser mentira. Excluir con el motivo escrito es disciplina, no desidia: el `git log` del YAML dice quién decidió qué y por qué.

## Estructura

```
estimador-cag/
├── app/
│   ├── main.py                       # App FastAPI y middleware de logs HTTP
│   ├── config.py                     # Configuración leída desde .env
│   ├── logging_config.py             # Configuración de structlog
│   ├── schemas/
│   │   └── estimation.py             # Contrato Pydantic: request, result, response y enums
│   ├── routers/
│   │   └── estimations.py            # Endpoint POST /api/v1/estimate
│   ├── prompts/
│   │   ├── loader.py                 # Renderiza los prompts Jinja2 por versión
│   │   └── estimation/v1/            # Plantillas system.j2, user.j2 y examples.j2
│   ├── services/
│   │   ├── estimation.py             # Caso de uso: pipeline de 6 pasos con sus políticas
│   │   └── llm_wrapper.py            # Instructor + LiteLLM: la llamada al modelo
│   ├── guardrails/
│   │   └── output.py                 # enforce_scope_response (política filter)
│   └── cache/
│       └── exact.py                  # Caché exact-match (LRU en memoria)
├── streamlit_app.py                  # Cliente: formulario y visualización de los datos
├── tests/                            # Tests de schemas, prompts, servicio, API, cliente y estructura
├── .github/workflows/ci.yml          # Pipeline de CI
├── .env.example                      # Plantilla de variables de entorno
└── pyproject.toml
```

## Requisitos

- Python 3.11 o superior
- [uv](https://docs.astral.sh/uv/) para gestionar dependencias
- Una API key de Anthropic y/o de OpenAI (solo hace falta la del proveedor del modelo elegido)

## Instalación

1. Clonar el repositorio y entrar a la carpeta:

   ```bash
   git clone https://github.com/sebasenscloud/estimador-cag.git
   cd estimador-cag
   ```

2. Instalar las dependencias (crea el entorno virtual `.venv`):

   ```bash
   uv sync
   ```

3. Crear el archivo `.env` a partir de la plantilla:

   ```bash
   cp .env.example .env
   ```

4. Editar `.env` y completar la API key del proveedor que vayas a usar.

Las API keys se leen siempre desde `.env`. Nunca están escritas en el código, y el archivo `.env` está excluido de git.

### Variables de entorno

| Variable | Descripción | Ejemplo |
|---|---|---|
| `LLM_MODEL` | Modelo en formato LiteLLM `<proveedor>/<modelo>` | `anthropic/claude-haiku-4-5` / `openai/gpt-4o-mini` |
| `LLM_MAX_RETRIES` | Reintentos de Instructor si la respuesta no cumple el schema | `3` |
| `ANTHROPIC_API_KEY` | API key de Anthropic | `sk-ant-...` |
| `OPENAI_API_KEY` | API key de OpenAI | `sk-...` |
| `APP_ENV` | Entorno; con `production` los logs salen en JSON | `development` |
| `LOG_LEVEL` | Nivel de logs | `info` |
| `MAX_TOKENS` | Largo máximo de la respuesta | `16000` |
| `CACHE_MAX_ENTRIES` | Respuestas que guarda la caché | `128` |
| `ANCHOR_DETECTION_MODE` | Detección de anclas: `heuristic` (8 reglas) o `llm` (clasificador) | `heuristic` |
| `SUMMARY_MODEL` | Modelo para el resumen acumulado (vacío = `LLM_MODEL`) | `anthropic/claude-haiku-4-5` |
| `SUMMARY_MAX_CHARS` | Largo máximo que se le pide al resumen | `1500` |
| `MAX_ANCHORS` | Turnos anclados que se conservan fuera de la ventana | `5` |
| `SESSION_MAX_TURNS` | Turnos que conserva la ventana deslizante de cada sesión | `6` |
| `ATTACHMENT_STRATEGY` | Camino para procesar adjuntos (`local_text` = B) | `local_text` |
| `ATTACHMENT_MAX_FILES` | Adjuntos por petición | `5` |
| `ATTACHMENT_MAX_BYTES` | Tamaño máximo por adjunto | `10485760` |
| `ATTACHMENT_MAX_CHARS` | Caracteres de texto extraídos por adjunto | `20000` |
| `SCOPE_MAX_WEEKS` | Duración máxima antes de considerar el pedido fuera de scope | `104` |
| `PROMPT_VERSION` | Versión del prompt de estimación (`v2` agrega `<audience>`; `v3` suma `<critic_feedback>`) | `v3` |
| `CRITIC_MODEL` | Modelo del crítico del ACB (vacío = `LLM_MODEL`) | vacío |
| `BOSS_MAX_ITERATIONS` | Rondas máximas del ACB (cada una = actor + crítico) | `3` |
| `MODERATION_ENABLED` | Capa de moderación del guardrail de entrada (necesita `OPENAI_API_KEY`) | `true` |
| `MODERATION_MODEL` | Modelo de moderación de OpenAI | `omni-moderation-latest` |
| `TIER_TECHNICAL_TERMS` | Términos técnicos distintos para resolver `developer` | `2` |
| `TIER_SMALL_TEAM_SIZE` | Equipo máximo para resolver `pm` | `2` |
| `ESTIMATOR_API_URL` | Dirección del servicio IA que usa el cliente | `http://127.0.0.1:8000` |
| `ESTIMATOR_API_TIMEOUT` | Segundos que el cliente espera la respuesta | `300` |

### Cambiar de proveedor

Cambiá una sola línea en `.env` y reiniciá el servicio IA:

```
LLM_MODEL=openai/gpt-4o-mini
```

LiteLLM detecta el proveedor por el prefijo y el servicio usa la API key que corresponde. Instructor usa *tool calling* para obtener la salida estructurada; los modelos sugeridos (`claude-haiku-4-5` y `gpt-4o-mini`) lo soportan.

## Uso

Hacen falta **dos terminales**, una para cada pieza.

### 1. Servicio IA (FastAPI)

```bash
uv run uvicorn app.main:app --reload
```

La documentación interactiva queda disponible en http://127.0.0.1:8000/docs.

### 2. Cliente (Streamlit)

En otra terminal:

```bash
uv run streamlit run streamlit_app.py
```

Se abre en el navegador en http://localhost:8501.

El cliente trabaja **por sesiones**:

- Al cargar la página crea una sesión (`POST /api/v1/sessions`) y guarda el `session_id` en `st.session_state`.
- Escribí la transcripción o un mensaje, adjuntá PDFs o Word si querés (selector múltiple), elegí tipo, nivel de detalle y formato, y tocá **Enviar**. Cada envío es un turno de `POST /api/v1/sessions/{session_id}/estimate`.
- La conversación se muestra como chat: tus mensajes (con sus adjuntos) y cada estimación como tabla, lista o narrativa.
- El panel lateral muestra la **memoria del proyecto** (`project_metadata`), separada de la conversación (el historial), y cuánto texto se extrajo de cada adjunto.
- **Nueva conversación** crea otra sesión y resetea el estado.
- Si el servicio se reinicia, la sesión se pierde (vive en memoria del proceso) y el cliente lo avisa.

El endpoint de streaming parcial (`POST /api/v1/estimate/stream`) sigue disponible en la API, pero el cliente ya no lo usa: los turnos de sesión devuelven la respuesta completa.

### Llamar al servicio directamente

```bash
curl -X POST http://127.0.0.1:8000/api/v1/estimate \
  -H "Content-Type: application/json" \
  -d '{"description": "App de reservas de turnos con login, calendario y notificaciones.", "project_type": "mobile_app", "detail_level": "medium", "output_format": "phases_table"}'
```

Respuesta (abreviada):

```json
{
  "result": {
    "summary": "App de reservas multiplataforma...",
    "phases": [
      {"name": "Discovery", "description": "...", "duration_weeks": 2, "cost_eur": 5000, "confidence_pct": 80},
      {"name": "Build", "description": "...", "duration_weeks": 8, "cost_eur": 32000, "confidence_pct": 70}
    ],
    "total_duration_weeks": 10,
    "total_cost_eur": 37000,
    "confidence_pct": 72
  },
  "prompt_version": "v1",
  "cached": false
}
```

Cada estimación que no sale de la caché hace una llamada real al LLM (más los reintentos, si los hay) y consume crédito de la API key.

### Logs

Los logs aparecen en la terminal del servicio IA. Ejemplo:

```
2026-11-05T13:20:07Z [info] llm_request_completed  cache_hit=False detail_level=medium duration_ms=6123 input_tokens=2240 model=anthropic/claude-haiku-4-5 output_format=phases_table output_tokens=480 project_type=mobile_app prompt_version=v1
```

Con `APP_ENV=production` se emiten en formato JSON. El texto de las descripciones nunca se registra.

## Tests y calidad de código

```bash
uv run ruff check .          # Linter
uv run ruff format --check . # Formato
uv run pytest -v             # Tests
```

Los tests no llaman a ningún LLM ni levantan servidores: el cliente de Instructor y las respuestas HTTP se reemplazan por mocks, así que corren sin API keys reales y sin costo.

## Integración continua

El workflow [`.github/workflows/ci.yml`](.github/workflows/ci.yml) corre automáticamente en cada push a `main` y en cada Pull Request:

1. Linter (`ruff check`)
2. Verificación de formato (`ruff format --check`)
3. Tests (`pytest`)
