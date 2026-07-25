import re
import struct
import logging
import base64
import httpx
from typing import Optional
from config import settings

logger = logging.getLogger("tts_service")

def pcm_to_wav(pcm_data: bytes, sample_rate: int = 24000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
    """Adiciona o cabecalho RIFF/WAV de 44 bytes aos dados brutos PCM do Gemini TTS"""
    data_size = len(pcm_data)
    block_align = channels * (bits_per_sample // 8)
    byte_rate = sample_rate * block_align
    
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        36 + data_size,
        b'WAVE',
        b'fmt ',
        16,
        1,  # PCM format
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b'data',
        data_size
    )
    return header + pcm_data

class TTSService:
    def get_api_key(self) -> str:
        return settings.OPENAI_API_KEY or settings.OPENROUTER_API_KEY

    async def generate_speech_base64(self, text: str, gender: str = "feminina") -> Optional[str]:
        """
        Gera o audio TTS em formato base64 MP3 ou WAV nativo do WhatsApp.
        1. Tenta OpenRouter com 'google/gemini-3.1-flash-tts-preview' (Voz puck para feminina, Kore para masculina) + Conversao PCM to WAV.
        2. Tenta OpenAI Speech API ('gpt-4o-mini-tts' / 'tts-1') com vozes 'coral' / 'cedar' como fallback.
        """
        if not text or not text.strip():
            return None

        clean_text = text.replace("<speak>", "").replace("</speak>", "").strip()
        clean_text = re.sub(r'!\[.*?\]\([^\)]+\)', '', clean_text)
        clean_text = re.sub(r'https?://\S+', '', clean_text).strip()

        if not clean_text:
            return None

        is_feminina = str(gender).lower().strip() in ["feminina", "feminino", "mulher"]
        openrouter_voice = "Kore" if is_feminina else "puck"
        openai_voice = "coral" if is_feminina else "cedar"

        logger.info(f"Voz configurada para a empresa: {gender} -> Voz OpenRouter: {openrouter_voice} | Voz OpenAI: {openai_voice}")

        api_key = self.get_api_key()
        if not api_key:
            logger.error("Nenhuma chave API (OPENAI_API_KEY / OPENROUTER_API_KEY) configurada para TTS")
            return None

        # 1. Tentar OpenRouter Gemini 3.1 Flash TTS Preview (Voz Ultra Profissional puck/Kore)
        try:
            logger.info(f"Gerando voz Gemini 3.1 Flash TTS via OpenRouter - Voz: {openrouter_voice}")
            url_or = "https://openrouter.ai/api/v1/audio/speech"
            headers_or = {
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY or api_key}",
                "Content-Type": "application/json"
            }
            payload_or = {
                "model": "google/gemini-3.1-flash-tts-preview",
                "voice": openrouter_voice,
                "input": clean_text[:400]
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                res_or = await client.post(url_or, headers=headers_or, json=payload_or)
                if res_or.status_code < 300 and res_or.content:
                    raw_pcm = res_or.content
                    wav_bytes = pcm_to_wav(raw_pcm)
                    b64_audio = base64.b64encode(wav_bytes).decode("utf-8")
                    logger.info("Audio Gemini 3.1 Flash TTS gerado com sucesso via OpenRouter (com formato WAV 24kHz)")
                    return f"data:audio/wav;base64,{b64_audio}"
                else:
                    logger.warning(f"OpenRouter Gemini TTS retornou status {res_or.status_code}: {res_or.text[:200]}")
        except Exception as ex:
            logger.warning(f"Excecao no OpenRouter Gemini TTS: {ex}")

        # 2. Fallback OpenAI Speech API (gpt-4o-mini-tts / tts-1)
        try:
            logger.info(f"Gerando fallback TTS via OpenAI - Voz: {openai_voice}")
            url = "https://api.openai.com/v1/audio/speech"
            headers = {
                "Authorization": f"Bearer {settings.OPENAI_API_KEY or api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "gpt-4o-mini-tts",
                "voice": openai_voice,
                "input": clean_text[:400],
                "instructions": (
                    "Fale em português brasileiro. Voz masculina de aproximadamente 35 anos, tom profissional, amigável e natural, com pausas suaves."
                    if is_masculino else
                    "Fale em português brasileiro. Voz feminina de aproximadamente 25 anos, tom profissional, amigável e natural, com pausas suaves."
                ),
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
                    logger.info("Audio TTS gerado com sucesso via OpenAI Fallback")
                    return f"data:audio/mp3;base64,{b64_audio}"
                else:
                    logger.error(f"OpenAI Speech Fallback retornou status {res.status_code}: {res.text[:200]}")
        except Exception as e:
            logger.error(f"Excecao no OpenAI Speech Fallback: {e}")

        return None

tts_service = TTSService()
