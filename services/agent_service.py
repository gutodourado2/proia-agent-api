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
            "description": "Busca produtos no cardapio da loja por nome ou categoria. SEMPRE chame esta ferramenta primeiro quando precisar de informacoes sobre qualquer produto (preco, disponibilidade, ID para foto, etc).",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_empresa_id": {"type": "string", "description": "UUID da empresa"},
                    "p_busca": {"type": "string", "description": "Termo de busca (ex: pepsi, frango, costela)"},
                    "p_categoria": {"type": "string", "description": "Categoria (ex: Bebidas, Pratos)"},
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
            "description": "Lista todas as categorias de produtos disponiveis na loja.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_empresa_id": {"type": "string", "description": "UUID da empresa"}
                },
                "required": ["p_empresa_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "enviar_foto_produto",
            "description": "Envia a foto REAL do produto direto no WhatsApp do cliente. IMPORTANTE: Voce DEVE primeiro chamar buscar_produtos para obter o produto_id CORRETO antes de chamar esta ferramenta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "produto_id": {"type": "integer", "description": "ID numerico EXATO do produto retornado por buscar_produtos"},
                    "image_url": {"type": "string", "description": "URL da imagem (opcional, sera buscada automaticamente pelo produto_id)"},
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
                    "p_empresa_id": {"type": "string", "description": "ID numerico da empresa"}
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
                    "p_empresa_id": {"type": "string", "description": "UUID da empresa"},
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
            "description": "Busca acompanhamentos e cortesias disponiveis para um produto (ex: arroz, feijao tropeiro como cortesia do frango).",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_produto_id": {"type": "integer", "description": "ID do produto"}
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
                    "p_observacoes": {"type": "string", "description": "Horario de entrega/retirada + status pagamento PIX se aplicavel"}
                },
                "required": ["p_empresa_id", "p_itens", "p_endereco_entrega", "p_forma_pagamento", "p_taxa_entrega", "p_telefone_cliente"]
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
            "name": "escalar_atendimento_humano",
            "description": "Encaminha para atendente humano quando o cliente pedir ou em caso de frustracao.",
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
Voce e a atendente virtual do __EMPRESA_NOME__, especialista em delivery via WhatsApp.
Seu tom e caloroso, profissional e direto. Responda em portugues brasileiro natural.
Use emojis com moderacao. Seja breve e objetiva.

REGRAS ABSOLUTAS (siga TODAS sem excecao):

1. NUNCA INVENTE IDs DE PRODUTOS.
   - Para QUALQUER operacao que precise de um produto_id (foto, adicionais, pedido), voce DEVE PRIMEIRO chamar buscar_produtos para encontrar o produto e obter o ID CORRETO retornado pelo banco de dados.
   - NUNCA use um ID de memoria ou suposicao. Sempre busque no banco.

2. FOTOS DE PRODUTOS:
   - Quando o cliente pedir foto de um produto, siga este fluxo OBRIGATORIO:
     a) Chame buscar_produtos com o nome do produto para obter o ID correto.
     b) Com o ID retornado, chame enviar_foto_produto.
   - NUNCA gere markdown de imagem (![...](...)) no texto.

3. ADICIONAIS E CORTESIAS:
   - Frango Inteiro: 1 cortesia gratis. Meio Frango: 2 cortesias gratis.
   - ANTES de fechar o pedido, chame buscar_adicionais_produto para obter os opcao_adicional_id.
   - Ao chamar criar_pedido_completo, INCLUA o array adicionais com os IDs numericos corretos.

4. ENTREGA vs RETIRADA:
   - Pergunte: "Sera para entrega ou retirada na loja?"
   - Se RETIRADA: pergunte horario. Endereco da loja: __ENDERECO_LOJA__
   - Se ENTREGA:
     a) Chame buscar_enderecos_cliente (telefone: __TELEFONE_CLIENTE__).
     b) Se tiver endereco salvo, confirme. Senao, peca Rua, Numero e Bairro.
     c) Calcule frete com calcular_entrega_completa.
     d) Pergunte horario desejado.
   - Grave horario no campo p_observacoes.

5. PAGAMENTO PIX:
   - Se o cliente enviar comprovante: valide se o valor >= total (produtos + frete).
   - Se valido: grave "PEDIDO PAGO VIA PIX (Comprovante Validado)" em p_observacoes.
   - Se valor menor: avise educadamente.

6. FINALIZACAO DO PEDIDO:
   - Apos criar_pedido_completo, SEMPRE exiba o numero do pedido: "Seu Pedido #ID foi confirmado!"

7. CARDAPIO DIGITAL:
   - Link do cardapio: __CARDAPIO_URL__
"""

class AgentService:
    def get_client_for_model(self, target_model: Optional[str] = None):
        model_name = target_model or settings.MODEL_NAME
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
        session_id = remote_jid.split('@')[0]
        
        user_id_empresa = empresa_data.get("user_id") or "72055e41-9f72-4dac-97c2-7b5109890b50"
        id_numerico_empresa = empresa_data.get("id", 43)
        slug_empresa = empresa_data.get("slug") or "cantinho-do-frango-assado"
        cardapio_digital_url = f"https://app.proia.com.br/loja/{slug_empresa}"
        endereco_loja_oficial = empresa_rows.get("endereco", "R. Sao Francisco, 2249 - Lot. Mimoso Doeste I, Luis Eduardo Magalhaes - BA")

        chosen_model = model_override or settings.MODEL_NAME
        client, model_name = self.get_client_for_model(chosen_model)

        logger.info(f"Agente: {contact_name} | Modelo: {model_name} | Loja: {slug_empresa}")

        # Build system prompt header (safe f-strings, only simple variables)
        header = (
            f"CONTEXTO DA SESSAO:\n"
            f"Data/Hora: {datetime.now().isoformat()}\n"
            f"Empresa ID: {id_numerico_empresa}\n"
            f"Empresa UUID: {user_id_empresa}\n"
            f"Loja: {empresa_data.get('categoria', '')} {empresa_data.get('nome_empresa', '')}\n"
            f"Slug: {slug_empresa}\n"
            f"Endereco Loja: {endereco_loja_oficial}\n"
            f"Cliente: {contact_name}\n"
            f"Telefone: {session_id}\n"
            f"Regras da Loja: {empresa_data.get('regras_adicionais', '')}\n"
            f"Valor/km: {empresa_data.get('valor_por_km', 0)}\n"
            f"Frete minimo: {empresa_data.get('valor_minimo_entrega', 0)}\n"
            f"Dist. maxima: {empresa_data.get('distancia_maxima_km', 0)}\n"
            f"Loja fechada: {empresa_rows.get('loja_fechada_manual', False)}\n"
            f"Chave PIX: {empresa_rows.get('chave_pix', '')}\n"
            f"Msg PIX: {empresa_rows.get('mensagem_pix', '')}\n\n"
        )

        # Build prompt body using safe replace
        body = SYSTEM_PROMPT_BODY
        body = body.replace("__EMPRESA_NOME__", f"{empresa_data.get('categoria', '')} {empresa_data.get('nome_empresa', '')}")
        body = body.replace("__ENDERECO_LOJA__", endereco_loja_oficial)
        body = body.replace("__TELEFONE_CLIENTE__", session_id)
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
                temperature=0.3
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
