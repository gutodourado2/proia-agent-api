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

    async def send_whatsapp_audio(self, instance: str, remote_jid: str, base64_audio: str) -> bool:
        """Envia arquivo de áudio PTT/WhatsApp via Evolution API"""
        url = f"{self.base_url}/message/sendWhatsAppAudio/{instance}"
        number = remote_jid.split('@')[0]
        payload = {
            "number": number,
            "audio": base64_audio,
            "options": {
                "delay": 1000,
                "presence": "recording",
                "encoding": True
            }
        }
        async with httpx.AsyncClient(timeout=25.0) as client:
            try:
                res = await client.post(url, headers=self.get_headers(), json=payload)
                if res.status_code < 300:
                    return True
                else:
                    logger.warning(f"sendWhatsAppAudio retornou status {res.status_code}. Tentando fallback sendMedia...")
            except Exception as e:
                logger.warning(f"Erro no sendWhatsAppAudio: {e}. Tentando fallback sendMedia...")

            # Fallback para sendMedia
            try:
                url_media = f"{self.base_url}/message/sendMedia/{instance}"
                payload_media = {
                    "number": number,
                    "media": base64_audio,
                    "mediaType": "audio",
                    "mimetype": "audio/mp3"
                }
                res_m = await client.post(url_media, headers=self.get_headers(), json=payload_media)
                return res_m.status_code < 300
            except Exception as ex:
                logger.error(f"Erro no fallback sendMedia: {ex}")
                return False

evolution_service = EvolutionService()
