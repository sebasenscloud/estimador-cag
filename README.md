# Estimador CAG

Estimador de proyectos de software basado en **CAG (Cache-Augmented Generation)**. A partir de la transcripción de una reunión con un cliente, genera una estimación de esfuerzo: desglose de tareas con horas, total estimado, equipo recomendado y duración.

El contexto (estimaciones de ejemplo) se inyecta directamente en el system prompt de cada llamada al LLM, sin base de datos ni búsqueda vectorial.

El proyecto ofrece dos formas de uso:

- **API REST** con FastAPI: `POST /api/v1/estimate`
- **Interfaz de chat** con Streamlit: textarea con botón "Enviar", respuestas en streaming y un panel lateral con el contexto CAG

## Características

- **Wrapper de proveedores:** una interfaz común (`LLMProvider`) con implementaciones para **Anthropic** y **OpenAI**. El proveedor se elige con `LLM_PROVIDER`, sin tocar código.
- **Caching exact-match:** si llega exactamente la misma transcripción, con el mismo proveedor, modelo y system prompt, la respuesta sale de una caché en memoria (LRU) sin llamar al LLM.
- **Streaming:** la interfaz muestra la estimación token a token.
- **Observabilidad con structlog:** se registra cada llamada al LLM (proveedor, modelo, tokens, duración, acierto de caché, errores) y cada request HTTP de la API.
- **Prompt como f-string:** `build_system_prompt()` en `app/services/llm_service.py` arma las instrucciones y los ejemplos de contexto.

## Estructura

```
estimador-cag/
├── app/
│   ├── main.py                       # App FastAPI y middleware de logs HTTP
│   ├── config.py                     # Configuración leída desde .env
│   ├── logging_config.py             # Configuración de structlog
│   ├── routers/
│   │   └── estimations.py            # Endpoint POST /api/v1/estimate
│   ├── services/
│   │   ├── llm_service.py            # System prompt, caché, logs y streaming
│   │   ├── cache.py                  # Caché exact-match (LRU en memoria)
│   │   └── providers/
│   │       ├── __init__.py           # Fábrica: elige el proveedor según LLM_PROVIDER
│   │       ├── base.py               # Interfaz LLMProvider, LLMResult y errores
│   │       ├── anthropic_provider.py # Implementación con el SDK de Anthropic
│   │       └── openai_provider.py    # Implementación con el SDK de OpenAI
│   └── context/
│       └── examples.py               # Estimaciones de ejemplo (contexto CAG)
├── streamlit_app.py                  # Interfaz de chat
├── tests/                            # Tests de estructura, API, caché, servicio y proveedores
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

### Cambiar de proveedor

Para usar OpenAI, cambiá estas dos líneas en `.env` y reiniciá la app:

```
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
```

`LLM_MODEL` tiene que ser un modelo del proveedor elegido.

## Uso

### Interfaz de chat (Streamlit)

```bash
uv run streamlit run streamlit_app.py
```

O, con el entorno virtual activado:

```bash
source .venv/bin/activate
streamlit run streamlit_app.py
```

Se abre en el navegador en http://localhost:8501.

- Pegá la transcripción de una reunión en el cuadro de texto y tocá **Enviar**. La estimación se muestra en streaming.
- El historial de la conversación se mantiene durante la sesión.
- El panel lateral muestra el system prompt activo, las estimaciones de ejemplo que alimentan el CAG y las métricas de la última llamada: proveedor, acierto de caché, modelo, tokens de entrada y de salida, y tiempo de respuesta.
- Si enviás exactamente la misma transcripción otra vez, la respuesta sale de la caché al instante y sin costo.

### API REST (FastAPI)

```bash
uv run uvicorn app.main:app --reload
```

La documentación interactiva queda disponible en http://127.0.0.1:8000/docs.

Ejemplo de llamada:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/estimate \
  -H "Content-Type: application/json" \
  -d '{"transcription": "En la reunión el cliente pidió una app de reservas de turnos con login, calendario y notificaciones."}'
```

Respuesta:

```json
{
  "estimation": "## Estimación: ...",
  "model": "claude-opus-5-5",
  "provider": "anthropic",
  "input_tokens": 1240,
  "output_tokens": 580,
  "cache_hit": false,
  "datetime_created": "2026-10-29T14:32:10.123456Z"
}
```

Cada estimación que no sale de la caché hace una llamada real al LLM y consume crédito de la API key.

### Logs

Los logs aparecen en la terminal donde corre la API o Streamlit. Ejemplo:

```
2026-11-05T13:20:07Z [info] llm_request_completed  cache_hit=False duration_ms=8123 input_tokens=1240 model=claude-opus-5-5 output_tokens=580 provider=anthropic streaming=True
```

Con `APP_ENV=production` se emiten en formato JSON, listos para un sistema de logs. El texto de las transcripciones nunca se registra.

## Tests y calidad de código

```bash
uv run ruff check .          # Linter
uv run ruff format --check . # Formato
uv run pytest -v             # Tests
```

Los tests no llaman a ningún LLM: los proveedores y las respuestas se reemplazan por mocks, así que corren sin API keys reales y sin costo.

## Integración continua

El workflow [`.github/workflows/ci.yml`](.github/workflows/ci.yml) corre automáticamente en cada push a `main` y en cada Pull Request:

1. Linter (`ruff check`)
2. Verificación de formato (`ruff format --check`)
3. Tests de estructura, API, caché, servicio y proveedores (`pytest`)
