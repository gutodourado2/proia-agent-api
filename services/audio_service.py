import base64
import logging
from typing import Optional
from openai import AsyncOpenAI
from config import settings

logger = logging.getLogger("audio_service")

class AudioService:
    def get_client(self):
        api_key = settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY
        return AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            default_headers={
                "HTTP-Referer": "https://proia.com.br",
                "X-Title": "ProIA Delivery Audio Agent"
            }
        )

    async def transcribe_audio_base64(self, base64_audio: str) -> Optional[str]:
        """
        Transcreve mensagem de áudio do WhatsApp usando Google Gemini Audio via OpenRouter.
        """
        if not base64_audio:
            return None
        try:
            # Limpar cabeçalho data:audio/...;base64, se existir
            if "," in base64_audio:
                base64_audio = base64_audio.split(",")[1]

            client = self.get_client()

            content = [
                {
                    "type": "text",
                    "text": "Transcreva o áudio a seguir exatamente como falado pelo usuário em português. Retorne APENAS o texto falado, sem introdução, sem explicações e sem aspas."
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:audio/ogg;base64,{base64_audio}"
                    }
                }
            ]

            response = await client.chat.completions.create(
                model="google/gemini-2.5-flash",
                messages=[{"role": "user", "content": content}],
                temperature=0.0
            )

            text = response.choices[0].message.content.strip() if response.choices and response.choices[0].message.content else ""
            logger.info(f"Transcrição Gemini Audio realizada com sucesso: '{text}'")
            return text
        except Exception as e:
            logger.error(f"Erro ao transcrever áudio com Gemini Audio: {e}")
            return None

    async def transcribe_audio(self, base64_audio: str) -> Optional[str]:
        """Alias para garantir compatibilidade com chamadas no main.py"""
        return await self.transcribe_audio_base64(base64_audio)

audio_service = AudioService()
