import base64
import io
import logging
from typing import Optional
from openai import AsyncOpenAI
from config import settings

logger = logging.getLogger("audio_service")

class AudioService:
    def __init__(self):
        self.api_key = settings.OPENAI_API_KEY or settings.OPENROUTER_API_KEY

    async def transcribe_audio_base64(self, base64_audio: str) -> Optional[str]:
        """Transcreve mensagem de áudio do WhatsApp usando OpenAI Whisper API"""
        if not base64_audio:
            return None
        try:
            audio_bytes = base64.b64decode(base64_audio)
            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = "voice_message.mp3"

            client = AsyncOpenAI(api_key=self.api_key)
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="pt"
            )
            text = transcript.text.strip() if transcript and transcript.text else ""
            logger.info(f"Transcrição Whisper realizada com sucesso: '{text}'")
            return text
        except Exception as e:
            logger.error(f"Erro ao transcrever áudio com Whisper: {e}")
            return None

audio_service = AudioService()
