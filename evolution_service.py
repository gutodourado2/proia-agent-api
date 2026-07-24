import logging
import httpx
from config import settings

logger = logging.getLogger("evolution_service")

class EvolutionService:
    def __init__(self):
        self.base_url = settings.EVOLUTION_API_URL.rstrip('/')
        self.api_key = settings.EVOLUTION_API_KEY

    def get_headers(self) -> dict:
        return {
            "apikey": self.api_key,
            "Content-Type": "application/json"
        }

    async def send_presence(self, instance: str, remote_jid: str, state: str = "composing") -> bool:
        """Envia indicador de 'digitando...' (composing) ou 'gravando áudio...' (recording) no WhatsApp"""
        url = f"{self.base_url}/chat/sendPresence/{instance}"
        number = remote_jid.split('@')[0]
        payload = {
            "number": number,
            "remoteJid": remote_jid,
            "presence": state,
            "delay": 2000
        }
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                res = await client.post(url, headers=self.get_headers(), json=payload)
                return res.status_code < 300
            except Exception as e:
                logger.warning(f"Falha ao enviar presenca Evolution: {e}")
                return False

    async def send_text_message(self, instance: str, remote_jid: str, text: str) -> bool:
        """Envia mensagem de texto via Evolution API"""
        url = f"{self.base_url}/message/sendText/{instance}"
        payload = {
            "number": remote_jid.split('@')[0],
            "text": text,
            "options": {
                "delay": 1000,
                "presence": "composing",
                "linkPreview": True
            }
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.post(url, headers=self.get_headers(), json=payload)
                res.raise_for_status()
                return True
            except Exception as e:
                logger.error(f"Erro ao enviar mensagem de texto Evolution API: {e}")
                return False

evolution_service = EvolutionService()
