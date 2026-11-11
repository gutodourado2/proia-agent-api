import asyncio
import json
import re
import time
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import httpx
from openai import AsyncOpenAI
from config import settings
from profiles import get_active_profile
from services.supabase_service import supabase_service
from services.evolution_service import evolution_service
from services.jev_service import jev_service



logger = logging.getLogger("agent_service")

# ══════════════════════════════════════════════════════════
# FERRAMENTAS (Tools) — OpenAI Function Calling
# ══════════════════════════════════════════════════════════
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "buscar_produtos",
            "description": "Busca produtos no cardapio por nome, sinonimo ou categoria (ex: refri, refrigerante, pepsi, guarana, frango, marmita). SEMPRE chame esta ferramenta para consultar a existencia de qualquer produto no banco.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_empresa_id": {"type": "string", "description": "ID numerico (ex: 43) ou UUID da empresa"},
                    "p_busca": {"type": "string", "description": "Termo de busca ou sinonimo (ex: refri, pepsi, guarana, frango, carne)"},
                    "p_categoria": {"type": "string", "description": "Nome da categoria (ex: Refrigerantes, Carnes, Frango assado, Marmita)"},
                    "p_apenas_disponivel": {"type": "boolean", "default": True}
                },
                "required": ["p_empresa_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "listar_categorias",
            "description": "Lista todas as categorias de produtos disponiveis na loja (ex: Refrigerantes, Carnes, Complementos, Marmita, etc).",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_empresa_id": {"type": "string", "description": "ID numerico ou UUID da empresa"}
                },
                "required": ["p_empresa_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "enviar_foto_produto",
            "description": "Envia a foto REAL do produto direto no WhatsApp do cliente. Voce DEVE primeiro chamar buscar_produtos para obter o produto_id CORRETO retornado pelo banco.",
            "parameters": {
                "type": "object",
                "properties": {
                    "produto_id": {"type": "integer", "description": "ID numerico EXATO do produto retornado por buscar_produtos"},
                    "image_url": {"type": "string", "description": "URL da imagem (opcional)"},
                    "caption": {"type": "string", "description": "Legenda (ex: Pepsi 1L - R$ 11,00)"}
                },
                "required": ["produto_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "info_empresa",
            "description": "Retorna informacoes da empresa como horario de funcionamento, endereco e regras.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_empresa_id": {"type": "string", "description": "ID numerico ou UUID da empresa"}
                },
                "required": ["p_empresa_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_enderecos_cliente",
            "description": "Busca enderecos salvos do cliente pelo telefone. Chame ANTES de pedir um novo endereco.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_telefone": {"type": "string", "description": "Numero do cliente (ex: 557799999999)"}
                },
                "required": ["p_telefone"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calcular_entrega_completa",
            "description": "Calcula distancia e taxa de entrega via Google Maps entre a loja e o endereco do cliente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_empresa_id": {"type": "string", "description": "ID numerico ou UUID da empresa"},
                    "p_endereco": {"type": "string", "description": "Endereco completo do cliente"}
                },
                "required": ["p_empresa_id", "p_endereco"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_adicionais_produto",
            "description": "Busca acompanhamentos e cortesias disponiveis para um produto (ex: arroz, feijao tropeiro, macarrao como cortesias gratis, mandioca como adicional pago).",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_produto_id": {"type": "integer", "description": "ID do produto (ex: 1113 para Frango Inteiro)"}
                },
                "required": ["p_produto_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "criar_pedido_completo",
            "description": "Cria o pedido final no banco com itens, adicionais, endereco e pagamento. OBRIGATORIO chamar quando o cliente informar o horario da retirada ou confirmar o pagamento da entrega.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_empresa_id": {"type": "integer"},
                    "p_itens": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "produto_id": {"type": "integer"},
                                "quantidade": {"type": "integer"},
                                "adicionais": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "description": "Array com os opcao_adicional_id retornados por buscar_adicionais_produto"
                                }
                            },
                            "required": ["produto_id", "quantidade"]
                        }
                    },
                    "p_endereco_entrega": {"type": "string", "description": "Endereco completo de entrega OU 'Retirada na loja' para retiradas"},
                    "p_forma_pagamento": {"type": "string", "description": "Forma de pagamento ('Pagamento na retirada (Balcão)', 'Cartão', 'Dinheiro', 'PIX')"},
                    "p_taxa_entrega": {"type": "number", "description": "Taxa de entrega (0 para retirada na loja)"},
                    "p_latitude_entrega": {"type": "number"},
                    "p_longitude_entrega": {"type": "number"},
                    "p_distancia_km": {"type": "number"},
                    "p_telefone_cliente": {"type": "string"},
                    "p_nome_cliente": {"type": "string", "description": "Nome do cliente"},
                    "p_observacoes": {"type": "string", "description": "Horario de retirada/entrega (ex: Horario de retirada: 12:00h)"},
                    "p_forcar_novo": {"type": "boolean", "description": "Passe true para forcar a criacao de um NOVO PEDIDO (#ID Novo) quando for uma nova compra separada, mudar de retirada para entrega, ou se o pedido anterior tiver mais de 20 minutos."}
                },
                "required": ["p_empresa_id", "p_itens", "p_endereco_entrega", "p_forma_pagamento", "p_taxa_entrega", "p_telefone_cliente", "p_nome_cliente"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_pedido",
            "description": "Consulta status de um pedido existente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_pedido_id": {"type": "integer", "description": "ID do pedido"}
                },
                "required": ["p_pedido_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "atualizar_pedido_completo",
            "description": "Atualiza itens, observacoes ou taxa de entrega de um pedido existente mantendo o mesmo #ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_pedido_id": {"type": "integer"},
                    "p_empresa_id": {"type": "string"},
                    "p_itens": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "produto_id": {"type": "integer"},
                                "quantidade": {"type": "integer"},
                                "adicionais": {
                                    "type": "array",
                                    "items": {"type": "integer"}
                                }
                            },
                            "required": ["produto_id", "quantidade"]
                        }
                    },
                    "p_observacoes": {"type": "string"},
                    "p_forma_pagamento": {"type": "string"},
                    "p_troco_para": {"type": "number"},
                    "p_taxa_entrega": {"type": "number"},
                    "p_endereco_entrega": {"type": "string"},
                    "p_nome_cliente": {"type": "string"}
                },
                "required": ["p_pedido_id", "p_empresa_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "escalar_atendimento_humano",
            "description": "ATENCAO: USAR APENAS SE O CLIENTE PEDIR EXPLICITAMENTE PARA FALAR COM UM ATENDENTE HUMANO.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_empresa_id": {"type": "string"},
                    "p_telefone": {"type": "string"},
                    "p_nome_cliente": {"type": "string"},
                    "p_motivo": {"type": "string"},
                    "p_mensagem_contexto": {"type": "string"},
                    "p_instancia": {"type": "string"}
                },
                "required": ["p_empresa_id", "p_telefone", "p_nome_cliente", "p_motivo", "p_instancia"]
            }
        }
    }
]

