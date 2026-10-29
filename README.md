# Estimador CAG

Estimador de proyectos de software basado en **CAG (Cache-Augmented Generation)**. A partir de la transcripción de una reunión con un cliente, genera una estimación de esfuerzo: desglose de tareas con horas, total estimado, equipo recomendado y duración.

El contexto (estimaciones de ejemplo) se inyecta directamente en el system prompt de cada llamada al LLM, sin base de datos ni búsqueda vectorial.

El proyecto ofrece dos formas de uso:

- **API REST** con FastAPI: `POST /api/v1/estimate`
- **Interfaz de chat** con Streamlit: respuestas en streaming y un panel lateral con el contexto CAG

## Estructura

```
estimador-cag/
├── app/
│   ├── main.py                  # App FastAPI
│   ├── config.py                # Configuración leída desde .env
│   ├── routers/
│   │   └── estimations.py       # Endpoint POST /api/v1/estimate
│   ├── services/
│   │   └── llm_service.py       # System prompt y llamadas al LLM (normal y streaming)
│   └── context/
│       └── examples.py          # Estimaciones de ejemplo (contexto CAG)
├── streamlit_app.py             # Interfaz de chat
├── tests/                       # Tests de estructura e integración
├── .github/workflows/ci.yml     # Pipeline de CI
├── .env.example                 # Plantilla de variables de entorno
└── pyproject.toml
```

## Requisitos

- Python 3.11 o superior
- [uv](https://docs.astral.sh/uv/) para gestionar dependencias
- Una API key de Anthropic

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

4. Editar `.env` y reemplazar `tu-api-key-aca` por tu API key real.

La API key se lee siempre desde `.env`. Nunca está escrita en el código, y el archivo `.env` está excluido de git.

### Variables de entorno

| Variable | Descripción | Ejemplo |
|---|---|---|
| `ANTHROPIC_API_KEY` | API key de Anthropic | `sk-ant-...` |
| `LLM_PROVIDER` | Proveedor del LLM | `anthropic` |
| `LLM_MODEL` | Modelo a utilizar | `claude-opus-5-5` |
| `APP_ENV` | Entorno de ejecución | `development` |
| `LOG_LEVEL` | Nivel de logs | `info` |
| `MAX_TOKENS` | Largo máximo de la respuesta | `16000` |

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

- Pegá la transcripción de una reunión en la caja de chat y la estimación se muestra en streaming.
- El historial de la conversación se mantiene durante la sesión.
- El panel lateral muestra el system prompt activo, las estimaciones de ejemplo que alimentan el CAG y las métricas de la última llamada: modelo, tokens de entrada y de salida, y tiempo de respuesta.

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
  "datetime_created": "2026-10-29T14:32:10.123456Z"
}
```

Cada estimación, tanto desde la API como desde la interfaz, hace una llamada real al LLM y consume crédito de la API key.

## Tests y calidad de código

```bash
uv run ruff check .          # Linter
uv run ruff format --check . # Formato
uv run pytest -v             # Tests
```

Los tests no llaman al LLM: la función que lo invoca se reemplaza por un mock, así que corren sin API key real y sin costo.

## Integración continua

El workflow [`.github/workflows/ci.yml`](.github/workflows/ci.yml) corre automáticamente en cada push a `main` y en cada Pull Request:

1. Linter (`ruff check`)
2. Verificación de formato (`ruff format --check`)
3. Tests de estructura e integración (`pytest`)
