import json
import logging
from typing import Dict, Any
from openai import AsyncOpenAI
from config import settings

logger = logging.getLogger("vision_service")

class VisionService:
    def get_client_and_model(self):
        api_key = settings.OPENAI_API_KEY or settings.OPENROUTER_API_KEY
        if settings.OPENAI_API_KEY:
            client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            return client, "gpt-4o-mini"
        else:
            client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
                default_headers={
                    "HTTP-Referer": "https://proia.com.br",
                    "X-Title": "ProIA Delivery Agent"
                }
            )
            return client, "google/gemini-2.5-flash"

    async def analyze_image_or_receipt(self, base64_data: str, user_caption: str = "", message_type: str = "imageMessage") -> str:
        """
        Analisa imagens/documentos enviadas no WhatsApp:
        - Se for comprovante PIX: valida valor, recebedor e status.
        - Se for imagem geral / pergunta do cliente: descreve o que há na imagem de forma natural.
        """
        prompt = (
            "Analise esta imagem enviada pelo cliente no WhatsApp.\n"
            f"Mensagem/Pergunta do cliente: '{user_caption}'.\n\n"
            "INSTRUÇÕES:\n"
            "1. Se for um COMPROVANTE DE PAGAMENTO PIX, responda EXATAMENTE um JSON bruto:\n"
            "{\"tipo\": \"comprovante_pix\", \"valor\": 70.00, \"recebedor\": \"Nome\", \"status\": \"sucesso\"}\n\n"
            "2. Se NÃO for comprovante (ex: foto de pessoa, produto, objeto ou duvida do cliente), responda com uma descrição natural, simpática e objetiva em Português respondendo exatamente a pergunta do cliente sobre o que há na imagem."
        )

        try:
            client, model_name = self.get_client_and_model()

            if message_type == "documentMessage":
                content = [
                    {"type": "text", "text": prompt},
                    {
                        "type": "file",
                        "file": {
                            "filename": "documento.pdf",
                            "file_data": f"data:application/pdf;base64,{base64_data}"
                        }
                    }
                ]
            else:
                content = [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_data}"
                        }
                    }
                ]

            response = await client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": content}],
                temperature=0.2
            )

            result_text = response.choices[0].message.content.strip()
            
            try:
                data = json.loads(result_text)
                if isinstance(data, dict) and data.get("tipo") == "comprovante_pix":
                    return (
                        f"[Cliente enviou um comprovante. Análise OpenAI: "
                        f"Tipo: comprovante_pix, Valor: R$ {data.get('valor')}, "
                        f"Recebedor: {data.get('recebedor')}, Status: {data.get('status')}]"
                    )
            except Exception:
                pass

            if user_caption:
                return f"[Análise da Imagem: {result_text}. Pergunta do cliente: '{user_caption}']"
            return f"[Análise da Imagem: {result_text}]"

        except Exception as e:
            logger.error(f"Erro na visão computacional: {e}")
            if user_caption:
                return user_caption
            return "Olá! Vi que você me mandou uma foto. Como posso te ajudar?"

vision_service = VisionService()
