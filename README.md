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

- Escribí la descripción del proyecto, elegí el tipo, el nivel de detalle y el formato, y tocá **Estimar**.
- Mientras el modelo genera, se ven el resumen y las fases a medida que llegan (streaming parcial). Al terminar, se muestran la duración total, el costo total y la confianza, y debajo las fases como tabla, lista o narrativa según el formato elegido.
- Si el pedido queda fuera de scope, se muestra un aviso con el motivo en lugar de los números.
- El panel lateral muestra la versión del prompt, el tiempo, si la respuesta salió de la caché y el JSON enviado al servicio.

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
