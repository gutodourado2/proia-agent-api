import json
import re
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
from openai import AsyncOpenAI
from config import settings
from services.supabase_service import supabase_service
from services.evolution_service import evolution_service

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
- 🛑 REGRA ABSOLUTA DE ENDEREÇO POR ESCRITO (PROIBIDO CALCULAR FRETE POR LOCALIZAÇÃO GPS DO WHATSAPP):
  * O pedido para entrega SÓ PODE SER FINALIZADO após o cliente enviar o ENDEREÇO COMPLETO POR ESCRITO (Rua, Número e Bairro), você executar `calcular_entrega_completa` com o endereço digitado e apresentar o VALOR TOTAL para aprovação!
- Confirme os itens, adicionais, pergunte se deseja bebida e confirme o endereço digitado (`calcular_entrega_completa`).
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

📌 SINÔNIMO PARA "RESERVA / RESERVAR":
- "Reservar", "Guarda um frango", "Reserva pra mim" ➔ É um PEDIDO DE RETIRADA NORMAL. Pegue o nome, o horário e chame `criar_pedido_completo`.

🛵 REGRA DE ENTREGA E FRETE POR ESCRITO:
- É PROIBIDO calcular taxa de frete por localização GPS do WhatsApp (`locationMessage`).
- Para entregas, peça o ENDEREÇO COMPLETO POR ESCRITO (Rua, Número e Bairro), execute `calcular_entrega_completa` com o endereço digitado e confirme o valor total com o cliente antes de criar o pedido.

