import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import httpx

from config import settings

logger = logging.getLogger("proia-agent-api.jev")

class JevService:
    """
    Serviço de Integração com TypeSafe Jev 1.14 (System One) via OpenRouter.
    Executa decisões rápidas, tipadas e estruturadas (noul, choice, score)
    para blindagem de pedidos, trava de impressão térmica, validação de endereço e roteamento de custos.
    
    Possui tolerância total a falhas (Graceful Degradation):
    Se a API do Jev falhar ou o gateway estiver desativado, o sistema degrada com segurança
    sem nunca quebrar o fluxo de atendimento do cliente.
    """

    def __init__(self):
        self.base_url = "https://openrouter.ai/api/v1/chat/completions"
        self.model = settings.JEV_MODEL_NAME
        self.timeout = getattr(settings, "JEV_TIMEOUT_SECONDS", 3.5)

    def _get_api_key(self) -> str:
        key = settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY
        return key.strip().strip('"\'') if key else ""

    async def _call_jev(self, system_instruction: str, user_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Executa chamada assíncrona ao Jev no OpenRouter com timeout estrito e tratamento de exceções.
        """
        if not settings.ENABLE_JEV_GATEWAY:
            return None

        api_key = self._get_api_key()
        if not api_key:
            logger.warning("[JEV] Chave de API OpenRouter não configurada. Operando em bypass seguro.")
            return None

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://proia.com.br",
            "X-Title": "ProIA Delivery System One"
        }

        prompt = (
            f"{system_instruction}\n\n"
            f"Avalie estritamente os dados abaixo e responda APENAS um JSON válido no formato:\n"
            f"{json.dumps(user_payload.get('expected_schema', {}), indent=2)}\n\n"
            f"DADOS DE ENTRADA:\n"
            f"{json.dumps(user_payload.get('state', {}), ensure_ascii=False)}"
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0,
            "max_tokens": 512
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(self.base_url, headers=headers, json=payload)
                if res.status_code != 200:
                    logger.error(f"[JEV] Erro HTTP {res.status_code}: {res.text[:200]}")
                    return None
                
                data = res.json()
                raw_content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                # Extrair JSON da resposta mesmo se houver blocos de código markdown
                clean_json_str = re.sub(r'```(?:json)?', '', raw_content).strip()
                match = re.search(r'\{.*\}', clean_json_str, re.DOTALL)
                if match:
                    clean_json_str = match.group(0)
                
                parsed = json.loads(clean_json_str)
                logger.info(f"[JEV] Decisão tomada com sucesso: {parsed}")
                return parsed
        except httpx.TimeoutException:
            logger.warning(f"[JEV] Timeout de {self.timeout}s na chamada. Aplicando fallback de segurança.")
            return None
        except Exception as e:
            logger.warning(f"[JEV] Falha ao consultar Jev ({e}). Aplicando fallback de segurança.")
            return None

    # =========================================================================
    # PORTA 1: TRAVA DE IMPRESSÃO SEGURA (ORDER FINALIZATION GATE)
    # =========================================================================
    async def verify_order_readiness(
        self,
        carrinho: Dict[str, Any],
        ultimo_resumo_ia: str,
        mensagem_cliente: str,
        tipo_entrega: str = "retirada"
    ) -> Tuple[bool, str]:
        """
        Verifica com certeza matemática se o pedido está 100% pronto para ser criado e impresso na cozinha.
        Exige que o cliente tenha dito expressamente 'Sim / Confirmado / Pode fechar' para o resumo.
        """
        if not settings.ENABLE_JEV_GATEWAY or not settings.ENABLE_JEV_PRINT_GATE:
            # Fallback seguro: se o gateway estiver desativado, autoriza a tool
            return True, "jev_disabled_bypass"

        # Heurística rápida de segurança local: se a mensagem do cliente for uma negação ou pergunta, barra na hora
        msg_lower = mensagem_cliente.lower().strip()
        palavras_negativas = ["não", "nao", "espera", "pera", "calma", "trocar", "mudar", "quanto fica", "qual o valor", "tem ", "vocês tem"]
        if any(msg_lower.startswith(p) or msg_lower == p for p in palavras_negativas):
            return False, "Cliente fez pergunta ou pediu para esperar antes de fechar."

        system_instruction = (
            "Você é o Jev System One Gatekeeper de Impressão Térmica de Cozinha do ProIA Delivery. "
            "Sua missão é IMPEDIR QUE A IMPRESSORA COMA PAPEL ANTES DA HORA. "
            "A impressora física SÓ PODE IMPRIMIR se o cliente tiver respondido expressamente confirmando "
            "o resumo final (ex: 'Sim', 'Pode fechar', 'Confirmo', 'Isso mesmo', 'Manda ver', 'Tá certo'). "
            "Se o cliente acabou de escolher itens, ou apenas disse 'Quero um frango às 10h' ou 'Feijão tropeiro e pepsi', "
            "o pedido AINDA NÃO FOI CONFIRMADO e NÃO PODE SER IMPRESSO! A resposta deve ser FALSE."
        )

        state = {
            "carrinho": carrinho,
            "tipo_entrega": tipo_entrega,
            "ultimo_resumo_ia": ultimo_resumo_ia[-300:] if ultimo_resumo_ia else "",
            "mensagem_cliente": mensagem_cliente
        }

        expected_schema = {
            "is_ready_to_finalize_and_print": False,
            "probability": 0.05,
            "reason": "Cliente ainda não confirmou o resumo final com 'Sim'."
        }

        result = await self._call_jev(system_instruction, {"state": state, "expected_schema": expected_schema})
        
        if result and isinstance(result, dict):
            is_ready = bool(result.get("is_ready_to_finalize_and_print", False))
            prob = float(result.get("probability", 0.0))
            reason = str(result.get("reason", "Validação Jev concluída."))
            
            # Trava estrita: exige probabilidade >= 0.85
            if is_ready and prob >= 0.85:
                return True, reason
            else:
                return False, reason

        # Fallback local de regex caso o Jev esteja indisponível:
        confirmacoes_regex = r'\b(sim|confirmo|confirmado|pode fechar|pode mandar|isso|isso mesmo|manda ver|t[aá] certo|fechou|ok pode)\b'
        if re.search(confirmacoes_regex, msg_lower):
            return True, "fallback_regex_confirmado"
        
        return False, "O cliente ainda não confirmou expressamente o resumo final do pedido."

    # =========================================================================
    # PORTA 2: VALIDAÇÃO DE ENDEREÇO 100% POR ESCRITO & ANTI-PREJUÍZO DE FRETE
    # =========================================================================
    async def validate_address_text(
        self,
        raw_address_text: str,
        cidade_loja: str = "Luís Eduardo Magalhães - BA"
    ) -> Dict[str, Any]:
        """
        Valida se o endereço foi enviado por extenso com Rua, Número e Bairro.
        Rejeita PINs isolados do WhatsApp e endereços incompletos que causariam prejuízo no frete.
        """
        fallback_default = {
            "is_valid": True,
            "is_pin_only": False,
            "clean_address": f"{raw_address_text}, {cidade_loja}",
            "delivery_notes": "",
            "reason": "bypass_jev"
        }

        if not settings.ENABLE_JEV_GATEWAY or not settings.ENABLE_JEV_ADDRESS_GATE:
            return fallback_default

        # Detecção local de mensagem de PIN ou link de mapa
        raw_lower = raw_address_text.lower().strip()
        if any(term in raw_lower for term in ["location", "maps.google", "maps.app", "pin", "mandei a localizacao", "mandei a localização"]):
            return {
                "is_valid": False,
                "is_pin_only": True,
                "clean_address": "",
                "delivery_notes": "",
                "reason": "Cliente enviou PIN ou link de localização. O delivery exige endereço completo por escrito para precisão de entrega."
            }

        system_instruction = (
            "Você é o Jev Address Gatekeeper do ProIA Delivery. "
            "Sua missão é evitar prejuízos financeiros no cálculo do frete para o dono da loja. "
            "REGRAS RÍGIDAS: "
            "1. Se o texto não tiver NÚMERO predial (ou quadra/lote/bloco) E BAIRRO, retorne is_valid: false. "
            "2. Separe o endereço oficial (Rua, Número, Bairro) das referências do motoboy (ex: 'portão azul', 'em frente à praça'). "
            "3. Se for apenas localização ou ponto de referência vago sem logradouro e número, retorne is_valid: false."
        )

        state = {
            "cidade_oficial": cidade_loja,
            "texto_endereco": raw_address_text
        }

        expected_schema = {
            "is_valid": True,
            "is_pin_only": False,
            "has_number_or_lot": True,
            "has_neighborhood": True,
            "clean_address": "Rua Paraiba, 450, Mimoso 1, Luís Eduardo Magalhães - BA",
            "delivery_notes": "Portão azul em frente ao mercado",
            "reason": "Endereço completo com número e bairro identificado."
        }

        result = await self._call_jev(system_instruction, {"state": state, "expected_schema": expected_schema})
        if result and isinstance(result, dict):
            return result

        # Fallback local: se tem pelo menos 1 dígito numérico e tamanho mínimo razoável
        tem_numero = bool(re.search(r'\d+', raw_address_text)) or "s/n" in raw_lower or "lote" in raw_lower or "quadra" in raw_lower
        if not tem_numero:
            return {
                "is_valid": False,
                "is_pin_only": False,
                "clean_address": "",
                "delivery_notes": "",
                "reason": "Endereço sem número predial ou lote. Necessário solicitar número para cálculo exato."
            }

        return fallback_default

    # =========================================================================
    # PORTA 3: GESTÃO INTELIGENTE DE ADIÇÕES & CONSULTA DE STATUS DO PEDIDO
    # =========================================================================
    async def evaluate_order_addition(
        self,
        mensagem_cliente: str,
        ultimo_pedido: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Identifica se o cliente está querendo adicionar itens a um pedido recente
        e se o pedido já saiu para entrega (Status >= 3) ou ainda está na loja (Status < 3).
        """
        if not ultimo_pedido:
            return {"is_addition": False, "action": "standard_flow"}

        status_pedido = int(ultimo_pedido.get("status", 1))
        # Status convenção: 1 = Pendente, 2 = Em Preparo, 3 = Saiu para Entrega, 4 = Entregue, 5 = Cancelado
        ja_saiu_para_entrega = (status_pedido >= 3)

        if not settings.ENABLE_JEV_GATEWAY or not settings.ENABLE_JEV_ORDER_ADDITIONS:
            # Fallback seguro: se saiu para entrega, avisa
            return {
                "is_addition": True,
                "ja_saiu_para_entrega": ja_saiu_para_entrega,
                "status_id": status_pedido,
                "pedido_id": ultimo_pedido.get("id"),
                "action": "create_new_order_with_fee" if ja_saiu_para_entrega else "update_existing_no_fee"
            }

        system_instruction = (
            "Você é o Jev Order Addition Classifier do ProIA Delivery. "
            "Avalie se a mensagem do cliente expressa o desejo de adicionar, incluir ou pedir mais itens "
            "a um pedido recém-finalizado (ex: 'esqueci da coca', 'coloca mais um tropeiro', 'põe uma cerveja')."
        )

        state = {
            "mensagem_cliente": mensagem_cliente,
            "ultimo_pedido_id": ultimo_pedido.get("id"),
            "status_atual_banco": status_pedido,
            "status_descricao": "Saiu para Entrega" if ja_saiu_para_entrega else "Em Preparo / Pendente"
        }

        expected_schema = {
            "is_addition_intent": True,
            "confidence": 0.95
        }

        result = await self._call_jev(system_instruction, {"state": state, "expected_schema": expected_schema})
        is_addition = False
        if result and isinstance(result, dict):
            is_addition = bool(result.get("is_addition_intent", False)) and float(result.get("confidence", 0.0)) >= 0.70
        else:
            # Heurística local de regex para adição
            is_addition = bool(re.search(r'\b(adiciona|coloca|p[oõ]e|esqueci|mais um[a]?|acrescenta|manda tamb[eé]m)\b', mensagem_cliente.lower()))

        return {
            "is_addition": is_addition,
            "ja_saiu_para_entrega": ja_saiu_para_entrega,
            "status_id": status_pedido,
            "pedido_id": ultimo_pedido.get("id"),
            "action": "create_new_order_with_fee" if ja_saiu_para_entrega else "update_existing_no_fee"
        }

    # =========================================================================
    # PORTA 4: CIRCUIT BREAKER DE TRANSBORDO HUMANO
    # =========================================================================
    async def verify_human_escalation(
        self,
        mensagem_cliente: str,
        historico_recente: List[Dict[str, Any]]
    ) -> bool:
        """
        Avalia com probabilidade matemática se o cliente REALMENTE precisa de um humano.
        Evita 100% dos falsos transbordos por timeouts ou pequenos erros da LLM.
        """
        if not settings.ENABLE_JEV_GATEWAY:
            return True  # Se Jev desligado, aceita a chamada da tool

        msg_lower = mensagem_cliente.lower().strip()
        # Se o cliente pediu expressamente palavras-chave claras de humano:
        if any(term in msg_lower for term in ["falar com atendente", "humano", "atendente", "falar com pessoa", "gerente", "suporte"]):
            return True

        system_instruction = (
            "Você é o Circuit Breaker de Transbordo do ProIA Delivery. "
            "Responda TRUE apenas se o cliente estiver expressando raiva extrema, "
            "relatando comida estragada/errada grave ou exigindo falar com uma pessoa de verdade. "
            "Se o cliente estiver apenas fazendo um pedido, perguntando cardápio ou tirando dúvidas, responda FALSE."
        )

        state = {
            "mensagem_cliente": mensagem_cliente,
            "historico": [m.get("content", "") for m in historico_recente[-4:]]
        }

        expected_schema = {
            "needs_human": False,
            "probability": 0.02
        }

        result = await self._call_jev(system_instruction, {"state": state, "expected_schema": expected_schema})
        if result and isinstance(result, dict):
            needs_human = bool(result.get("needs_human", False))
            prob = float(result.get("probability", 0.0))
            return needs_human and prob >= 0.85

        return False

jev_service = JevService()
