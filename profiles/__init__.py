from profiles.base_profile import BaseAgentProfile
from profiles.gemini_profile import GeminiProfile
from profiles.deepseek_profile import DeepSeekProfile

PROFILES = {
    "gemini": GeminiProfile(),
    "deepseek": DeepSeekProfile(),
}

def get_active_profile(profile_name: str = "gemini") -> BaseAgentProfile:
    """Retorna a instância do perfil de IA ativo com base na variável de configuração."""
    name_lower = (profile_name or "gemini").lower().strip()
    return PROFILES.get(name_lower, PROFILES["gemini"])
