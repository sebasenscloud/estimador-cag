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
    # Adjuntos: "local_text" = camino B. Preparado para sumar el camino A.
    attachment_strategy: str = "local_text"
    attachment_max_files: int = 5
    attachment_max_bytes: int = 10 * 1024 * 1024
    attachment_max_chars: int = 20_000
    estimator_api_url: str = "http://127.0.0.1:8000"
    estimator_api_timeout: float = 300.0


settings = Settings()
