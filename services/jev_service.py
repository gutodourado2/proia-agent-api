import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import httpx

from config import settings
from services.supabase_service import supabase_service

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
        self.base_url = "https://openrouter.ai/api/alpha/decisions"
        self.model = settings.JEV_MODEL_NAME
        self.timeout = getattr(settings, "JEV_TIMEOUT_SECONDS", 4.0)

    def _get_api_key(self) -> str:
        key = settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY
        return key.strip().strip('"\'') if key else ""

    @staticmethod
    def _extract_noul_prob(answers: Optional[Dict[str, Any]], key: str) -> Optional[float]:
        """Extrai a probabilidade calibrada (0.0 a 1.0) de uma pergunta do tipo `noul`."""
        if not isinstance(answers, dict) or key not in answers:
            return None

        ans = answers.get(key)
        if isinstance(ans, bool):
            return 1.0 if ans else 0.0
        if isinstance(ans, (int, float)):
            return max(0.0, min(1.0, float(ans)))
        if not isinstance(ans, dict):
            return None

        for direct_key in ("noul", "probability", "confidence", "score"):
            val = ans.get(direct_key)
            if isinstance(val, bool):
                return 1.0 if val else 0.0
            if isinstance(val, (int, float)):
                return max(0.0, min(1.0, float(val)))

        probs = ans.get("probabilities")
        if isinstance(probs, (int, float)) and not isinstance(probs, bool):
            return max(0.0, min(1.0, float(probs)))
        if isinstance(probs, dict):
            for true_key in ("true", True, "yes", "sim", "True"):
                val = probs.get(true_key)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    return max(0.0, min(1.0, float(val)))

        return None

    async def _call_jev(
        self,
        state: Any,
        questions: Dict[str, Any],
        gate_name: str = "decision",
    ) -> Optional[Dict[str, Any]]:
        """
        Executa chamada assíncrona ao Jev no endpoint OpenRouter /api/alpha/decisions
        com timeout curto e degradação segura em caso de falha.
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

        payload = {
            "model": self.model,
            "state": state,
            "questions": questions
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(self.base_url, headers=headers, json=payload)
                if res.status_code != 200:
                    err_msg = f"[JEV] Erro HTTP {res.status_code} na chamada OpenRouter Decisions ({gate_name}): {res.text[:250]}"
                    logger.error(err_msg)
                    try:
                        await supabase_service.registrar_log(
                            "ERROR",
                            err_msg,
                            {"model": self.model, "gate": gate_name, "status": res.status_code},
                        )
                    except Exception:
                        pass
                    return None

                data = res.json()
                answers = data.get("answers") if isinstance(data, dict) else None
                if not isinstance(answers, dict):
                    logger.warning(f"[JEV] Resposta sem campo 'answers' válido ({gate_name}): {str(data)[:200]}")
                    return None

                logger.info(f"[JEV] Decisão tomada com sucesso ({gate_name}): {answers}")
                try:
                    await supabase_service.registrar_log(
                        "INFO",
                        f"[JEV System One] Decisão executada ({self.model})",
                        {"gate": gate_name, "answers": answers, "usage": data.get("usage")},
                    )
                except Exception:
                    pass
                return answers
        except httpx.TimeoutException:
            err_timeout = f"[JEV] Timeout de {self.timeout}s na chamada OpenRouter ({self.model}, {gate_name}). Aplicando fallback."
            logger.warning(err_timeout)
            try:
                await supabase_service.registrar_log("WARN", err_timeout, {"model": self.model, "gate": gate_name})
            except Exception:
                pass
            return None
        except Exception as e:
            err_gen = f"[JEV] Falha ao consultar OpenRouter ({self.model}, {gate_name}): {e}"
            logger.warning(err_gen)
            try:
                await supabase_service.registrar_log("WARN", err_gen, {"model": self.model, "gate": gate_name})
            except Exception:
                pass
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
        Verifica se o pedido está pronto para ser criado e impresso na cozinha,
        evitando fechamento prematuro quando o cliente ainda tira dúvidas ou pede para esperar.
        Em caso de indisponibilidade do Jev, degrada em fail-open para nunca travar vendas.
        """
        if not settings.ENABLE_JEV_GATEWAY or not settings.ENABLE_JEV_PRINT_GATE:
            return True, "jev_disabled_bypass"

        msg_lower = (mensagem_cliente or "").lower().strip()
        confirmacoes_regex = (
            r'\b(sim|confirmo|confirmado|pode fechar|pode mandar|isso|isso mesmo|manda ver|'
            r't[aá] certo|fechou|ok|beleza|blz|pix|cart[aã]o|dinheiro|d[eé]bito|cr[eé]dito|manda)\b'
        )
        tem_confirmacao_explicita = bool(re.search(confirmacoes_regex, msg_lower))

        # Heurística local rápida apenas para hesitações/dúvidas claras sem confirmação
        frases_hesitacao = (
            "espera", "pera", "calma", "trocar", "mudar", "quanto fica",
            "qual o valor", "tem ", "vocês tem", "voces tem", "cancelar", "cancela",
        )
        eh_negacao_simples = msg_lower in ("não", "nao", "ainda não", "ainda nao")
        if eh_negacao_simples or (not tem_confirmacao_explicita and any(msg_lower.startswith(p) for p in frases_hesitacao)):
            return False, "Cliente fez pergunta, pediu alteração ou pediu para esperar antes de fechar."

        state = {
            "tipo_entrega": tipo_entrega,
            "ultimo_resumo_ia": ultimo_resumo_ia[-350:] if ultimo_resumo_ia else "",
            "mensagem_cliente": mensagem_cliente,
            "nome_cliente": carrinho.get("p_nome_cliente", ""),
            "forma_pagamento": carrinho.get("p_forma_pagamento", ""),
            "observacoes": carrinho.get("p_observacoes", ""),
        }

        questions = {
            "is_ready_to_finalize": {
                "type": "noul",
                "instructions": (
                    "Avalie se a mensagem do cliente confirma o pedido ou responde com o dado final "
                    "de fechamento solicitado (ex: 'Sim', 'Ok', 'Beleza', 'Pode fechar', 'PIX', "
                    "'Cartão', 'Dinheiro', ou horário de retirada), permitindo gravar e imprimir o pedido."
                ),
                "criteria": {
                    "true": (
                        "Cliente confirmou o pedido, aceitou o resumo final, ou informou forma de pagamento "
                        "ou horário de retirada/entrega para concluir."
                    ),
                    "false": (
                        "Cliente está fazendo pergunta sobre cardápio/preço, pedindo para esperar, "
                        "alterando itens ainda em aberto ou cancelando."
                    ),
                },
            }
        }

        answers = await self._call_jev(state, questions, gate_name="verify_order_readiness")
        prob = self._extract_noul_prob(answers, "is_ready_to_finalize")

        if prob is not None:
            if prob >= 0.65 or tem_confirmacao_explicita:
                return True, f"jev_confirmed (prob={prob:.2f})"
            return False, f"Jev identificou pedido ainda em aberto antes da impressão (prob={prob:.2f})."

        # Fail-open seguro se JEV falhar: só bloqueia se terminar com pergunta clara sem confirmação
        if msg_lower.endswith("?") and not tem_confirmacao_explicita:
            return False, "Cliente enviou dúvida pendente antes de confirmar o pedido."
        return True, "jev_unavailable_fail_open"

    # =========================================================================
    # PORTA 2: VALIDAÇÃO DE ENDEREÇO 100% POR ESCRITO & ANTI-PREJUÍZO DE FRETE
    # =========================================================================
    async def validate_address_text(
        self,
        raw_address_text: str,
        cidade_loja: str = "Luís Eduardo Magalhães - BA"
    ) -> Dict[str, Any]:
        """
        Valida se o endereço foi enviado por extenso com Rua, Número (ou quadra/lote) e Bairro.
        Rejeita PINs isolados do WhatsApp e endereços incompletos que causariam prejuízo no frete.
        """
        raw_clean = (raw_address_text or "").strip()
        cidade_nome = cidade_loja.split("-")[0].strip().lower()
        ja_tem_cidade = bool(cidade_nome and cidade_nome in raw_clean.lower())
        clean_address = raw_clean if ja_tem_cidade or not raw_clean else f"{raw_clean}, {cidade_loja}"

        fallback_default = {
            "is_valid": True,
            "is_pin_only": False,
            "clean_address": clean_address,
            "delivery_notes": "",
            "reason": "bypass_jev"
        }

        if not settings.ENABLE_JEV_GATEWAY or not settings.ENABLE_JEV_ADDRESS_GATE:
            return fallback_default

        raw_lower = raw_clean.lower()
        eh_pin_ou_link = (
            any(term in raw_lower for term in ["location", "maps.google", "maps.app", "mandei a localizacao", "mandei a localização"])
            or bool(re.search(r'\bpin\b', raw_lower))
        ) and not re.search(r'\d+', raw_clean)
        if eh_pin_ou_link:
            return {
                "is_valid": False,
                "is_pin_only": True,
                "clean_address": "",
                "delivery_notes": "",
                "reason": "Cliente enviou PIN ou link de localização. O delivery exige endereço completo por escrito para precisão de entrega."
            }

        tem_numero = (
            bool(re.search(r'\d+', raw_clean))
            or any(term in raw_lower for term in ["s/n", "sem numero", "sem número", "lote", "quadra"])
        )
        tem_indicador_logradouro = bool(re.search(
            r'\b(rua|r\.|av|av\.|avenida|travessa|tv\.|alameda|quadra|qd|lote|lt|estrada|rodovia|praça|praca|setor|condom[ií]nio|edif[ií]cio|bloco|lot\.|loteamento)\b',
            raw_lower
        ))
        eh_apenas_referencia_vaga = (
            any(term in raw_lower for term in ["do lado d", "ao lado d", "em frente a", "na frente d", "perto d", "atrás d", "atras d", "próximo a", "proximo a"])
            and not tem_indicador_logradouro
        )
        if eh_apenas_referencia_vaga:
            return {
                "is_valid": False,
                "is_pin_only": False,
                "clean_address": "",
                "delivery_notes": raw_clean,
                "reason": "Cliente enviou apenas ponto de referência sem o nome oficial da Rua/Avenida e Bairro."
            }

        state = {
            "cidade_oficial": cidade_loja,
            "texto_endereco": raw_clean
        }

        questions = {
            "is_pin_or_link_only": {
                "type": "noul",
                "instructions": "O texto é apenas um link/PIN de localização ou referência vaga sem logradouro escrito?",
                "criteria": {
                    "true": "Contém apenas link de mapa, coordenadas, ou apenas ponto de referência (ex: 'do lado de fulano') sem nome de rua/avenida.",
                    "false": "Contém endereço escrito com nome real de rua/avenida/quadra/travessa."
                }
            },
            "has_street_and_number": {
                "type": "noul",
                "instructions": "O endereço escrito possui nome real de logradouro (rua/av/quadra) E número predial (ou s/n, lote, bloco)?",
                "criteria": {
                    "true": "Identifica claramente o nome da rua/avenida/setor e o número da casa, apartamento, quadra ou lote.",
                    "false": "Falta número da residência/lote, falta nome da rua, ou é apenas ponto de referência."
                }
            }
        }

        answers = await self._call_jev(state, questions, gate_name="validate_address_text")
        pin_prob = self._extract_noul_prob(answers, "is_pin_or_link_only")
        num_prob = self._extract_noul_prob(answers, "has_street_and_number")

        if pin_prob is not None and pin_prob >= 0.80 and (not tem_numero or not tem_indicador_logradouro):
            return {
                "is_valid": False,
                "is_pin_only": True,
                "clean_address": "",
                "delivery_notes": "",
                "reason": "Jev detectou apenas referência ou localização sem endereço por escrito completo (Rua, Número e Bairro)."
            }

        if not tem_numero and (num_prob is None or num_prob < 0.65):
            return {
                "is_valid": False,
                "is_pin_only": False,
                "clean_address": "",
                "delivery_notes": "",
                "reason": "Endereço sem número predial ou lote. Necessário solicitar Rua, Número e Bairro para cálculo exato."
            }

        if num_prob is not None and num_prob < 0.35 and not tem_indicador_logradouro:
            return {
                "is_valid": False,
                "is_pin_only": False,
                "clean_address": "",
                "delivery_notes": raw_clean,
                "reason": "Endereço sem nome claro de Rua/Avenida e Bairro."
            }

        return {
            "is_valid": True,
            "is_pin_only": False,
            "clean_address": clean_address,
            "delivery_notes": "",
            "reason": f"Endereço válido (jev_num_prob={num_prob:.2f})" if num_prob is not None else "fallback_numero_detectado"
        }

    # =========================================================================
    # PORTA 3: GESTÃO INTELIGENTE DE ADIÇÕES & CONSULTA DE STATUS DO PEDIDO
    # =========================================================================
    async def evaluate_order_addition(
        self,
        mensagem_cliente: str,
        ultimo_pedido: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Identifica se o cliente está querendo adicionar/alterar itens em um pedido recente DE HOJE,
        consultar o status de entrega ou fazer um novo pedido separado, verificando se o motoboy
        já saiu (Status >= 3) ou se o pedido ainda está na cozinha (Status < 3).
        """
        if not ultimo_pedido:
            return {"is_addition": False, "is_new_separate_order": False, "is_status_query": False, "action": "no_active_order_today"}

        status_pedido = int(ultimo_pedido.get("status", 1))
        # Status convenção: 1 = Novo/Aceito, 2 = Em Preparo, 3 = Saiu para Entrega (Despacho), 4 = Entregue, 5 = Cancelado
        ja_saiu_para_entrega = (status_pedido >= 3)
        endereco_atual = str(ultimo_pedido.get("endereco_entrega", "") or "")
        modalidade_atual = "retirada" if "retirada" in endereco_atual.lower() else "entrega"
        minutos_desde_criacao = float(ultimo_pedido.get("minutos_desde_criacao", 0) or 0)

        msg_lower = (mensagem_cliente or "").lower()
        regex_adicao = bool(re.search(
            r'\b(adiciona|coloca|p[oõ]e|esqueci|mais um[a]?|acrescenta|manda tamb[eé]m|inclu[ia]|junto com|no mesmo pedido|troca|muda)\b',
            msg_lower
        ))
        regex_status = bool(re.search(
            r'\b(j[aá] saiu|cad[eê]|demora|t[aá] vindo|meu pedido|andamento|motoboy|entregador|previs[aã]o)\b',
            msg_lower
        ))
        regex_novo_separado = bool(re.search(
            r'\b(outro pedido|pedido separado|novo pedido)\b',
            msg_lower
        ))

        if not settings.ENABLE_JEV_GATEWAY or not settings.ENABLE_JEV_ORDER_ADDITIONS:
            return {
                "is_addition": regex_adicao and not regex_novo_separado,
                "is_new_separate_order": regex_novo_separado,
                "is_status_query": regex_status,
                "ja_saiu_para_entrega": ja_saiu_para_entrega,
                "status_id": status_pedido,
                "pedido_id": ultimo_pedido.get("id"),
                "modalidade_atual": modalidade_atual,
                "minutos_desde_criacao": minutos_desde_criacao,
                "action": "create_new_order_with_fee" if (ja_saiu_para_entrega or regex_novo_separado) else "update_existing_no_fee"
            }

        state = {
            "mensagem_cliente": mensagem_cliente,
            "ultimo_pedido_id": ultimo_pedido.get("id"),
            "minutos_desde_criacao": round(minutos_desde_criacao, 1),
            "modalidade_pedido_atual": modalidade_atual,
            "status_atual_banco": status_pedido,
            "status_descricao": "Saiu para Entrega / Finalizado" if ja_saiu_para_entrega else "Em Preparo / Na Cozinha"
        }

        questions = {
            "is_addition_intent": {
                "type": "noul",
                "instructions": (
                    "O cliente quer adicionar, incluir ou alterar algum item/observação/pagamento no pedido "
                    "que ele acabou de fazer hoje (ex: 'esqueci a coca', 'coloca mais um tropeiro', 'manda uma pepsi junto')?"
                ),
                "criteria": {
                    "true": "Mensagem pede para acrescentar, incluir ou alterar item no pedido recente de hoje.",
                    "false": "É apenas saudação, dúvida geral de cardápio, consulta de status ou novo pedido separado."
                }
            },
            "is_status_query": {
                "type": "noul",
                "instructions": "O cliente está perguntando se o pedido já saiu para entrega ou qual o andamento do pedido?",
                "criteria": {
                    "true": "Pergunta sobre andamento, entrega, motoboy ou tempo de espera do pedido.",
                    "false": "Não está perguntando andamento da entrega."
                }
            }
        }

        answers = await self._call_jev(state, questions, gate_name="evaluate_order_addition")
        prob_add = self._extract_noul_prob(answers, "is_addition_intent")
        prob_status = self._extract_noul_prob(answers, "is_status_query")

        if prob_add is not None:
            is_addition = (prob_add >= 0.65) and not regex_novo_separado
        else:
            is_addition = regex_adicao and not regex_novo_separado

        if prob_status is not None:
            is_status_query = prob_status >= 0.65
        else:
            is_status_query = regex_status

        return {
            "is_addition": is_addition,
            "is_new_separate_order": regex_novo_separado,
            "is_status_query": is_status_query,
            "ja_saiu_para_entrega": ja_saiu_para_entrega,
            "status_id": status_pedido,
            "pedido_id": ultimo_pedido.get("id"),
            "modalidade_atual": modalidade_atual,
            "minutos_desde_criacao": minutos_desde_criacao,
            "action": (
                "create_new_order_with_fee"
                if (ja_saiu_para_entrega or regex_novo_separado)
                else ("update_existing_no_fee" if is_addition else ("answer_status" if is_status_query else "standard_flow"))
            )
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
        Avalia com probabilidade matemática se o cliente precisa de um atendente humano.
        Evita falsos transbordos quando o Jev está operacional, mas degrada em fail-open (True)
        se o Jev estiver indisponível para nunca bloquear atendimentos urgentes ou cancelamentos.
        """
        if not settings.ENABLE_JEV_GATEWAY:
            return True

        msg_lower = (mensagem_cliente or "").lower().strip()
        palavras_transbordo_direto = (
            "falar com atendente", "humano", "atendente", "falar com pessoa",
            "gerente", "suporte", "cancelar", "cancelamento", "estorno",
            "reembolso", "nao chegou", "não chegou", "procon", "devolver", "reclamar"
        )
        if any(term in msg_lower for term in palavras_transbordo_direto):
            return True

        state = {
            "mensagem_cliente": mensagem_cliente,
            "historico": [m.get("content", "") for m in historico_recente[-4:]]
        }

        questions = {
            "needs_human": {
                "type": "noul",
                "instructions": (
                    "O cliente realmente precisa de intervenção humana (ex: cancelamento de pedido já em rota, "
                    "reembolso, pedido atrasado/errado/estragado, reclamação grave ou pedido explícito de atendente)?"
                ),
                "criteria": {
                    "true": "Cancelamento, reembolso, problema na entrega/comida, insatisfação séria ou solicitação de atendente.",
                    "false": "Cliente está apenas fazendo pedido comum, perguntando preço/cardápio ou conversando normalmente."
                }
            }
        }

        answers = await self._call_jev(state, questions, gate_name="verify_human_escalation")
        prob = self._extract_noul_prob(answers, "needs_human")
        if prob is not None:
            return prob >= 0.65

        # Fail-open: nunca barrar escalonamento humano decidido pela IA se o JEV falhar
        return True

jev_service = JevService()

