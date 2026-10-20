from pydantic_settings import BaseSettings,SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    anthropic_api_key : str
    llm_provider : str
    llm_model : str
    app_env : str
    log_level : str
    max_tokens : int
    
settings=Settings()