# ══════════════════════════════════════════════════════════
# SYSTEM PROMPT (OFICIAL GEMINI - INTACTO E APRIMORADO)
# ══════════════════════════════════════════════════════════
SYSTEM_PROMPT_BODY = """
Voce e o atendente virtual humano do __EMPRESA_NOME__, especialista em vendas rápidas, objetivas e atendimento via WhatsApp.

⚡ DIRETIVA SUPREMA DE FORMATAÇÃO E ECONOMIA DE TOKENS:
- Responda em no máximo 1 a 3 LINHAS por mensagem. Seja ULTRA-OBJETIVO, DIRETO e EXTREMAMENTE HUMANO.
- NUNCA envie blocos de texto longos, enrolação ou saudações repetitivas.
- Use negritos simples (*palavra*) de forma pontual para destacar valores, itens e números de pedidos. NUNCA use asterisco duplo (**texto*) incorreto.
- Use no máximo 1 ou 2 emojis bem posicionados se necessário.

👤 REGRA ABSOLUTA DE NOME DO CLIENTE:
- É OBRIGATÓRIO perguntar ou confirmar o nome de quem vai receber (entrega) ou retirar o pedido (ex: *"Qual o seu nome ou de quem vai receber/retirar o pedido?"*).
- Registre o nome informado no campo `p_nome_cliente` ao criar ou atualizar o pedido.

🔢 REGRA ABSOLUTA DE MATEMÁTICA E VALORES (CÁLCULO PRECISO & PIX DIFERENÇA):
- SOME OS VALORES COM PRECISÃO ABSOLUTA! NUNCA invente ou erre a soma dos produtos!
- SE O PEDIDO JÁ FOI PAGO VIA PIX E O CLIENTE ADICIONAR UM ITEM NOVO (EX: Guaraná R$ 11,00):
  * Mantenha o pedido existente, calcule APENAS A DIFERENÇA A PAGAR (R$ 11,00) e envie a chave PIX solicitando o pagamento apenas desse valor adicional!
  * Ao receber o comprovante adicional, valide e registre em `p_observacoes`: "PEDIDO PAGO VIA PIX (Comprovante Validado - Adicional R$ 11,00) - Pago".

🛑 REGRA DE OURO DE INTENÇÃO: NOVO PEDIDO (#ID NOVO) VS ATUALIZAÇÃO DO PEDIDO ANTERIOR:
1. TIPO DE ENTREGA DIFERENTE (RETIRADA VS ENTREGA):
   - Se o cliente já tem um pedido de RETIRADA e faz uma compra para ENTREGA (ou vice-versa), são entregas distintas! CHAME `criar_pedido_completo` COM `p_forcar_novo: true` PARA GERAR UM NOVO #ID!
2. JANELA DE TEMPO DE 20 MINUTOS & PEDIDO QUE JÁ SAIU (STATUS 3 OU > 20 MIN):
   - Se o pedido anterior foi feito HÁ MAIS DE 20 MINUTOS ou já saiu para entrega (Status 3), JAMAIS altere o pedido anterior! Informe que o anterior já emitiu e CHAME `criar_pedido_completo` COM `p_forcar_novo: true` PARA GERAR UM NOVO #ID!
3. EXPRESSÕES DE NOVO PEDIDO VS ADIÇÃO:
   - Se o cliente disser "manda 1/2kg de costela", "quero outro frango", "faz outro pedido", CHAME `criar_pedido_completo` COM `p_forcar_novo: true`.
   - Se disser "adiciona uma coca no meu pedido", "esqueci o refrigerante", CHAME `atualizar_pedido_completo`. SÓ chame a ferramenta 1 VEZ quando houver mudança real de itens!

📌 REGRA ABSOLUTA DE SINÔNIMO PARA "RESERVA / RESERVAR":
- Quando o cliente disser "Reserva um frango", "Reservar para 12:40", "Deixa reservado":
  Entenda que "RESERVAR" É UM PEDIDO NORMAL DE RETIRADA NA LOJA! Pegue o NOME, o horário e chame `criar_pedido_completo`.

🔄 FLUXO DE ATENDIMENTO (RETIRADA VS ENTREGA):

🛍️ SE O CLIENTE PEDIR PARA RETIRADA NA LOJA OU RESERVA:
- Confirme o item, pergunte o nome do cliente e o horário da retirada.
- NUNCA pergunte sobre bebidas ou acompanhamentos extras em retirada! O cliente comprará o que quiser no balcão.
- Assim que o cliente informar o horário (ex: "12:40h") e o nome, EXECUTE `criar_pedido_completo` IMEDIATAMENTE e responda com o Pedido #ID!

🛵 SE O CLIENTE PEDIR PARA ENTREGA:
- 🛑 REGRA ABSOLUTA DE ENDEREÇO POR ESCRITO E FRETE OBRIGATÓRIO VIA TOOL:
  * É PROIBIDO inventar, supor ou chutar taxa de entrega (ex: R$ 8,00 ou taxa mínima)!
  * O pedido para entrega SÓ PODE SER FINALIZADO após o cliente enviar o ENDEREÇO COMPLETO POR ESCRITO (Rua, Número e Bairro), você executar a tool `calcular_entrega_completa` com o endereço digitado e repassar o valor exato retornado por ela para o cliente aprovar!
- Confirme os itens, adicionais, pergunte se deseja bebida e confirme o endereço digitado (`calcular_entrega_completa`). Se o frete já tiver sido calculado com sucesso e o cliente apenas confirmar ("Certo", "Pode fechar", "Sim") ou complementar referências, NÃO recalcule o frete em loop: use a taxa já calculada e execute `criar_pedido_completo`!
- Pergunte a forma de pagamento: *"Como prefere pagar: PIX, Cartão ou Dinheiro?"*.
- 📲 REGRA DA CHAVE PIX & COMPROVANTE:
  * Se o cliente disser apenas "PIX", assuma PIX na entrega, NÃO envie chave PIX e EXECUTE `criar_pedido_completo` IMEDIATAMENTE!
  * A Chave PIX SÓ É ENVIADA SE O CLIENTE PEDIR EXPLICITAMENTE. Se enviou a chave, SÓ gere o pedido após o recebimento e validação do comprovante.
"""

