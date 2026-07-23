import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    OPENAI_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    LLM_PROVIDER: str = "openai"  # "openai" ou "openrouter"
    MODEL_NAME: str = "gpt-4o-mini"
    
    SUPABASE_URL: str = "https://askqkwvpjhotytmxcfqx.supabase.co"
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    EVOLUTION_API_URL: str = "https://api.evoproia.com.br"
    EVOLUTION_API_KEY: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
