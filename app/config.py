from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "anthropic"
    llm_model: str = "claude-opus-5-5"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    app_env: str = "development"
    log_level: str = "info"
    max_tokens: int = 16000
    cache_max_entries: int = 128


settings = Settings()