# ══════════════════════════════════════════════════════════
# SYSTEM PROMPT (EXCLUSIVO DO MODO TESTER DEEPSEEK STAGING)
# ══════════════════════════════════════════════════════════
TESTER_SYSTEM_PROMPT_BODY = """
Você é o atendente virtual inteligente do __EMPRESA_NOME__ (Modo Staging/Calibração DeepSeek), especializado em vendas e atendimento rápido via WhatsApp.

⚡ DIRETIVAS SUPREMAS DE RESPOSTA E FORMATAÇÃO (DEEPSEEK V4 FLASH):
- Responda em no máximo 1 a 3 LINHAS por mensagem. Seja ULTRA-OBJETIVO, SIMPÁTICO e HUMANO.
- NUNCA envie blocos de texto longos, enrolação ou saudações repetitivas.
- Formate valores e números de pedidos com negrito simples (*palavra*). NUNCA use asterisco duplo incorreto (**texto*).

👤 CONFIRMAÇÃO DO NOME DO CLIENTE:
- É OBRIGATÓRIO perguntar ou confirmar o nome de quem vai receber ou retirar o pedido (ex: *"Qual o seu nome ou de quem vai receber/retirar o pedido?"*).

🔢 CÁLCULO PRECISO DE VALORES & DIFERENÇA PIX:
- SOME OS VALORES COM PRECISÃO ABSOLUTA! NUNCA invente ou erre a soma dos produtos!
- Se o pedido já foi pago via PIX e o cliente adicionar um novo item (ex: Guaraná R$ 11,00), cobre apenas a *DIFERENÇA A PAGAR (R$ 11,00)*!

🛑 REGRA INTELIGENTE DE NOVO PEDIDO (#ID NOVO) VS ATUALIZAÇÃO:
1. RETIRADA VS ENTREGA: Se o cliente tem um pedido de RETIRADA e pede outro para ENTREGA, chame `criar_pedido_completo` com `p_forcar_novo: true` para criar um NOVO #ID!
2. JANELA DE 20 MINUTOS & PEDIDO QUE JÁ SAIU (STATUS 3 OU > 20 MIN): Se o pedido anterior foi feito há mais de 20 minutos ou já saiu para entrega, informe que o anterior já foi embalado e chame `criar_pedido_completo` com `p_forcar_novo: true`!
3. EXPRESSÕES DE NOVO PEDIDO VS ADIÇÃO:
   - "Manda 1/2kg de costela", "quero outro frango", "faz outro pedido" ➔ Chame `criar_pedido_completo` com `p_forcar_novo: true`.
   - "Adiciona uma coca no meu pedido", "esqueci o refrigerante" ➔ Chame `atualizar_pedido_completo` (apenas 1 vez quando houver alteração real).

🍱 REGRA MANDATÓRIA DE ADICIONAIS & CORTESIAS NO BANCO (`adicionais: [1, 3]`):
- Quando o produto possuir cortesias ou adicionais (ex: Frango Inteiro acompanha Feijão Tropeiro ou Arroz):
- É ESTRITAMENTE OBRIGATÓRIO passar o array `adicionais: [opcao_adicional_id]` dentro de cada objeto de `p_itens` ao chamar `criar_pedido_completo`!
- Exemplo de p_itens: `[{"produto_id": 1113, "quantidade": 1, "adicionais": [3]}]` (onde 3 é o ID da opção Feijão Tropeiro).
- NUNCA passe adicionais em branco se o cliente tiver escolhido um acompanhamento cortesia ou pago!

🛵 REGRA DE ENTREGA E FRETE POR ESCRITO:
- É PROIBIDO calcular taxa de frete por localização GPS do WhatsApp (`locationMessage`).
- É PROIBIDO inventar ou chutar taxa de entrega (ex: R$ 8,00 ou frete mínimo).
- Para entregas, peça o ENDEREÇO COMPLETO POR ESCRITO (Rua, Número e Bairro), execute a tool `calcular_entrega_completa` com o endereço digitado e confirme o valor total exato com o cliente antes de criar o pedido. Se a taxa já tiver sido calculada com sucesso e o cliente apenas confirmar ou enviar referências, use o valor já calculado e chame `criar_pedido_completo` sem recalcular.

📲 REGRA DA CHAVE PIX & COMPROVANTE:
- SÓ envie a chave PIX se o cliente pedir explicitamente (ex: "Qual a chave PIX?"). Se pedir, envie a chave e SÓ gere o pedido após o recebimento e validação do comprovante.
"""

