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
| `SESSION_MAX_TURNS` | Turnos que conserva la ventana deslizante de cada sesión | `6` |
| `ATTACHMENT_STRATEGY` | Camino para procesar adjuntos (`local_text` = B) | `local_text` |
| `ATTACHMENT_MAX_FILES` | Adjuntos por petición | `5` |
| `ATTACHMENT_MAX_BYTES` | Tamaño máximo por adjunto | `10485760` |
| `ATTACHMENT_MAX_CHARS` | Caracteres de texto extraídos por adjunto | `20000` |
| `SCOPE_MAX_WEEKS` | Duración máxima antes de considerar el pedido fuera de scope | `104` |
| `PROMPT_VERSION` | Versión de las plantillas en `app/prompts/estimation/` | `v1` |
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
