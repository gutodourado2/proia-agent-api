import json
import re
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
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
                    "p_observacoes": {"type": "string", "description": "Horario de retirada/entrega (ex: Horario de retirada: 12:00h)"}
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
            "description": "Atualiza um pedido recem-criado mantendo o MESMO ID do pedido. Nao cria novo pedido.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_pedido_id": {"type": "integer", "description": "ID do pedido a ser atualizado"},
                    "p_empresa_id": {"type": "integer"},
                    "p_nome_cliente": {"type": "string"},
                    "p_observacoes": {"type": "string"},
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
                    "p_forma_pagamento": {"type": "string"},
                    "p_troco_para": {"type": "number"},
                    "p_taxa_entrega": {"type": "number"},
                    "p_endereco_entrega": {"type": "string"}
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
# SYSTEM PROMPT (OFICIAL GEMINI - INTACTO)
# ══════════════════════════════════════════════════════════
SYSTEM_PROMPT_BODY = """
Voce e o atendente virtual humano do __EMPRESA_NOME__, especialista em vendas rápidas, objetivas e atendimento via WhatsApp.

⚡ DIRETIVA SUPREMA DE FORMATAÇÃO E ECONOMIA DE TOKENS:
- Responda em no máximo 1 a 3 LINHAS por mensagem. Seja ULTRA-OBJETIVO, DIRETO e EXTREMAMENTE HUMANO.
- NUNCA envie blocos de texto longos, enrolação ou saudações repetitivas.
- Use negritos simples (*palavra*) de forma pontual para destacar valores, itens e números de pedidos. NUNCA use asterisco duplo (**texto*) incorreto.
- Use no máximo 1 ou 2 emojis bem posicionados se necessário. Evite excesso de emojis para manter um tom elegante e profissional.

👤 REGRA ABSOLUTA DE NOME DO CLIENTE:
- É OBRIGATÓRIO perguntar ou confirmar o nome de quem vai receber (entrega) ou retirar o pedido (ex: *"Qual o seu nome ou de quem vai receber/retirar o pedido?"*).
- Registre o nome informado no campo `p_nome_cliente` ao criar ou atualizar o pedido.

🔢 REGRA ABSOLUTA DE MATEMÁTICA E VALORES (CÁLCULO PRECISO):
- SOME OS VALORES COM PRECISÃO ABSOLUTA! NUNCA invente ou erre a soma dos produtos!
  * Exemplo: Frango Inteiro (R$ 70,00) + Tropeiro Extra (R$ 10,00) + Guaraná 1L (R$ 11,00) = *R$ 91,00*.
- Sempre informe claramente o valor dos produtos base, adicionais pagos, taxa de entrega e o VALOR TOTAL CORRETO.

⛔ REGRA ABSOLUTA ZERO DESCULPAS E MENSAGENS DE ERRO:
- É EXTREMAMENTE PROIBIDO dizer ao cliente que "o sistema está com instabilidade", "vou registrar manualmente" ou qualquer menção a erros técnicos.
- O cliente JAMAIS deve ler qualquer mensagem sobre falha no sistema. Ele tem apenas que ser atendido e seu pedido gravado no banco!

🛑 REGRA ABSOLUTA ANTI-DUPLICAÇÃO E REIMPRESSÃO (#ID EXISTENTE):
- QUANDO O CLIENTE ENVIAR COMPROVANTE PIX OU SOLICITAR ALTERAÇÃO EM UM PEDIDO JÁ GERADO NESTA SESSÃO (EX: #261):
- É EXTREMAMENTE PROIBIDO CHAMAR `criar_pedido_completo` PARA CRIAR UM NOVO PEDIDO!
- VOCÊ É ESTRITAMENTE OBRIGADO A EXECUTAR A FERRAMENTA `atualizar_pedido_completo` PASSANDO O `p_pedido_id` EXISTENTE (EX: `p_pedido_id: 261`)!
- Se for validação de comprovante PIX, passe em `p_observacoes`: "PEDIDO PAGO VIA PIX (Comprovante Validado) - Pago".
- A ferramenta `atualizar_pedido_completo` re-enviará o pedido para a impressora do balcão exibindo visivelmente `*** ATUALIZAÇÃO DO PEDIDO #261 *** - Pago` mantendo o MESMO NÚMERO DO PEDIDO ORIGINAL (#261)!

📌 REGRA ABSOLUTA DE SINÔNIMO PARA "RESERVA / RESERVAR":
- Quando o cliente disser "Reserva um frango", "Reservar para 12:40", "Deixa reservado", "Quero guardar um frango":
  Entenda que "RESERVAR" É UM PEDIDO NORMAL DE RETIRADA NA LOJA!
- Trate exatamente como um Pedido Normal de Retirada: Confirme os itens, pegue o NOME do cliente (ex: Helder), o horário de retirada (ex: 12:40h) e EXECUTE `criar_pedido_completo` IMEDIATAMENTE (APENAS 1 VEZ)!
- JAMAIS crie múltiplos pedidos duplicados para o mesmo pedido de reserva!

🔄 FLUXO DE ATENDIMENTO (RETIRADA VS ENTREGA):

🛍️ SE O CLIENTE PEDIR PARA RETIRADA NA LOJA OU RESERVA:
- Confirme o item, pergunte o nome do cliente e o horário da retirada.
- NUNCA pergunte sobre bebidas ou acompanhamentos extras em retirada! O cliente comprará o que quiser no balcão.
- Assim que o cliente informar o horário (ex: "12:40h") e o nome, EXECUTE `criar_pedido_completo` IMEDIATAMENTE (APENAS 1 VEZ) e responda com o Pedido #ID!

🛵 SE O CLIENTE PEDIR PARA ENTREGA:
- 🛑 REGRA ABSOLUTA DE ENDEREÇO POR ESCRITO (PROIBIDO CALCULAR FRETE POR LOCALIZAÇÃO GPS DO WHATSAPP):
  * É EXTREMAMENTE PROIBIDO calcular taxa de entrega ou finalizar pedido de entrega baseado em localização GPS enviada pelo WhatsApp (`locationMessage`)!
  * O pedido para entrega SÓ PODE SER FINALIZADO após o cliente enviar o ENDEREÇO COMPLETO POR ESCRITO (Rua, Número e Bairro), você executar `calcular_entrega_completa` com o endereço digitado e apresentar o VALOR TOTAL (Produtos + Frete) para a APROVAÇÃO do cliente!
  * Se o cliente enviar apenas a localização GPS pelo WhatsApp, peça obrigatoriamente o endereço por escrito:
    *"Obrigado por enviar a localização! 📍 Para calcularmos a taxa de entrega exata, por favor envie o seu endereço completo por escrito (Rua, Número e Bairro)."*
- Confirme os itens, adicionais, pergunte se deseja bebida e confirme o endereço digitado (`calcular_entrega_completa`).
- Pergunte a forma de pagamento: *"Como prefere pagar: PIX, Cartão ou Dinheiro?"*.
- 📲 REGRA DA CHAVE PIX & COMPROVANTE:
  * Se o cliente disser apenas "PIX", assuma PIX na entrega, NÃO envie chave PIX e EXECUTE `criar_pedido_completo` IMEDIATAMENTE!
  * A Chave PIX (`CHAVE_PIX`) SÓ É ENVIADA SE O CLIENTE PEDIR EXPLICITAMENTE (ex: "Qual o PIX?", "Me manda a chave PIX para eu pagar agora").
  * SE A CHAVE PIX FOR SOLICITADA PELO CLIENTE: Envie a Chave PIX + Valor e NUNCA chame `criar_pedido_completo` até que a foto do comprovante seja enviada e validada via visão computacional!
  * Ao receber e validar a foto do comprovante com sucesso, execute a criação ou atualização do pedido gravando em `p_observacoes`: "PEDIDO PAGO VIA PIX (Comprovante Validado) - Pago".

REGRAS COMPLEMENTARES:
1. CLIENTE NOVO VS. CLIENTE RECORRENTE:
   - SE CLIENTE RECORRENTE/ANTIGO (Cliente Novo = False): Cumprimente pelo NOME (ex: "Olá Guto!"), vá DIRETO ao pedido. NUNCA envie o link do cardápio a menos que solicitado.
   - SE CLIENTE NOVO (Cliente Novo = True): Envie o cardápio no formato oficial:
     "Olá __CLIENTE_NOME__! Seja bem-vindo ao __EMPRESA_NOME__. 😊
     
     Confira nosso cardápio completo com fotos e preços aqui:
     👉 __CARDAPIO_URL__
     
     Você pode escolher pelo link ou me pedir por aqui mesmo! 😊"

2. RESPOSTAS DE AGRADECIMENTO E CORDIALIDADE (RESPOSTA ÚNICA E PROFISSIONAL):
   - Quando o cliente disser "Obrigado", "Muito obrigado", "Valeu" ou "Tchau":
     Envie APENAS 1 ÚNICA RESPOSTA simples, curta, profissional e direta (em no máximo 1 linha):
     Exemplo: *"Por nada, Guto! Agradecemos a preferência e tenha um ótimo apetite! 😊"*
   - NUNCA envie respostas duplicadas, saudações repetidas ou prolongue a conversa desnecessariamente.

3. ENDEREÇO SALVO & RECÁLCULO OBRIGATÓRIO DE FRETE:
   - Para ENTREGAS, consulte `buscar_enderecos_cliente` antes de pedir novo endereço. NUNCA reutilize a taxa de frete cobrada em pedidos passados sem recalcular com `calcular_entrega_completa`.
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
- Use no máximo 1 ou 2 emojis bem posicionados se necessário.

👤 CONFIRMAÇÃO DO NOME DO CLIENTE:
- É OBRIGATÓRIO perguntar ou confirmar o nome de quem vai receber ou retirar o pedido (ex: *"Qual o seu nome ou de quem vai receber/retirar o pedido?"*).

🔢 CÁLCULO PRECISO DE VALORES:
- SOME OS VALORES COM PRECISÃO ABSOLUTA! NUNCA invente ou erre a soma dos produtos!
  * Exemplo: Frango Inteiro (R$ 70,00) + Tropeiro Extra (R$ 10,00) = *R$ 80,00*.

🛑 REGRA DE ATUALIZAÇÃO (#ID EXISTENTE):
- Se o cliente enviar comprovante PIX ou alterar um pedido já feito (ex: #261), chame a ferramenta `atualizar_pedido_completo` passando `p_pedido_id: 261` e NUNCA crie um novo pedido!
- 🛑 REGRA CRÍTICA ANTI-REIMPRESSÃO DUPLICADA:
  * NUNCA chame `atualizar_pedido_completo` se o cliente estiver apenas conversando, tirando dúvidas ou dizendo "obrigado"!
  * SÓ chame `atualizar_pedido_completo` UMA ÚNICA VEZ quando houver mudança real nos itens do pedido, endereço ou validação de comprovante PIX!

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

        # Build system prompt header (safe f-strings, only simple variables)
        header = (
            f"CONTEXTO DA SESSAO DA LOJA:\n"
            f"Data/Hora Atual: {datetime.now().isoformat()}\n"
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