📲 REGRA DA CHAVE PIX & COMPROVANTE:
- SÓ envie a chave PIX se o cliente pedir explicitamente (ex: "Qual a chave PIX?"). Se pedir, envie a chave e SÓ gere o pedido após o recebimento e validação do comprovante.
"""

class AgentService:
    def get_client_for_model(self, target_model: Optional[str] = None):
        model_name = target_model or settings.MODEL_NAME
        # Se for um modelo nativo OpenAI para fallback (ex: gpt-4o, gpt-4o-mini)
        if model_name in ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"]:
            client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            return client, model_name

        if "/" in model_name or settings.LLM_PROVIDER.lower() == "openrouter" or (settings.OPENROUTER_API_KEY and not settings.OPENAI_API_KEY):
            api_key = settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY
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

    async def get_chat_history(self, session_id: str, limit: int = 14) -> List[Dict[str, Any]]:
        url = f"{supabase_service.base_url}/rest/v1/n8n_chat_histories?session_id=eq.{session_id}&order=id.desc&limit={limit}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(url, headers=supabase_service.headers)
                data = res.json()
                data.reverse()
                messages = []
                for row in data:
                    msg = row.get("message", {})
                    role = "user" if msg.get("type") == "human" else "ai"
                    content = msg.get("content", "")
                    if content:
                        clean_content = re.sub(r'!\[.*?\]\([^\)]+\)', '', content).strip()
                        messages.append({"role": "user" if role == "user" else "assistant", "content": clean_content})
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
                "additional_kwargs": {},
                "response_metadata": {}
            }
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                await client.post(url, headers=supabase_service.headers, json=payload)
            except Exception as e:
                logger.error(f"Erro ao salvar mensagem no historico: {e}")

    async def execute_tool(self, name: str, args: Dict[str, Any], default_user_id: str = "", instance: str = "", remote_jid: str = "") -> str:
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
                res = await supabase_service.calcular_entrega_completa(**args)
            elif name == "buscar_adicionais_produto":
                res = await supabase_service.buscar_adicionais_produto(**args)
            elif name == "criar_pedido_completo":
                res = await supabase_service.criar_pedido_completo(args)
            elif name == "consultar_pedido":
                res = await supabase_service.consultar_pedido(**args)
            elif name == "atualizar_pedido_completo":
                res = await supabase_service.atualizar_pedido_completo(args)
            elif name == "escalar_atendimento_humano":
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
        model_override: Optional[str] = None
    ) -> str:
        phone_number = remote_jid.split('@')[0] if remote_jid else ""
        user_id_empresa = empresa_data.get("user_id") or "72055e41-9f72-4dac-97c2-7b5109890b50"
        id_numerico_empresa = empresa_data.get("id", 43)
        session_id = f"{id_numerico_empresa}_{phone_number}"
        
        slug_empresa = empresa_data.get("slug") or "cantinho-do-frango-assado"
        cardapio_digital_url = f"https://app.proia.com.br/loja/{slug_empresa}"
        endereco_loja_oficial = empresa_rows.get("endereco", "R. Sao Francisco, 2249 - Lot. Mimoso Doeste I, Luis Eduardo Magalhaes - BA")

        chosen_model = model_override or settings.MODEL_NAME
        client, model_name = self.get_client_for_model(chosen_model)

        history = await self.get_chat_history(session_id, limit=14)
        eh_cliente_novo = (len(history) == 0)
        prompt_customizado_loja = empresa_data.get("prompt_customizado") or empresa_data.get("regras_adicionais") or ""

        logger.info(f"Agente: {contact_name} | Modelo: {model_name} | Loja ID: {id_numerico_empresa} ({slug_empresa}) | Session: {session_id} | Novo: {eh_cliente_novo}")

        # Calcular fuso horario local exato da loja no Brasil
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
        hora_local_str = f"{dia_nome}, {agora_local.strftime('%d/%m/%Y às %H:%M:%S')} (Fuso: {fuso_loja})"

        # Build system prompt header (safe f-strings, only simple variables)
        header = (
            f"CONTEXTO DA SESSAO DA LOJA:\n"
            f"Data/Hora Atual Local da Loja: {hora_local_str}\n"
            f"Empresa ID: {id_numerico_empresa}\n"
            f"Empresa UUID: {user_id_empresa}\n"
            f"Loja: {empresa_data.get('categoria', '')} {empresa_data.get('nome_empresa', '')}\n"
            f"Slug: {slug_empresa}\n"
            f"Endereco Loja: {endereco_loja_oficial}\n"
            f"Cliente: {contact_name}\n"
            f"Telefone: {phone_number}\n"
            f"Cliente Novo (Sem Historico): {eh_cliente_novo}\n"
            f"REGRAS ESPECIFICAS DA LOJA (PROMPT CUSTOMIZADO):\n"
            f"{prompt_customizado_loja if prompt_customizado_loja else 'Nenhuma regra customizada extra.'}\n\n"
            f"Horario e Regras da Loja: {empresa_data.get('regras_adicionais', 'Terça a Domingo das 09:00 às 15:00')}\n"
            f"Valor/km: {empresa_data.get('valor_por_km', 0)}\n"
            f"Frete minimo: {empresa_data.get('valor_minimo_entrega', 0)}\n"
            f"Dist. maxima: {empresa_data.get('distancia_maxima_km', 0)}\n"
            f"Chave PIX (NUNCA ENVIAR A MENOS QUE SOLICITADO EXPLICITAMENTE): {empresa_rows.get('chave_pix', '')}\n\n"
        )

        # Build prompt body using safe replace (Isolamento total: DeepSeek usa TESTER_SYSTEM_PROMPT_BODY, Gemini usa SYSTEM_PROMPT_BODY)
        if "deepseek" in chosen_model.lower():
            body = TESTER_SYSTEM_PROMPT_BODY
        else:
            body = SYSTEM_PROMPT_BODY

        body = body.replace("__EMPRESA_NOME__", f"{empresa_data.get('categoria', '')} {empresa_data.get('nome_empresa', '')}")
        body = body.replace("__ENDERECO_LOJA__", endereco_loja_oficial)
        body = body.replace("__TELEFONE_CLIENTE__", phone_number)
        body = body.replace("__CARDAPIO_URL__", cardapio_digital_url)

        system_prompt = header + body
        
        # Mensagem do usuario (simples e limpa para o LLM processar melhor)
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
                tool_result_str = await self.execute_tool(fn_name, fn_args, default_user_id=user_id_empresa, instance=instance, remote_jid=remote_jid)
                await supabase_service.registrar_log("INFO", f"Tool {fn_name} executada", {"args": fn_args, "res_len": len(tool_result_str), "snippet": tool_result_str[:250]})
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": fn_name,
                    "content": tool_result_str
                })

        fallback_text = "Estou aqui para ajudar! O que voce gostaria de pedir?"
        await self.save_message_to_history(session_id, "assistant", fallback_text)
        return fallback_text

agent_service = AgentService()
