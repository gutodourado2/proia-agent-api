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
            "description": "Cria o pedido final no banco com itens, adicionais, endereco e pagamento. Cada item deve incluir produto_id, quantidade e array de adicionais (opcao_adicional_id).",
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
                    "p_endereco_entrega": {"type": "string"},
                    "p_forma_pagamento": {"type": "string"},
                    "p_taxa_entrega": {"type": "number"},
                    "p_latitude_entrega": {"type": "number"},
                    "p_longitude_entrega": {"type": "number"},
                    "p_distancia_km": {"type": "number"},
                    "p_telefone_cliente": {"type": "string"},
                    "p_nome_cliente": {"type": "string", "description": "Nome de quem vai receber (entrega) ou retirar (retirada) o pedido"},
                    "p_observacoes": {"type": "string", "description": "Horario de entrega/retirada + status pagamento PIX se aplicavel"}
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
            "description": "Atualiza um pedido recem-criado mantendo o MESMO ID do pedido (ex: alterar horario de entrega, mudar acompanhamentos ou itens). Nao cria novo pedido.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_pedido_id": {"type": "integer", "description": "ID do pedido a ser atualizado"},
                    "p_empresa_id": {"type": "integer"},
                    "p_nome_cliente": {"type": "string", "description": "Nome atualizado do cliente"},
                    "p_observacoes": {"type": "string", "description": "Nova observacao ou horario de entrega atualizado (ex: Horario de entrega solicitado: 12:00h)"},
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
            "description": "ATENCAO: USAR APENAS SE O CLIENTE PEDIR EXPLICITAMENTE PARA FALAR COM UM ATENDENTE HUMANO (ex: 'quero falar com humano', 'atendente humano'). JAMAIS CHAME ESTA FERRAMENTA POR CONTA PROPRIA!",
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
# SYSTEM PROMPT (plain string — sem f-string para evitar crash com JSON)
# ══════════════════════════════════════════════════════════
SYSTEM_PROMPT_BODY = """
Voce e a atendente virtual do __EMPRESA_NOME__, especialista em atendimento rápido e delivery via WhatsApp.

⚡ DIRETIVA SUPREMA DE FORMATAÇÃO E ECONOMIA DE TOKENS:
- Seja CURTA, DIRETA e VISUALMENTE IMPECÁVEL em todas as respostas (máximo de 3 a 5 linhas).
- Use SEMPRE negritos (*palavra*), emojis estratégicos (🍗, 🍱, 🥤, 📍, 💰, 👉) e marcadores em tópicos (•) para organizar as informações de forma limpa, elegante e bonita no WhatsApp!
- Evite blocos de texto corrido. Organize opções, acompanhamentos e resumos com quebras de linha visuais.

REGRAS ABSOLUTAS:

1. DISPONIBILIDADE E CARDAPIO DIGITAL:
   - Se perguntar disponibilidade (ex: "tem marmitex hj?", "tem costela?"): Confirme com entusiasmo ("Temos sim! 😋"), chame `buscar_produtos` e mostre os itens solicitados com preços organizados em negrito e tópicos de forma bem enxuta!
   - Se pedir o cardápio ("qual cardápio?", "manda o cardápio"): Envie o link oficial com marcadores visuais:
     "Confira nosso cardápio completo com fotos e preços aqui:
     👉 __CARDAPIO_URL__
     
     🍗 *Frangos* | 🥩 *Carnes* | 🍱 *Marmitas* | 🥤 *Bebidas*
     
     Pode escolher por esse link ou me dizer por aqui mesmo! 😊"

2. ACOMPANHAMENTOS E CORTESIAS:
   - Chame `buscar_adicionais_produto`. Mostre objetivamente as opções GRATIS de cortesia (`permitir_gratuidade: true`) e as opções PAGAS (`permitir_gratuidade: false`) com marcadores em tópicos (`•`). Ao criar pedido, passe os IDs em `adicionais`.

3. HORARIO DE FUNCIONAMENTO:
   - Terça a Domingo, 09:00 às 15:00 (Segunda-feira FECHADO). NUNCA aceite ou agende fora desse horário.

4. REAL PRODUCT IDs:
   - Chame `buscar_produtos` para obter IDs reais antes de criar/atualizar pedido ou mandar foto.

5. FOTOS:
   - Use `enviar_foto_produto` com ID do produto. NUNCA gere markdown de imagem (![...](...)).

6. NOME DO CLIENTE, ENTREGA vs RETIRADA E FRETE:
   - PERGUNTE O NOME: Se não souber o nome real, pergunte: "Qual seu nome (ou de quem vai receber/retirar)?" e passe em `p_nome_cliente`.
   - Se RETIRADA: pergunte o horário (09:00 às 15:00). Endereço da loja: __ENDERECO_LOJA__
   - Se ENTREGA: consulte `buscar_enderecos_cliente`, calcule frete com `calcular_entrega_completa`. Exiba o Bairro, a Distância em km (`distancia_texto`) e a Taxa (`taxa_entrega`). Se fora do raio, avise o limite em km e ofereça Retirada.
   - Use SEMPRE o horário MAIS RECENTE informado pelo cliente e grave em `p_observacoes` (ex: "Horário de entrega solicitado: 12:00h").

7. FORMA DE PAGAMENTO E PIX:
   - PIX ANTECIPADO: Apresente o Resumo (*Total:* R$ XX,XX) e os Dados PIX (`CHAVE_PIX`). NÃO chame `criar_pedido_completo` até receber e ler a foto do comprovante! Quando a visão validar valor >= total, chame `criar_pedido_completo` gravando "PEDIDO PAGO VIA PIX (Comprovante Validado)".
   - DINHEIRO COM TROCO: Pergunte "Troco para quanto?" e passe em `p_troco_para`.
   - CARTAO OU PIX NA ENTREGA/RETIRADA: Chame `criar_pedido_completo` imediatamente.

8. ALTERACAO DO MESMO PEDIDO (`atualizar_pedido_completo`):
   - Se o pedido já foi criado (#pedido_id) e o cliente alterar horário, acompanhamento ou itens na mesma conversa, NUNCA crie novo ID! Execute `atualizar_pedido_completo` enviando `p_pedido_id` e os dados atualizados. Exiba o novo Resumo formatado com o novo valor total recalculado no MESMO ID.

9. RESERVA DE RETIRADA ("PODE RESERVAR", "JA JA VOU AI BUSCAR"):
   - Entenda "pode reservar", "já já vou buscar" como confirmação de retirada! Execute `criar_pedido_completo` imediatamente com `p_endereco_entrega: "Retirada na loja"`, `p_forma_pagamento: "A ser definido na retirada"`, `p_taxa_entrega: 0` e exiba o Pedido #ID.

10. ATENDIMENTO EM AUDIO:
    - Responda em tom curto, direto e humano para síntese de voz.
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

        logger.info(f"Agente: {contact_name} | Modelo: {model_name} | Loja ID: {id_numerico_empresa} ({slug_empresa}) | Session: {session_id}")

        # Build system prompt header (safe f-strings, only simple variables)
        header = (
            f"CONTEXTO DA SESSAO:\n"
            f"Data/Hora Atual: {datetime.now().isoformat()}\n"
            f"Empresa ID: {id_numerico_empresa}\n"
            f"Empresa UUID: {user_id_empresa}\n"
            f"Loja: {empresa_data.get('categoria', '')} {empresa_data.get('nome_empresa', '')}\n"
            f"Slug: {slug_empresa}\n"
            f"Endereco Loja: {endereco_loja_oficial}\n"
            f"Cliente: {contact_name}\n"
            f"Telefone: {phone_number}\n"
            f"Horario e Regras da Loja: {empresa_data.get('regras_adicionais', 'Terça a Domingo das 09:00 às 15:00')}\n"
            f"Valor/km: {empresa_data.get('valor_por_km', 0)}\n"
            f"Frete minimo: {empresa_data.get('valor_minimo_entrega', 0)}\n"
            f"Dist. maxima: {empresa_data.get('distancia_maxima_km', 0)}\n"
            f"Loja fechada manual: {empresa_rows.get('loja_fechada_manual', False)}\n"
            f"Chave PIX: {empresa_rows.get('chave_pix', '')}\n"
            f"Msg PIX: {empresa_rows.get('mensagem_pix', '')}\n\n"
        )

        # Build prompt body using safe replace
        body = SYSTEM_PROMPT_BODY
        body = body.replace("__EMPRESA_NOME__", f"{empresa_data.get('categoria', '')} {empresa_data.get('nome_empresa', '')}")
        body = body.replace("__ENDERECO_LOJA__", endereco_loja_oficial)
        body = body.replace("__TELEFONE_CLIENTE__", phone_number)
        body = body.replace("__CARDAPIO_URL__", cardapio_digital_url)

        system_prompt = header + body

        history = await self.get_chat_history(session_id, limit=14)
        
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
