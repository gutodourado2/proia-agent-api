from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseAgentProfile(ABC):
    name: str
    model_name: str
    temperature: float
    
    @abstractmethod
    def build_system_prompt(self, empresa_nome: str, fuso_horario: str, data_hora_local: str, regras_adicionais: str = "") -> str:
        """Gera o System Prompt formatado com os dados da empresa."""
        pass
