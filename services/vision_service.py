import json
import logging
from openai import AsyncOpenAI
from config import settings

logger = logging.getLogger("vision_service")

class VisionService:
    def get_client_and_model(self):
        if settings.LLM_PROVIDER.lower() == "openrouter" or (settings.OPENROUTER_API_KEY and not settings.OPENAI_API_KEY):
            client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.OPENROUTER_API_KEY,
                default_headers={
                    "HTTP-Referer": "https://proia.com.br",
                    "X-Title": "ProIA Delivery Agent"
                }
            )
            return client, "openai/gpt-4o-mini"
        else:
            client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            return client, "gpt-4o-mini"

    async def analyze_pix_receipt(self, base64_data: str, message_type: str = "imageMessage") -> str:
        prompt = (
            "Analise este documento/comprovante. Responda APENAS com um JSON bruto contendo os campos: "
            "'tipo' (pode ser 'comprovante_pix' se for um comprovante de pix, ou 'outro'), "
            "'valor' (o valor pago se for pix, em formato numérico ex: 50.00, ou null), "
            "'recebedor' (o nome de quem recebeu o Pix, ex: Letícia Evaristo Ribeiro, ou null), "
            "'status' (pode ser 'sucesso' se o pagamento foi confirmado com sucesso, ou 'pendente'). "
            "Exemplo de resposta: {\"tipo\": \"comprovante_pix\", \"valor\": 70.00, \"recebedor\": \"Letícia Evaristo Ribeiro\", \"status\": \"sucesso\"}. "
            "Responda APENAS o JSON bruto, sem tags markdown ou qualquer outro texto."
        )

        try:
            client, model_name = self.get_client_and_model()

            if message_type == "documentMessage":
                content = [
                    {"type": "text", "text": prompt},
                    {
                        "type": "file",
                        "file": {
                            "filename": "comprovante.pdf",
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
                            "url": f"data:image/png;base64,{base64_data}"
                        }
                    }
                ]

            response = await client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": content}],
                temperature=0.0
            )

            result_text = response.choices[0].message.content.strip()
            try:
                data = json.loads(result_text)
                return (
                    f"[Cliente enviou um comprovante. Análise OpenAI: "
                    f"Tipo: {data.get('tipo')}, Valor: R$ {data.get('valor')}, "
                    f"Recebedor: {data.get('recebedor')}, Status: {data.get('status')}]"
                )
            except Exception:
                return f"[Cliente enviou um comprovante. Conteúdo bruto da análise: {result_text}]"

        except Exception as e:
            logger.error(f"Erro na visão computacional: {e}")
            return "[Falha ao analisar a imagem/PDF do comprovante do cliente.]"

vision_service = VisionService()
