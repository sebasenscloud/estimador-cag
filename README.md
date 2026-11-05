# Estimador CAG

Estimador de proyectos de software basado en **CAG (Cache-Augmented Generation)**. A partir de la descripción de un proyecto, genera una estimación de esfuerzo en horas, con el nivel de detalle y el formato que elijas.

El contexto (estimaciones de ejemplo) se inyecta directamente en el system prompt de cada llamada al LLM, sin base de datos ni búsqueda vectorial.

El proyecto tiene dos piezas:

- **Servicio IA** (FastAPI): expone `POST /api/v1/estimate` y es el único que habla con el LLM.
- **Cliente** (Streamlit): un formulario que arma un `EstimationRequest` tipado y lo envía al servicio por HTTP.

```
Navegador → Streamlit (formulario) → POST /api/v1/estimate → FastAPI → LLM (Anthropic u OpenAI)
```

## Características

- **Contrato tipado con Pydantic v2** (`app/schemas.py`): `EstimationRequest` y `EstimationResponse`, con enums para el tipo de proyecto, el nivel de detalle y el formato de salida. El cliente reutiliza las mismas clases.
- **Wrapper de proveedores:** una interfaz común (`LLMProvider`) con implementaciones para **Anthropic** y **OpenAI**. El proveedor se elige con `LLM_PROVIDER`, sin tocar código.
- **Caching exact-match:** si llega exactamente la misma solicitud (descripción y opciones), con el mismo proveedor, modelo y prompt, la respuesta sale de una caché en memoria (LRU) sin llamar al LLM.
- **Observabilidad con structlog:** se registra cada llamada al LLM (proveedor, modelo, versión del prompt, opciones elegidas, tokens, duración, acierto de caché, errores) y cada request HTTP de la API.
- **Prompt como f-string versionado:** `build_system_prompt()` y `build_user_message()` en `app/services/llm_service.py`. `PROMPT_VERSION` se devuelve en cada respuesta.

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
| `text` | texto | La estimación generada (texto libre en Markdown) |
| `prompt_version` | texto | Versión del prompt que la generó |

Si el JSON no cumple el contrato, el servicio responde **422** con el detalle del error.

## Estructura

```
estimador-cag/
├── app/
│   ├── main.py                       # App FastAPI y middleware de logs HTTP
│   ├── config.py                     # Configuración leída desde .env
│   ├── schemas.py                    # Contrato Pydantic: request, response y enums
│   ├── logging_config.py             # Configuración de structlog
│   ├── routers/
│   │   └── estimations.py            # Endpoint POST /api/v1/estimate
│   ├── services/
│   │   ├── llm_service.py            # Prompts, versión, caché y logs
│   │   ├── cache.py                  # Caché exact-match (LRU en memoria)
│   │   └── providers/
│   │       ├── __init__.py           # Fábrica: elige el proveedor según LLM_PROVIDER
│   │       ├── base.py               # Interfaz LLMProvider, LLMResult y errores
│   │       ├── anthropic_provider.py # Implementación con el SDK de Anthropic
│   │       └── openai_provider.py    # Implementación con el SDK de OpenAI
│   └── context/
│       └── examples.py               # Estimaciones de ejemplo (contexto CAG)
├── streamlit_app.py                  # Cliente: formulario que llama al servicio IA
├── tests/                            # Tests de estructura, API, servicio, proveedores y cliente
├── .github/workflows/ci.yml          # Pipeline de CI
├── .env.example                      # Plantilla de variables de entorno
└── pyproject.toml
```

## Requisitos

- Python 3.11 o superior
- [uv](https://docs.astral.sh/uv/) para gestionar dependencias
- Una API key de Anthropic y/o de OpenAI (solo hace falta la del proveedor activo)

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
| `LLM_PROVIDER` | Proveedor activo: `anthropic` u `openai` | `anthropic` |
| `LLM_MODEL` | Modelo del proveedor activo | `claude-opus-5-5` / `gpt-4o-mini` |
| `ANTHROPIC_API_KEY` | API key de Anthropic | `sk-ant-...` |
| `OPENAI_API_KEY` | API key de OpenAI | `sk-...` |
| `APP_ENV` | Entorno; con `production` los logs salen en JSON | `development` |
| `LOG_LEVEL` | Nivel de logs | `info` |
| `MAX_TOKENS` | Largo máximo de la respuesta | `16000` |
| `CACHE_MAX_ENTRIES` | Respuestas que guarda la caché | `128` |
| `ESTIMATOR_API_URL` | Dirección del servicio IA que usa el cliente | `http://127.0.0.1:8000` |
| `ESTIMATOR_API_TIMEOUT` | Segundos que el cliente espera la respuesta | `300` |

### Cambiar de proveedor

Para usar OpenAI, cambiá estas dos líneas en `.env` y reiniciá el servicio IA:

```
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
```

`LLM_MODEL` tiene que ser un modelo del proveedor elegido.

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

O, con el entorno virtual activado:

```bash
source .venv/bin/activate
streamlit run streamlit_app.py
```

Se abre en el navegador en http://localhost:8501.

- Escribí la descripción del proyecto, elegí el tipo, el nivel de detalle y el formato, y tocá **Estimar**.
- El cliente valida el formulario con el mismo `EstimationRequest` del servicio antes de enviarlo.
- El panel lateral muestra la versión del prompt, el tiempo de respuesta, los parámetros enviados y el JSON exacto que viajó al servicio.
- Si el servicio IA no está corriendo, el cliente lo avisa con el comando para iniciarlo.

### Llamar al servicio directamente

```bash
curl -X POST http://127.0.0.1:8000/api/v1/estimate \
  -H "Content-Type: application/json" \
  -d '{"description": "App de reservas de turnos con login, calendario y notificaciones.", "project_type": "mobile_app", "detail_level": "medium", "output_format": "phases_table"}'
```

Respuesta:

```json
{
  "text": "## Estimación: ...",
  "prompt_version": "v2"
}
```

Cada estimación que no sale de la caché hace una llamada real al LLM y consume crédito de la API key.

### Logs

Los logs aparecen en la terminal del servicio IA. Ejemplo:

```
2026-11-05T13:20:07Z [info] llm_request_completed  cache_hit=False detail_level=medium duration_ms=8123 input_tokens=1240 model=claude-opus-5-5 output_format=phases_table output_tokens=580 project_type=mobile_app prompt_version=v2 provider=anthropic streaming=False
```

Con `APP_ENV=production` se emiten en formato JSON, listos para un sistema de logs. El texto de las descripciones nunca se registra.

## Tests y calidad de código

```bash
uv run ruff check .          # Linter
uv run ruff format --check . # Formato
uv run pytest -v             # Tests
```

Los tests no llaman a ningún LLM ni levantan servidores: los proveedores y las respuestas HTTP se reemplazan por mocks, así que corren sin API keys reales y sin costo. Incluyen tests del formulario de Streamlit con `streamlit.testing`.

## Integración continua

El workflow [`.github/workflows/ci.yml`](.github/workflows/ci.yml) corre automáticamente en cada push a `main` y en cada Pull Request:

1. Linter (`ruff check`)
2. Verificación de formato (`ruff format --check`)
3. Tests de estructura, API, servicio, proveedores y cliente (`pytest`)
