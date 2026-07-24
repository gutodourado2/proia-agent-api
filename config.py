import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    OPENAI_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    LLM_PROVIDER: str = "openai"  # "openai" ou "openrouter"
    MODEL_NAME: str = "gpt-4o-mini"
    
    SUPABASE_URL: str = "https://askqkwvpjhotytmxcfqx.supabase.co"
    SUPABASE_SERVICE_ROLE_KEY: str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFza3Frd3ZwamhvdHl0bXhjZnF4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI2NDcxNTMsImV4cCI6MjA4ODIyMzE1M30.GDFVXEYt0aZSMgZ6xhyrl9oA-DeKqP1i4JkyukWQ06A"
    EVOLUTION_API_URL: str = "https://evo.proia.com.br"
    EVOLUTION_API_KEY: str = "72055e41-9f72-4dac-97c2-7b5109890b50"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
