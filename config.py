import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    OPENAI_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    LLM_PROVIDER: str = "openrouter"  # "openai" ou "openrouter"
    ACTIVE_AGENT_PROFILE: str = "deepseek"  # "deepseek" ou "gemini" - Alterne aqui para troca instantânea de produção!
    MODEL_NAME: str = "deepseek/deepseek-v4.1-flash"
    FALLBACK_MODEL_NAME: str = "google/gemini-3.7-flash"

    SECOND_FALLBACK_MODEL_NAME: str = "google/gemini-3.1-flash-lite"
    PREVIOUS_MODEL_NAME: str = "google/gemini-3.7-flash"

    
    # MODO TESTER STAGING (ISOLAMENTO TOTAL DE SEGURANÇA)
    ENABLE_TESTER_MODE: bool = True
    TESTER_PHONE_NUMBERS: list[str] = ["5577998238209", "557791662913"]
    TESTER_EMPRESA_ID: int = 99
    TESTER_MODEL_NAME: str = "deepseek/deepseek-v4.1-flash"

    # -------------------------------------------------------------------------
    # JEV SYSTEM ONE DECISION GATEWAY & ROLLBACK KILL-SWITCHES
    # -------------------------------------------------------------------------
    ENABLE_JEV_GATEWAY: bool = True  # Ativado: decisões estruturadas System One via OpenRouter
    ENABLE_JEV_PRINT_GATE: bool = True  # Trava de confirmação explícita antes de criar_pedido_completo
    ENABLE_JEV_ADDRESS_GATE: bool = True  # Trava de endereço por extenso (bloqueia PIN puro do WhatsApp)
    ENABLE_JEV_ORDER_ADDITIONS: bool = True  # Gestão inteligente de status do pedido em adições
    JEV_MODEL_NAME: str = "typesafe/jev-1.14"
    JEV_TIMEOUT_SECONDS: float = 3.5  # Timeout curto; se expirar, degrada graciosamente sem travar o cliente

    def model_post_init(self, __context: object) -> None:
        # Sanitizar chaves de API contra espacos ou aspas acidentais vindas do Coolify/.env
        if self.OPENROUTER_API_KEY:
            self.OPENROUTER_API_KEY = self.OPENROUTER_API_KEY.strip().strip('"\'')
        if self.OPENAI_API_KEY:
            self.OPENAI_API_KEY = self.OPENAI_API_KEY.strip().strip('"\'')

        # Previne erros de modelo inválido no OpenRouter:
        # 1. Se informarem 'deepseek-v4-flash-0423' com sufixo incorreto, normaliza para 'deepseek/deepseek-v4-flash'
        if self.MODEL_NAME == "deepseek/deepseek-v4-flash-0423":
            self.MODEL_NAME = "deepseek/deepseek-v4-flash"
        elif not self.MODEL_NAME:
            self.MODEL_NAME = "deepseek/deepseek-v4-flash-0731"

        if self.TESTER_MODEL_NAME == "deepseek/deepseek-v4-flash-0423":
            self.TESTER_MODEL_NAME = "deepseek/deepseek-v4-flash"
        elif not self.TESTER_MODEL_NAME:
            self.TESTER_MODEL_NAME = "deepseek/deepseek-v4-flash-0731"
    
    SUPABASE_URL: str = "https://askqkwvpjhotytmxcfqx.supabase.co"
    SUPABASE_SERVICE_ROLE_KEY: str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFza3Frd3ZwamhvdHl0bXhjZnF4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI2NDcxNTMsImV4cCI6MjA4ODIyMzE1M30.GDFVXEYt0aZSMgZ6xhyrl9oA-DeKqP1i4JkyukWQ06A"
    EVOLUTION_API_URL: str = "https://evo.proia.com.br"
    EVOLUTION_API_KEY: str = "72055e41-9f72-4dac-97c2-7b5109890b50"
    ENABLE_N8N_FORWARD: bool = False
    N8N_FORWARD_WEBHOOK_URL: str = "https://n8n.proia.com.br/webhook/cf0c69c8-01ab-4a34-907a-febed00cc5db"
    N8N_SECONDARY_FORWARD_WEBHOOK_URL: str = "https://n8n.proia.com.br/webhook/42456d3e-1951-4f2b-9290-c08dee4a52d0"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
