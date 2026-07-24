import logging
import base64
import httpx
from typing import Optional
from config import settings

logger = logging.getLogger("tts_service")

class TTSService:
    def __init__(self):
        self.openrouter_key = settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY
        self.openai_key = settings.OPENAI_API_KEY or settings.OPENROUTER_API_KEY

    async def generate_speech_base64(self, text: str, gender: str = "feminina") -> Optional[str]:
        """
        Gera audio TTS em formato base64 MP3.
        1. Tenta OpenRouter com 'google/gemini-3.1-flash-tts-preview' (Masculino: Kore, Feminino: puck).
        2. Em caso de falha no OpenRouter, faz fallback para OpenAI TTS ('gpt-4o-mini-tts' / 'tts-1').
        """
        if not text or not text.strip():
            return None

        # Limpar SSML se presente
        clean_text = text.replace("<speak>", "").replace("</speak>", "").strip()
        if "/>" in clean_text:
            clean_text = clean_text.split("/>")[-1].strip()

        is_masculino = str(gender).lower().strip() in ["masculina", "masculino", "homem", "macho"]

        openrouter_voice = "Kore" if is_masculino else "puck"
        openai_voice = "cedar" if is_masculino else "coral"
        openai_instructions = (
            "Fale em português brasileiro. Voz masculina de aproximadamente 35 anos, tom profissional, amigável e natural, com pausas suaves."
            if is_masculino else
            "Fale em português brasileiro. Voz feminina de aproximadamente 25 anos, tom profissional, amigável e natural, com pausas suaves."
        )

        # 1. Tentar OpenRouter TTS (google/gemini-3.1-flash-tts-preview)
        if self.openrouter_key:
            try:
                logger.info(f"Gerando TTS via OpenRouter (google/gemini-3.1-flash-tts-preview) - Voz: {openrouter_voice}")
                url = "https://openrouter.ai/api/v1/audio/speech"
                headers = {
                    "Authorization": f"Bearer {self.openrouter_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "google/gemini-3.1-flash-tts-preview",
                    "voice": openrouter_voice,
                    "input": clean_text
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    res = await client.post(url, headers=headers, json=payload)
                    if res.status_code < 300 and res.content:
                        b64_audio = base64.b64encode(res.content).decode("utf-8")
                        return f"data:audio/mp3;base64,{b64_audio}"
                    else:
                        logger.warning(f"OpenRouter TTS retornou erro status {res.status_code}: {res.text[:200]}. Iniciando fallback OpenAI...")
            except Exception as e:
                logger.warning(f"Excecao no OpenRouter TTS: {e}. Iniciando fallback OpenAI...")

        # 2. Fallback para OpenAI TTS (gpt-4o-mini-tts / tts-1)
        if self.openai_key:
            try:
                logger.info(f"Gerando TTS via OpenAI Fallback - Voz: {openai_voice}")
                url = "https://api.openai.com/v1/audio/speech"
                headers = {
                    "Authorization": f"Bearer {self.openai_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "gpt-4o-mini-tts",
                    "voice": openai_voice,
                    "input": clean_text,
                    "instructions": openai_instructions,
                    "response_format": "mp3"
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    res = await client.post(url, headers=headers, json=payload)
                    if res.status_code >= 400:
                        payload["model"] = "tts-1"
                        payload.pop("instructions", None)
                        res = await client.post(url, headers=headers, json=payload)

                    if res.status_code < 300 and res.content:
                        b64_audio = base64.b64encode(res.content).decode("utf-8")
                        return f"data:audio/mp3;base64,{b64_audio}"
                    else:
                        logger.error(f"OpenAI Fallback TTS retornou erro status {res.status_code}: {res.text[:200]}")
            except Exception as e:
                logger.error(f"Excecao no OpenAI Fallback TTS: {e}")

        return None

tts_service = TTSService()
