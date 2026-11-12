from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Formato LiteLLM: "<proveedor>/<modelo>", ej. "openai/gpt-4o-mini".
    llm_model: str = "anthropic/claude-haiku-4-5"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    llm_max_retries: int = 3
    app_env: str = "development"
    log_level: str = "info"
    max_tokens: int = 16000
    cache_max_entries: int = 128
    # Output guardrail: proyectos más largos que esto se consideran fuera de scope.
    scope_max_weeks: int = 104
    prompt_version: str = "v1"
    # Sesiones: turnos (user + assistant) que conserva la ventana deslizante.
    session_max_turns: int = 6
    # Adjuntos. ATTACHMENT_STRATEGY: auto (el router elige por documento),
    # local_text (siempre camino B) o multimodal (PDFs siempre por el camino A).
    attachment_strategy: str = "auto"
    # Camino A: inline (base64 en el mensaje) o files_api (subida al proveedor).
    attachment_multimodal_mode: str = "inline"
    # Umbrales del router para PDFs.
    attachment_min_chars_per_page: int = 100
    attachment_image_heavy_per_page: float = 1.0
    attachment_visual_max_chars_per_page: int = 800
    attachment_large_pdf_pages: int = 30
    attachment_max_files: int = 5
    attachment_max_bytes: int = 10 * 1024 * 1024
    attachment_max_chars: int = 20_000
    estimator_api_url: str = "http://127.0.0.1:8000"
    estimator_api_timeout: float = 300.0


settings = Settings()