class AgentService:
    def __init__(self):
        # Cache em memória para cálculo de frete por sessão (evita chamadas duplicadas ao Google Maps e garante taxa/lat/lng no fechamento)
        self._delivery_cache: Dict[str, Dict[str, Any]] = {}

    def _get_cached_delivery(self, session_id: str, raw_address: str = "") -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        cached = self._delivery_cache.get(session_id)
        if not cached:
            return None
        if (time.monotonic() - cached.get("ts", 0)) > 1800:  # TTL 30 minutos
            self._delivery_cache.pop(session_id, None)
            return None
        if raw_address:
            norm_new = re.sub(r'\W+', '', raw_address.lower())
            norm_old = re.sub(r'\W+', '', str(cached.get("raw_address", "")).lower())
            norm_fmt = re.sub(r'\W+', '', str(cached.get("endereco_formatado", "")).lower())
            if norm_new and norm_old and (norm_new in norm_old or norm_old in norm_new or (norm_fmt and norm_new in norm_fmt)):
                return cached
            return None
        return cached

    def _set_cached_delivery(self, session_id: str, raw_address: str, calc_res: Dict[str, Any]):
        if not session_id or not isinstance(calc_res, dict) or not calc_res.get("sucesso"):
            return
        self._delivery_cache[session_id] = {
            "raw_address": raw_address,
            "endereco_formatado": calc_res.get("endereco_formatado") or raw_address,
            "taxa_entrega": float(calc_res.get("taxa_entrega") or 0),
            "latitude": float(calc_res.get("latitude") or 0),
            "longitude": float(calc_res.get("longitude") or 0),
            "distancia_km": float(calc_res.get("distancia_km") or 0),
            "result": calc_res,
            "ts": time.monotonic()
        }

    def get_client_for_model(self, target_model: Optional[str] = None):
        model_name = target_model or settings.MODEL_NAME
        # Tratamento de segurança para nomes de modelo incorretos no OpenRouter:
        if "deepseek-v4-flash-0423" in model_name:
            model_name = "deepseek/deepseek-v4-flash"

        # Se for um modelo nativo OpenAI para fallback (ex: gpt-4o, gpt-4o-mini)
        if model_name in ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"]:
            client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            return client, model_name

        if "/" in model_name or settings.LLM_PROVIDER.lower() == "openrouter" or (settings.OPENROUTER_API_KEY and not settings.OPENAI_API_KEY):
            api_key = (settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY).strip().strip("\"\x27")
            client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
                default_headers={
                    "HTTP-Referer": "https://proia.com.br",
                    "X-Title": "ProIA Multi-Model Agent"
                }
            )
            return client, model_name
        else:
            client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            return client, model_name

    async def get_chat_history(self, session_id: str, limit: int = 12, max_hours: int = 6) -> List[Dict[str, Any]]:
        """
        Carrega apenas o histórico da SESSÃO ATIVA DE HOJE (últimas `max_hours` horas, limite enxuto de 12 msgs).
        Evita que pedidos de dias anteriores (ex: #1140) contaminem o contexto atual e reduz em >50% o gasto de tokens.
        """
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=max_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = (
            f"{supabase_service.base_url}/rest/v1/n8n_chat_histories"
            f"?session_id=eq.{session_id}"
            f"&created_at=gte.{cutoff_iso}"
            f"&order=id.desc&limit={limit}"
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(url, headers=supabase_service.headers)
                if res.status_code != 200:
                    # Fallback seguro caso a coluna created_at ainda não esteja visível no cache do PostgREST
                    fallback_url = f"{supabase_service.base_url}/rest/v1/n8n_chat_histories?session_id=eq.{session_id}&order=id.desc&limit={limit}"
                    res = await client.get(fallback_url, headers=supabase_service.headers)
                data = res.json()
                if not isinstance(data, list):
                    return []
                data.reverse()
                messages = []
                for row in data:
                    msg = row.get("message", {})
                    role = "user" if msg.get("type") == "human" else "assistant"
                    content = msg.get("content", "")
                    if content:
                        clean_content = re.sub(r'!\[.*?\]\([^\)]+\)', '', content).strip()
                        # Deduplicar mensagens consecutivas idênticas do mesmo remetente
                        if messages and messages[-1]["role"] == role and messages[-1]["content"] == clean_content:
                            continue
                        messages.append({"role": role, "content": clean_content})
                return messages
            except Exception as e:
                logger.error(f"Erro ao ler n8n_chat_histories: {e}")
                return []

    async def save_message_to_history(self, session_id: str, role: str, content: str):
        url = f"{supabase_service.base_url}/rest/v1/n8n_chat_histories"
        msg_type = "human" if role == "user" else "ai"
        clean_content = re.sub(r'!\[.*?\]\([^\)]+\)', '', content).strip()
        payload = {
            "session_id": session_id,
            "message": {
                "type": msg_type,
                "content": clean_content,
                "additional_kwargs": {"timestamp": datetime.now(timezone.utc).isoformat()},
                "response_metadata": {}
            }
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                await client.post(url, headers=supabase_service.headers, json=payload)
            except Exception as e:
                logger.error(f"Erro ao salvar mensagem no historico: {e}")

    async def execute_tool(
        self,
        name: str,
        args: Dict[str, Any],
        default_user_id: str = "",
        instance: str = "",
        remote_jid: str = "",
        user_message: str = "",
        history: Optional[List[Dict[str, Any]]] = None,
        cidade_loja: str = "Luís Eduardo Magalhães - BA",
        session_id: str = "",
        pedido_ativo_hoje: Optional[Dict[str, Any]] = None
    ) -> str:
        try:
            if "p_empresa_id" in args:
                args["p_empresa_id"] = default_user_id or str(args["p_empresa_id"])

            if name == "enviar_foto_produto":
                img_url = args.get("image_url", "")
                cap = args.get("caption", "")
                produto_id = args.get("produto_id")

                if not img_url and produto_id:
                    prod_data = await supabase_service.get_produto_imagem(int(produto_id))
                    if prod_data:
                        img_url = prod_data.get("imagem_url", "")
                        if not cap:
                            cap = f"{prod_data.get('produto')} - R$ {prod_data.get('preco')}"

                if img_url and instance and remote_jid:
                    success = await evolution_service.send_image_message(instance, remote_jid, img_url, cap)
                    if success:
                        return json.dumps({"sucesso": True, "mensagem": f"Foto do produto ID {produto_id} enviada no WhatsApp"}, ensure_ascii=False)
                    else:
                        return json.dumps({"sucesso": False, "erro": "Falha no envio da imagem pela Evolution API"}, ensure_ascii=False)

                return json.dumps({"sucesso": False, "erro": f"Produto ID {produto_id} nao encontrado ou sem imagem"}, ensure_ascii=False)

            if name == "buscar_produtos":
                res = await supabase_service.buscar_produtos(**args)
            elif name == "listar_categorias":
                res = await supabase_service.listar_categorias(**args)
            elif name == "info_empresa":
                res = await supabase_service.info_empresa(**args)
            elif name == "buscar_enderecos_cliente":
                res = await supabase_service.buscar_enderecos_cliente(**args)
            elif name == "calcular_entrega_completa":
                raw_addr = str(args.get("p_endereco", "") or "").strip()
                if settings.ENABLE_JEV_GATEWAY and settings.ENABLE_JEV_ADDRESS_GATE:
                    val = await jev_service.validate_address_text(raw_addr, cidade_loja=cidade_loja)
                    if not val.get("is_valid", True):
                        logger.warning(f"[JEV GATEWAY] Endereço bloqueado: {val.get('reason')}")
                        return json.dumps({
                            "sucesso": False,
                            "erro": "endereco_incompleto",
                            "motivo": val.get("reason"),
                            "orientacao_para_o_agente": (
                                "NÃO invente taxa de entrega e NÃO conclua o frete ainda! "
                                "O endereço fornecido está incompleto (faltou nome da Rua, Número predial ou Bairro, ou foi apenas referência/localização). "
                                "Peça gentilmente para o cliente digitar o endereço completo por escrito com o nome da Rua, "
                                "o Número da casa (ou quadra/lote) e o Bairro para calcularmos a taxa exata."
                            )
                        }, ensure_ascii=False)
                    if val.get("clean_address"):
                        args["p_endereco"] = val["clean_address"]

                # Verificar cache de sessão para evitar chamadas repetidas ao Google Maps
                cached_calc = self._get_cached_delivery(session_id, args.get("p_endereco", ""))
                if cached_calc and isinstance(cached_calc.get("result"), dict):
                    logger.info(f"[CACHE FRETE] Reutilizando cálculo de entrega da sessão {session_id}")
                    res = cached_calc["result"]
                else:
                    res = await supabase_service.calcular_entrega_completa(**args)
                    if isinstance(res, dict) and res.get("sucesso"):
                        self._set_cached_delivery(session_id, raw_addr, res)
            elif name == "buscar_adicionais_produto":
                res = await supabase_service.buscar_adicionais_produto(**args)
            elif name == "criar_pedido_completo":
                end_ent = str(args.get("p_endereco_entrega", "") or "").strip()
                tipo_ent = "retirada" if (not end_ent or "retirada" in end_ent.lower()) else "entrega"

                # 1. Proteção de Novo Pedido (#ID Novo): se não há pedido ativo hoje ou se o pedido anterior já saiu (status >= 3), forçar novo ID
                if not pedido_ativo_hoje or int(pedido_ativo_hoje.get("status", 1)) >= 3:
                    args["p_forcar_novo"] = True

                # 2. Blindagem de Endereço e Frete Obrigatório para Entregas (Zero taxa R$ 0,00 ou endereço sem rua/número)
                if tipo_ent == "entrega":
                    if settings.ENABLE_JEV_GATEWAY and settings.ENABLE_JEV_ADDRESS_GATE:
                        val_addr = await jev_service.validate_address_text(end_ent, cidade_loja=cidade_loja)
                        if not val_addr.get("is_valid", True):
                            logger.warning(f"[JEV GATEWAY] Bloqueio de criar_pedido_completo por endereço inválido: {val_addr.get('reason')}")
                            return json.dumps({
                                "sucesso": False,
                                "erro": "endereco_invalido_para_entrega",
                                "motivo": val_addr.get("reason"),
                                "orientacao_para_o_agente": (
                                    "PARE! O endereço de entrega informado está incompleto (sem nome de Rua, Número ou Bairro). "
                                    "Solicite ao cliente o endereço completo por escrito (Rua, Número e Bairro) e execute "
                                    "`calcular_entrega_completa` antes de finalizar o pedido!"
                                )
                            }, ensure_ascii=False)

                    # Preencher automaticamente taxa e coordenadas a partir do cache da sessão ou calcular agora
                    cached_deliv = self._get_cached_delivery(session_id)
                    if cached_deliv:
                        if float(args.get("p_taxa_entrega") or 0) <= 0:
                            args["p_taxa_entrega"] = cached_deliv["taxa_entrega"]
                        if float(args.get("p_latitude_entrega") or 0) == 0:
                            args["p_latitude_entrega"] = cached_deliv["latitude"]
                        if float(args.get("p_longitude_entrega") or 0) == 0:
                            args["p_longitude_entrega"] = cached_deliv["longitude"]
                        if float(args.get("p_distancia_km") or 0) == 0:
                            args["p_distancia_km"] = cached_deliv["distancia_km"]
                    elif float(args.get("p_taxa_entrega") or 0) <= 0 or float(args.get("p_latitude_entrega") or 0) == 0:
                        calc_auto = await supabase_service.calcular_entrega_completa(
                            p_empresa_id=args.get("p_empresa_id", default_user_id),
                            p_endereco=end_ent
                        )
                        if not isinstance(calc_auto, dict) or not calc_auto.get("sucesso"):
                            motivo_calc = calc_auto.get("mensagem") if isinstance(calc_auto, dict) else "Não foi possível localizar o endereço no mapa."
                            return json.dumps({
                                "sucesso": False,
                                "erro": "frete_nao_calculado",
                                "motivo": motivo_calc,
                                "orientacao_para_o_agente": (
                                    f"PARE! Não foi possível validar a rota/taxa para '{end_ent}': {motivo_calc}. "
                                    "Confirme com o cliente o nome correto da Rua, Número e Bairro antes de criar o pedido!"
                                )
                            }, ensure_ascii=False)
                        self._set_cached_delivery(session_id, end_ent, calc_auto)
                        args["p_taxa_entrega"] = float(calc_auto.get("taxa_entrega") or 0)
                        args["p_latitude_entrega"] = float(calc_auto.get("latitude") or 0)
                        args["p_longitude_entrega"] = float(calc_auto.get("longitude") or 0)
                        args["p_distancia_km"] = float(calc_auto.get("distancia_km") or 0)

                # 3. Trava JEV de Confirmação Final / Impressão na Cozinha
                if settings.ENABLE_JEV_GATEWAY and settings.ENABLE_JEV_PRINT_GATE:
                    ultimo_resumo = history[-1].get("content", "") if history else ""
                    is_ready, reason = await jev_service.verify_order_readiness(
                        carrinho=args,
                        ultimo_resumo_ia=ultimo_resumo,
                        mensagem_cliente=user_message,
                        tipo_entrega=tipo_ent
                    )
                    if not is_ready:
                        logger.warning(f"[JEV GATEWAY] Bloqueio físico de impressão acionado: {reason}")
                        return json.dumps({
                            "sucesso": False,
                            "bloqueio_impressao": True,
                            "motivo": reason,
                            "orientacao_para_o_agente": (
                                "ATENÇÃO: A IMPRESSORA DA COZINHA NÃO FOI ACIONADA E O PEDIDO NÃO FOI FECHADO! "
                                "O cliente ainda não confirmou expressamente o resumo final do pedido com 'Sim' ou 'Confirmado'. "
                                "NÃO diga que o pedido foi finalizado! "
                                "Apresente o resumo completo com itens, adicionais, endereço/horário e valor total, "
                                "e pergunte: 'Podemos confirmar o seu pedido?'"
                            )
                        }, ensure_ascii=False)
                res = await supabase_service.criar_pedido_completo(args)
            elif name == "consultar_pedido":
                res = await supabase_service.consultar_pedido(**args)
            elif name == "atualizar_pedido_completo":
                p_pedido_id = int(args.get("p_pedido_id") or 0)
                pedido_alvo = None
                if pedido_ativo_hoje and int(pedido_ativo_hoje.get("id") or 0) == p_pedido_id:
                    pedido_alvo = pedido_ativo_hoje
                elif p_pedido_id > 0:
                    pedido_alvo = await supabase_service.get_pedido_by_id(args.get("p_empresa_id", 43), p_pedido_id)

                # 🛑 TRAVA FÍSICA 1: Pedido inexistente ou de outro dia
                if not pedido_alvo:
                    return json.dumps({
                        "sucesso": False,
                        "erro": "PEDIDO_ANTIGO_OU_INEXISTENTE",
                        "orientacao_para_o_agente": (
                            f"PARE! O Pedido #{p_pedido_id} não é um pedido ativo de hoje. "
                            "JAMAIS tente atualizar pedidos de dias anteriores! "
                            "Chame `criar_pedido_completo` com `p_forcar_novo: true` para criar um novo pedido de hoje."
                        )
                    }, ensure_ascii=False)

                # 🛑 TRAVA FÍSICA 2: Pedido já saiu para entrega (Status >= 3)
                status_alvo = int(pedido_alvo.get("status", 1))
                if status_alvo >= 3:
                    logger.warning(f"[JEV GATEWAY] Bloqueio de atualização: Pedido #{p_pedido_id} já saiu (status={status_alvo})")
                    return json.dumps({
                        "sucesso": False,
                        "bloqueio_status_entrega": True,
                        "erro": "PEDIDO_JA_SAIU_PARA_ENTREGA",
                        "status_atual": status_alvo,
                        "orientacao_para_o_agente": (
                            f"PARE! O Pedido #{p_pedido_id} JÁ SAIU PARA ENTREGA com o motoboy (Status {status_alvo}) "
                            "e NÃO pode mais receber novos itens na mesma viagem! "
                            f"Informe ao cliente que o Pedido #{p_pedido_id} já saiu para entrega e pergunte se ele deseja "
                            "abrir um NOVO PEDIDO separado (avisando que haverá uma nova taxa de entrega). "
                            "Se o cliente confirmar, chame `criar_pedido_completo` com `p_forcar_novo: true`."
                        )
                    }, ensure_ascii=False)

                # 🛡️ PROTEÇÃO DE ITENS ANTERIORES: Se a mensagem do cliente é pura adição e a LLM omitiu itens que já estavam no pedido, preservá-los!
                itens_novos = args.get("p_itens")
                itens_antigos = pedido_alvo.get("itens_atuais") or []
                msg_low = (user_message or "").lower()
                eh_remocao_ou_troca = bool(re.search(r'\b(troca|trocar|tira|tirar|remove|remover|cancela|cancelar|sem o|ao inv[eé]s)\b', msg_low))
                if isinstance(itens_novos, list) and len(itens_novos) > 0 and len(itens_antigos) > 0 and not eh_remocao_ou_troca:
                    ids_novos = {int(i.get("produto_id") or 0) for i in itens_novos if isinstance(i, dict)}
                    itens_preservados = []
                    for ant in itens_antigos:
                        pid_ant = int(ant.get("produto_id") or 0)
                        if pid_ant > 0 and pid_ant not in ids_novos:
                            adicionais_raw = ant.get("itens_pedido_adicionais") or []
                            adicionais_ids = [
                                int(a.get("opcao_adicional_id"))
                                for a in adicionais_raw
                                if isinstance(a, dict) and a.get("opcao_adicional_id") is not None
                            ]
                            item_obj: Dict[str, Any] = {
                                "produto_id": pid_ant,
                                "quantidade": int(ant.get("quantidade") or 1)
                            }
                            if adicionais_ids:
                                item_obj["adicionais"] = adicionais_ids
                            itens_preservados.append(item_obj)
                    if itens_preservados:
                        logger.info(f"[AUTO-MERGE ITENS] Preservando {len(itens_preservados)} item(ns) anterior(es) no Pedido #{p_pedido_id}")
                        args["p_itens"] = itens_preservados + itens_novos

                res = await supabase_service.atualizar_pedido_completo(args)
            elif name == "escalar_atendimento_humano":
                if settings.ENABLE_JEV_GATEWAY:
                    needs_human = await jev_service.verify_human_escalation(
                        mensagem_cliente=user_message,
                        historico_recente=history or []
                    )
                    if not needs_human:
                        logger.warning("[JEV GATEWAY] Bloqueio de falso transbordo humano ativado pelo Circuit Breaker.")
                        return json.dumps({
                            "sucesso": False,
                            "bloqueio_transbordo": True,
                            "motivo": "Circuit Breaker: A solicitação do cliente não requer atendimento humano no momento.",
                            "orientacao_para_o_agente": (
                                "NÃO encerre o atendimento nem diga que passou para a equipe humana. "
                                "Continue o atendimento prestativo com o cliente, tirando a dúvida dele com gentileza."
                            )
                        }, ensure_ascii=False)
                res = await supabase_service.registrar_transbordo(**args)
            else:
                res = {"erro": f"Ferramenta desconhecida: {name}"}
            return json.dumps(res, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Erro ao executar tool {name}: {e}")
            return json.dumps({"erro": str(e), "sucesso": False}, ensure_ascii=False)

    async def run_agent(
        self,
        empresa_data: Dict[str, Any],
        empresa_rows: Dict[str, Any],
        contact_name: str,
        remote_jid: str,
        user_message: str,
        instance: str,
        model_override: Optional[str] = None,
        save_user_message: bool = True
    ) -> str:
        phone_number = remote_jid.split('@')[0] if remote_jid else ""
        user_id_empresa = empresa_data.get("user_id") or "72055e41-9f72-4dac-97c2-7b5109890b50"
        id_numerico_empresa = empresa_data.get("id", 43)
        session_id = f"{id_numerico_empresa}_{phone_number}"
        
        slug_empresa = empresa_data.get("slug") or "cantinho-do-frango-assado"
        cardapio_digital_url = f"https://app.proia.com.br/loja/{slug_empresa}"
        endereco_loja_oficial = empresa_rows.get("endereco", "R. Sao Francisco, 2249 - Lot. Mimoso Doeste I, Luis Eduardo Magalhaes - BA")

        chosen_profile_name = settings.ACTIVE_AGENT_PROFILE
        active_profile = get_active_profile(chosen_profile_name)

        chosen_model = model_override or active_profile.model_name
        client, model_name = self.get_client_for_model(chosen_model)

        # Calcular fuso horario local exato da loja no Brasil ANTES de consultar pedidos e histórico do dia
        fuso_loja = empresa_data.get("fuso_horario") or "America/Sao_Paulo"
        try:
            agora_local = datetime.now(ZoneInfo(fuso_loja))
        except Exception:
            agora_local = datetime.now(ZoneInfo("America/Sao_Paulo"))

        dias_semana = {
            "Monday": "Segunda-feira", "Tuesday": "Terça-feira", "Wednesday": "Quarta-feira",
            "Thursday": "Quinta-feira", "Friday": "Sexta-feira", "Saturday": "Sábado", "Sunday": "Domingo"
        }
        dia_nome = dias_semana.get(agora_local.strftime("%A"), agora_local.strftime("%A"))
        data_hoje_iso = agora_local.strftime('%Y-%m-%d')
        hora_local_str = f"{dia_nome}, {agora_local.strftime('%d/%m/%Y às %H:%M:%S')} (Fuso: {fuso_loja})"

        # Consultar em paralelo: 1) Histórico da sessão ativa (últimas 6h), 2) Pedido ativo de hoje (últimas 3h), 3) Endereços salvos do cliente
        history, pedido_ativo_hoje, enderecos_res = await asyncio.gather(
            self.get_chat_history(session_id, limit=12, max_hours=6),
            supabase_service.get_pedido_ativo_hoje(id_numerico_empresa, phone_number, data_hoje_iso=data_hoje_iso, max_hours=3),
            supabase_service.buscar_enderecos_cliente(phone_number),
            return_exceptions=True
        )
        if isinstance(history, Exception) or not isinstance(history, list):
            history = []
        if isinstance(pedido_ativo_hoje, Exception) or not isinstance(pedido_ativo_hoje, dict):
            pedido_ativo_hoje = None

        enderecos_salvos_str = "Nenhum endereço salvo anteriormente."
        if isinstance(enderecos_res, dict) and enderecos_res.get("sucesso") and isinstance(enderecos_res.get("enderecos"), list):
            end_list = enderecos_res["enderecos"]
            if len(end_list) > 0:
                end_principal = end_list[0]
                end_fmt = end_principal.get("endereco_formatado") or f"{end_principal.get('rua', '')}, {end_principal.get('numero', '')} - {end_principal.get('bairro', '')}"
                enderecos_salvos_str = f"Endereço salvo do cliente: {end_fmt}"

        eh_cliente_novo = (len(history) == 0 and pedido_ativo_hoje is None)
        prompt_customizado_loja = empresa_data.get("prompt_customizado") or empresa_data.get("regras_adicionais") or ""

        logger.info(
            f"Agente: {contact_name} | Perfil: {active_profile.name} | Modelo: {model_name} | "
            f"Loja ID: {id_numerico_empresa} ({slug_empresa}) | Session: {session_id} | "
            f"PedidoAtivoHoje: #{pedido_ativo_hoje.get('id') if pedido_ativo_hoje else 'Nenhum'}"
        )

        # Build system prompt header
        header = (
            f"CONTEXTO DA SESSAO DA LOJA:\n"
            f"Data/Hora Atual Local da Loja: {hora_local_str}\n"
            f"Data Oficial Hoje: {data_hoje_iso}\n"
            f"Dia da Semana: {dia_nome}\n"
            f"Fuso Horario Local da Loja: {fuso_loja}\n"
            f"Empresa ID: {id_numerico_empresa}\n"
            f"Empresa UUID: {user_id_empresa}\n"
            f"Loja: {empresa_data.get('categoria', '')} {empresa_data.get('nome_empresa', '')}\n"
            f"Slug: {slug_empresa}\n"
            f"Cardapio Digital: {cardapio_digital_url}\n"
            f"Endereco Loja: {endereco_loja_oficial}\n"
            f"Cliente: {contact_name}\n"
            f"Telefone: {phone_number}\n"
            f"Enderecos Salvos do Cliente: {enderecos_salvos_str}\n"
            f"REGRAS ESPECIFICAS DA LOJA (PROMPT CUSTOMIZADO):\n"
            f"{prompt_customizado_loja if prompt_customizado_loja else 'Nenhuma regra customizada extra.'}\n\n"
            f"Horario e Regras da Loja: {empresa_data.get('regras_adicionais', 'Terça a Domingo das 09:00 às 15:00')}\n"
            f"Valor/km: {empresa_data.get('valor_por_km', 0)}\n"
            f"Frete minimo: {empresa_data.get('valor_minimo_entrega', 0)}\n"
            f"Dist. maxima: {empresa_data.get('distancia_maxima_km', 0)}\n"
            f"Chave PIX (NUNCA ENVIAR A MENOS QUE SOLICITADO EXPLICITAMENTE): {empresa_rows.get('chave_pix', '')}\n\n"
        )

        # Construir o corpo do prompt usando o perfil ativo
        empresa_full_nome = f"{empresa_data.get('categoria', '')} {empresa_data.get('nome_empresa', '')}".strip()
        body = active_profile.build_system_prompt(
            empresa_nome=empresa_full_nome,
            fuso_horario=fuso_loja,
            data_hora_local=hora_local_str,
            regras_adicionais=prompt_customizado_loja
        )

        # 🧠 INTELIGÊNCIA JEV SYSTEM ONE: Estado de Pedido Ativo de Hoje vs. Sem Pedido Hoje
        contexto_adicao_extra = ""
        if not pedido_ativo_hoje:
            # 🚫 Diretiva Determinística Negativa: ZERO chamadas ao JEV e ZERO chance de perguntar sobre pedidos antigos (#1140)
            contexto_adicao_extra = (
                f"\n🚫 ESTADO ATUAL DE PEDIDOS DO CLIENTE HOJE ({data_hoje_iso}):\n"
                f"Este cliente NÃO possui nenhum pedido ativo ou aberto hoje.\n"
                f"REGRA ABSOLUTA: JAMAIS pergunte se ele deseja adicionar itens a um pedido anterior, "
                f"JAMAIS mencione números de pedidos antigos (#ID) e trate qualquer compra como um NOVO PEDIDO independente!\n\n"
            )
        else:
            try:
                ped_id = pedido_ativo_hoje.get("id")
                status_id = int(pedido_ativo_hoje.get("status", 1))
                mins_criacao = float(pedido_ativo_hoje.get("minutos_desde_criacao", 0) or 0)
                end_atual = str(pedido_ativo_hoje.get("endereco_entrega", "") or "")
                modalidade_atual = "Retirada na loja" if "retirada" in end_atual.lower() else f"Entrega ({end_atual})"
                valor_atual = pedido_ativo_hoje.get("valor_total", "0.00")
                pgto_atual = pedido_ativo_hoje.get("forma_pagamento", "")
                itens_atuais_raw = pedido_ativo_hoje.get("itens_atuais") or []
                itens_resumo_list = []
                for it in itens_atuais_raw:
                    if isinstance(it, dict):
                        itens_resumo_list.append(
                            f"{it.get('quantidade', 1)}x {it.get('produto_nome', 'Item')} (produto_id={it.get('produto_id')})"
                        )
                itens_resumo_str = ", ".join(itens_resumo_list) if itens_resumo_list else "Itens registrados no banco"

                if settings.ENABLE_JEV_GATEWAY and settings.ENABLE_JEV_ORDER_ADDITIONS:
                    adicao_check = await jev_service.evaluate_order_addition(user_message, pedido_ativo_hoje)
                else:
                    adicao_check = {
                        "is_addition": False,
                        "is_status_query": False,
                        "ja_saiu_para_entrega": status_id >= 3
                    }

                if status_id >= 3:
                    contexto_adicao_extra = (
                        f"\n⚠️ ATENÇÃO OPERACIONAL CRÍTICA (PEDIDO #{ped_id} JÁ SAIU PARA ENTREGA - STATUS {status_id}):\n"
                        f"O Pedido #{ped_id} ({modalidade_atual}, R$ {valor_atual}) JÁ SAIU COM O MOTOBOY ou já foi concluído!\n"
                        f"- Se o cliente pedir para adicionar qualquer item, INFORME IMEDIATAMENTE que o Pedido #{ped_id} já saiu para entrega e não é possível incluir itens na mesma viagem.\n"
                        f"- Pergunte se ele deseja abrir um NOVO PEDIDO separado (avisando que haverá nova taxa de entrega se for delivery).\n"
                        f"- JAMAIS chame `atualizar_pedido_completo` para o Pedido #{ped_id}! Se ele confirmar um novo pedido, use `criar_pedido_completo` com `p_forcar_novo: true`.\n\n"
                    )
                elif adicao_check.get("is_addition"):
                    contexto_adicao_extra = (
                        f"\n✅ ATENÇÃO OPERACIONAL DE ADIÇÃO DE ITENS (PEDIDO #{ped_id} AINDA NA LOJA - STATUS {status_id}):\n"
                        f"O cliente deseja adicionar/alterar itens no Pedido #{ped_id} feito hoje há {mins_criacao:.0f} min ({modalidade_atual}, Total atual: R$ {valor_atual}, Pagamento: {pgto_atual}).\n"
                        f"Itens que JÁ estão no Pedido #{ped_id}: {itens_resumo_str}.\n"
                        f"- O pedido AINDA ESTÁ NA COZINHA (o motoboy NÃO saiu): NÃO COBRE NOVA TAXA DE ENTREGA!\n"
                        f"- Chame `atualizar_pedido_completo` para o Pedido #{ped_id} enviando em `p_itens` os itens anteriores + os novos itens.\n"
                        f"- Se o pedido anterior já foi pago no PIX, cobre APENAS A DIFERENÇA dos novos itens!\n\n"
                    )
                elif adicao_check.get("is_status_query"):
                    status_nome = "Em Preparo na cozinha" if status_id == 2 else "Recebido e em fila de preparo"
                    contexto_adicao_extra = (
                        f"\nℹ️ CONSULTA DE ANDAMENTO DO PEDIDO #{ped_id} (HOJE):\n"
                        f"O Pedido #{ped_id} foi feito há {mins_criacao:.0f} min ({modalidade_atual}, R$ {valor_atual}) e está com status: {status_nome} (ainda na loja).\n"
                        f"Responda de forma tranquila e prestativa informando o andamento atual.\n\n"
                    )
                else:
                    contexto_adicao_extra = (
                        f"\nℹ️ PEDIDO RECENTE DO CLIENTE HOJE (#{ped_id} - há {mins_criacao:.0f} min):\n"
                        f"Itens: {itens_resumo_str} | Modalidade: {modalidade_atual} | Total: R$ {valor_atual} | Status: {status_id} (Ainda na loja).\n"
                        f"- Se o cliente estiver apenas tirando uma dúvida, responda normalmente sem insistir.\n"
                        f"- Se ele quiser acrescentar item a este Pedido #{ped_id} na mesma modalidade, use `atualizar_pedido_completo`.\n"
                        f"- Se for um pedido separado ou de modalidade diferente (ex: mudar de retirada para entrega), use `criar_pedido_completo` com `p_forcar_novo: true`.\n\n"
                    )
            except Exception as e_add:
                logger.warning(f"Erro ao avaliar adicao de pedido Jev: {e_add}")

        system_prompt = header + contexto_adicao_extra + "\n" + body

        # Mensagem do usuario (salvar apenas na primeira tentativa para nao duplicar em caso de fallback)
        if save_user_message:
            await self.save_message_to_history(session_id, "user", user_message)
        
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        for iteration in range(8):
            response = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                tools=TOOLS_SCHEMA,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=2048
            )

            response_msg = response.choices[0].message
            tool_calls = response_msg.tool_calls

            if not tool_calls:
                raw_text = response_msg.content or ""
                # Limpar markdown de imagem que pode vazar
                clean_text = re.sub(r'!\[.*?\]\([^\)]+\)', '', raw_text)
                clean_text = re.sub(r'https?://\S+\.(?:jpg|jpeg|png|webp)', '', clean_text)
                clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()
                
                await self.save_message_to_history(session_id, "assistant", clean_text)
                return clean_text

            messages.append(response_msg)

            for tool_call in tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except Exception:
                    fn_args = {}
                
                logger.info(f"Tool call: {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:200]})")
                tool_result_str = await self.execute_tool(
                    fn_name,
                    fn_args,
                    default_user_id=user_id_empresa,
                    instance=instance,
                    remote_jid=remote_jid,
                    user_message=user_message,
                    history=history,
                    cidade_loja=empresa_data.get("cidade") or "Luís Eduardo Magalhães - BA",
                    session_id=session_id,
                    pedido_ativo_hoje=pedido_ativo_hoje
                )
                await supabase_service.registrar_log("INFO", f"Tool {fn_name} executada", {"args": fn_args, "res_len": len(tool_result_str), "snippet": tool_result_str[:250]})
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": fn_name,
                    "content": tool_result_str
                })

        # Se esgotar as iterações de ferramentas, força o LLM a sintetizar uma resposta com o contexto acumulado
        try:
            final_res = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                tool_choice="none",
                temperature=0.3,
                max_tokens=512
            )
            final_content = final_res.choices[0].message.content or ""
            clean_final = re.sub(r'!\[.*?\]\([^\)]+\)', '', final_content)
            clean_final = re.sub(r'https?://\S+\.(?:jpg|jpeg|png|webp)', '', clean_final)
            clean_final = re.sub(r'\n{3,}', '\n\n', clean_final).strip()
            if clean_final:
                await self.save_message_to_history(session_id, "assistant", clean_final)
                return clean_final
        except Exception as e:
            logger.error(f"Erro ao sintetizar resposta final após iterações: {e}")

        # Se ainda assim falhar, fornecer mensagem contextual sem resetar o pedido
        fallback_text = "Recebi sua mensagem! Estou confirmando os detalhes do seu pedido. Poderia me confirmar um ponto de referência próximo para a entrega?"
        await self.save_message_to_history(session_id, "assistant", fallback_text)
        return fallback_text

agent_service = AgentService()
