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

    async def send_image_message(self, instance: str, remote_jid: str, image_url: str, caption: str = "") -> bool:
        """Envia foto/imagem nativa do produto no WhatsApp via Evolution API"""
        url = f"{self.base_url}/message/sendMedia/{instance}"
        number = remote_jid.split('@')[0]
        payload = {
            "number": number,
            "media": image_url,
            "mediaType": "image",
            "mediatype": "image",
            "mimetype": "image/jpeg",
            "caption": caption
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                res = await client.post(url, headers=self.get_headers(), json=payload)
                if res.status_code < 300:
                    logger.info(f"Foto enviada com sucesso via sendMedia para {remote_jid}")
                    return True
                else:
                    logger.warning(f"sendMedia retornou status {res.status_code}: {res.text[:200]}")
                    return False
            except Exception as e:
                logger.error(f"Erro ao enviar foto nativa Evolution API: {e}")
                return False

    async def send_whatsapp_audio(self, instance: str, remote_jid: str, base64_audio: str) -> bool:
        """Envia arquivo de áudio PTT/WhatsApp via Evolution API"""
        url = f"{self.base_url}/message/sendWhatsAppAudio/{instance}"
        number = remote_jid.split('@')[0]
        
        clean_b64 = base64_audio.split(",")[-1] if "," in base64_audio else base64_audio

        payload = {
            "number": number,
            "audio": clean_b64,
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
                    logger.info(f"Audio WhatsApp PTT enviado com sucesso para {remote_jid}")
                    return True
                else:
                    logger.warning(f"sendWhatsAppAudio (clean base64) status {res.status_code}: {res.text[:200]}")
            except Exception as e:
                logger.warning(f"Erro no sendWhatsAppAudio clean base64: {e}")

            # Fallback 1: Tentar com Data URI completo
            try:
                payload["audio"] = base64_audio if "data:audio" in base64_audio else f"data:audio/wav;base64,{clean_b64}"
                res = await client.post(url, headers=self.get_headers(), json=payload)
                if res.status_code < 300:
                    logger.info(f"Audio WhatsApp PTT enviado com sucesso (Data URI) para {remote_jid}")
                    return True
            except Exception as e:
                logger.warning(f"Erro no sendWhatsAppAudio Data URI: {e}")

            # Fallback 2: sendMedia
            try:
                url_media = f"{self.base_url}/message/sendMedia/{instance}"
                mime = "audio/wav" if "wav" in base64_audio.lower() else "audio/mp3"
                payload_media = {
                    "number": number,
                    "media": clean_b64,
                    "mediaType": "audio",
                    "mimetype": mime
                }
                res_m = await client.post(url_media, headers=self.get_headers(), json=payload_media)
                return res_m.status_code < 300
            except Exception as ex:
                logger.error(f"Erro no fallback sendMedia: {ex}")
                return False

evolution_service = EvolutionService()
