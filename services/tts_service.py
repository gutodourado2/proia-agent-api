import re
import logging
import base64
import httpx
from typing import Optional
from config import settings

logger = logging.getLogger("tts_service")

class TTSService:
    def get_api_key(self) -> str:
        return settings.OPENAI_API_KEY or settings.OPENROUTER_API_KEY

    async def generate_speech_base64(self, text: str, gender: str = "feminina") -> Optional[str]:
        """
        Gera audio TTS em formato base64 MP3 nativo do WhatsApp.
        1. Tenta a API nativa da OpenAI (https://api.openai.com/v1/audio/speech) com tts-1 (Voz alloy/echo).
        2. Tenta a API do OpenRouter como fallback.
        """
        if not text or not text.strip():
            return None

        clean_text = text.replace("<speak>", "").replace("</speak>", "").strip()
        clean_text = re.sub(r'!\[.*?\]\([^\)]+\)', '', clean_text)
        clean_text = re.sub(r'https?://\S+', '', clean_text).strip()

        if not clean_text:
            return None

        is_masculino = str(gender).lower().strip() in ["masculina", "masculino", "homem", "macho"]
        openai_voice = "echo" if is_masculino else "alloy"
        api_key = self.get_api_key()

        if not api_key:
            logger.error("Nenhuma chave API (OPENAI_API_KEY / OPENROUTER_API_KEY) configurada para TTS")
            return None

        # 1. Tentar OpenAI Speech API (Nativo tts-1 - Alta Velocidade e Qualidade)
        try:
            logger.info(f"Gerando audio TTS (OpenAI Speech API tts-1) - Voz: {openai_voice}")
            url = "https://api.openai.com/v1/audio/speech"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "tts-1",
                "voice": openai_voice,
                "input": clean_text[:400],
                "response_format": "mp3"
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(url, headers=headers, json=payload)
                if res.status_code < 300 and res.content:
                    b64_audio = base64.b64encode(res.content).decode("utf-8")
                    logger.info("Audio TTS gerado com sucesso via OpenAI Speech API")
                    return f"data:audio/mp3;base64,{b64_audio}"
                else:
                    logger.warning(f"OpenAI Speech API retornou status {res.status_code}: {res.text[:200]}")
        except Exception as e:
            logger.warning(f"Excecao ao chamar OpenAI Speech API: {e}")

        # 2. Fallback OpenRouter Speech API
        try:
            url_or = "https://openrouter.ai/api/v1/audio/speech"
            headers_or = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload_or = {
                "model": "google/gemini-3.1-flash-tts-preview",
                "voice": "Kore" if is_masculino else "puck",
                "input": clean_text[:400]
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                res_or = await client.post(url_or, headers=headers_or, json=payload_or)
                if res_or.status_code < 300 and res_or.content:
                    b64_audio = base64.b64encode(res_or.content).decode("utf-8")
                    logger.info("Audio TTS gerado com sucesso via OpenRouter Speech API")
                    return f"data:audio/mp3;base64,{b64_audio}"
                else:
                    logger.error(f"OpenRouter Speech API retornou status {res_or.status_code}: {res_or.text[:200]}")
        except Exception as ex:
            logger.error(f"Excecao no fallback OpenRouter TTS: {ex}")

        return None

tts_service = TTSService()
