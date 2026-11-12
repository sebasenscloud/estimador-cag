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
    prompt_version: str = "v1"
    estimator_api_url: str = "http://127.0.0.1:8000"
    estimator_api_timeout: float = 300.0


settings = Settings()
