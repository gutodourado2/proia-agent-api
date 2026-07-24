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

# Definicao das ferramentas (Tools) no formato OpenAI / OpenRouter Function Calling
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "buscar_produtos",
            "description": "Busca produtos no cardapio por nome, categoria ou palavra-chave (ex: frango, costela, marmita).",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_empresa_id": {"type": "string", "description": "ID/User_ID da empresa (string UUID)"},
                    "p_busca": {"type": "string", "description": "Nome do produto ou termo de busca (opcional)"},
                    "p_categoria": {"type": "string", "description": "Nome da categoria (opcional)"},
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
                    "p_empresa_id": {"type": "string", "description": "ID/User_ID da empresa (string UUID)"}
                },
                "required": ["p_empresa_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "enviar_foto_produto",
            "description": "Envia uma foto REAL e NATIVA do produto no WhatsApp como anexo. Use ESTA FERRAMENTA APENAS SE O CLIENTE SOLICITAR A FOTO EXPLICITAMENTE.",
            "parameters": {
                "type": "object",
                "properties": {
                    "produto_id": {"type": "integer", "description": "ID numérico do produto (ex: 1113 para Frango Inteiro)"},
                    "image_url": {"type": "string", "description": "URL publica da imagem do produto"},
                    "caption": {"type": "string", "description": "Legenda com nome e preco do produto"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "info_empresa",
            "description": "Retorna informacoes detalhadas da empresa (horarios, endereco, regras).",
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
            "description": "Busca os enderecos salvos do cliente pelo numero de telefone.",
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
            "description": "Calcula no Google Maps a distancia real de rota de transito em KM da loja ate o endereco do cliente, retornando a taxa oficial de entrega.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_empresa_id": {"type": "string", "description": "User_ID / ID da empresa (string UUID)"},
                    "p_endereco": {"type": "string", "description": "Endereco completo com rua, numero e bairro"}
                },
                "required": ["p_empresa_id", "p_endereco"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_adicionais_produto",
            "description": "Busca os acompanhamentos e adicionais configurados para um produto.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_produto_id": {"type": "integer", "description": "ID do produto (bigint)"}
                },
                "required": ["p_produto_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "criar_pedido_completo",
            "description": "Grava o pedido final no banco de dados com todos os itens, adicionais, endereco e forma de pagamento.",
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
                                "adicionais": {"type": "array", "items": {"type": "integer"}}
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
                    "p_observacoes": {"type": "string"}
                },
                "required": ["p_empresa_id", "p_itens", "p_endereco_entrega", "p_forma_pagamento", "p_taxa_entrega", "p_telefone_cliente"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_pedido",
            "description": "Consulta o status atual de um pedido existente no banco de dados.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_pedido_id": {"type": "integer", "description": "ID numérico do pedido"}
                },
                "required": ["p_pedido_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "escalar_atendimento_humano",
            "description": "Encaminha o atendimento para um atendente humano em caso de pedido explicito ou frustracao.",
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
                        return json.dumps({"sucesso": True, "mensagem": f"Foto oficial do produto enviada com sucesso no WhatsApp do cliente ({img_url})"}, ensure_ascii=False)
                    else:
                        return json.dumps({"sucesso": False, "erro": "Falha no envio da imagem pela Evolution API"}, ensure_ascii=False)

                return json.dumps({"sucesso": False, "erro": "Parametros de foto insuficientes"}, ensure_ascii=False)

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
        endereco_loja_oficial = empresa_rows.get("endereco", "R. São Francisco, 2249 - Lot. Mimoso Doeste I, Luís Eduardo Magalhães - BA")

        chosen_model = model_override or empresa_data.get("modelo_ia") or settings.MODEL_NAME
        client, model_name = self.get_client_for_model(chosen_model)

        logger.info(f"Executando Agente de Delivery para {contact_name} - Modelo: {model_name} - Loja: {slug_empresa}")

        system_prompt = f"""AGORA: {datetime.now().isoformat()}
EMPRESA_USER_ID: {user_id_empresa}
EMPRESA_NUMERIC_ID: {id_numerico_empresa}
EMPRESA_NOME: {empresa_data.get('categoria', '')} {empresa_data.get('nome_empresa', '')}
LOJA_SLUG: {slug_empresa}
CARDAPIO_DIGITAL_URL: {cardapio_digital_url}
LOJA_ENDEREÇO_OFICIAL: {endereco_loja_oficial}
CLIENTE_NOME: {contact_name}
CLIENTE_CONTATO: {remote_jid}
REGRAS_ESPECIFICAS_DA_LOJA: {empresa_data.get('regras_adicionais', '')}
VALOR_POR_KM: {empresa_data.get('valor_por_km', 0)}
VALOR_MINIMO_ENTREGA: {empresa_data.get('valor_minimo_entrega', 0)}
DISTANCIA_MAXIMA_KM: {empresa_data.get('distancia_maxima_km', 0)}
LOJA_FECHADA_MANUAL: {empresa_rows.get('loja_fechada_manual', False)}
CHAVE_PIX: {empresa_rows.get('chave_pix', '')}
MENSAGEM_PIX: {empresa_rows.get('mensagem_pix', '')}

Você é o atendente virtual de delivery da {empresa_data.get('categoria', '')} {empresa_data.get('nome_empresa', '')}.
Sua missão é ajudar o cliente a escolher produtos, indicar acompanhamentos e cortesias e finalizar o pedido com rapidez, clareza e simpatia.

════════════════════════════════════════════════════════════
1. REGRA ABSOLUTA: RETIRADA VS. ENTREGA E AGENDAMENTO DE HORÁRIO
════════════════════════════════════════════════════════════
- NA ETAPA DE FECHAMENTO DO PEDIDO, VOCÊ DEVE PERGUNTAR OBRIGATORIAMENTE:
  "O pedido será para entrega no seu endereço ou para retirada na loja?"
- PERGUNTA OBRIGATÓRIA DE HORÁRIO:
  - SE O CLIENTE ESCOLHER RETIRADA NA LOJA:
    - Pergunte obrigatoriamente: "Qual o horário que você gostaria de retirar o pedido na loja?"
    - Se o cliente perguntar ou não souber o endereço da loja, informe o endereço oficial da empresa: {endereco_loja_oficial}
    - Defina `p_endereco_entrega` = "Retirada no local", `p_taxa_entrega` = 0, `p_distancia_km` = 0.
  - SE O CLIENTE ESCOLHER ENTREGA:
    - Pergunte obrigatoriamente: "Qual o horário que você prefere que seja entregue? Ou prefere que seja entregue o quanto antes (entrega imediata)?"
- DOCUMENTAÇÃO OBRIGATÓRIA NAS OBSERVAÇÕES (`p_observacoes`):
  - O horário acordado com o cliente (ex: "Horário de retirada: 12:30", "Horário de entrega: às 13:00" ou "Entrega imediata") DEVE OBRIGATORIAMENTE ser gravado no campo `p_observacoes` ao chamar a ferramenta `criar_pedido_completo`.

════════════════════════════════════════════════════════════
2. REGRA ABSOLUTA DE ENDEREÇOS SALVOS E CÁLCULO DE FRETE
════════════════════════════════════════════════════════════
- SEMPRE QUE FOR PARA ENTREGA:
  1. Chame OBRIGATORIAMENTE a ferramenta `buscar_enderecos_cliente` com `p_telefone`: "{session_id}".
  2. Se a ferramenta retornar endereços salvos: pergunte ao cliente se deseja entregar no endereço encontrado (ex: "Encontrei o endereço salvo: [endereco]. Podemos entregar nesse local?").
  3. Se NÃO houver endereço salvo (ou se o cliente informar outro local): peça o endereço completo (Rua, Número da casa/prédio e Bairro). NUNCA calcule a entrega sem ter o endereço com o número da casa.
  4. Observações sobre o endereço (pontos de referência, número de apartamento, bloco, portão): adicione também no campo `p_observacoes` do pedido!
  5. Com o endereço confirmado, chame a ferramenta `calcular_entrega_completa` e exiba a taxa de entrega calculada. NUNCA invente valor de frete.

════════════════════════════════════════════════════════════
3. REGRA CRÍTICA DO ID DO PEDIDO NA FINALIZAÇÃO
════════════════════════════════════════════════════════════
- Sempre que você criar o pedido via `criar_pedido_completo`, exiba OBRIGATORIAMENTE o número do Pedido (#pedido_id) na mensagem de confirmação final!
- Exemplo: "Seu Pedido #[pedido_id] foi finalizado com sucesso! 🎉"

════════════════════════════════════════════════════════════
4. REGRA DE ADICIONAIS E CORTESIAS (OBRIGATÓRIO)
════════════════════════════════════════════════════════════
- Sempre que o cliente pedir um produto (ex: Frango Inteiro, Meio Frango, Marmita), chame `buscar_adicionais_produto` com o ID do produto.
- O Frango Inteiro (ID 1113) ACOMPANHA 1 CORTESIA GRÁTIS DA CASA. O Meio Frango (ID 1115) ACOMPANHA 2 CORTESIAS GRÁTIS!
- Informe as cortesias inclusas e pergunte qual acompanhamento ele prefere (Arroz, Feijão Tropeiro, Macarrão, Mandioca).

════════════════════════════════════════════════════════════
5. MENSAGEM LEVE DE PAGAMENTO
════════════════════════════════════════════════════════════
- Na etapa de fechamento, informe: "Você pode pagar na entrega no Cartão (crédito/débito), Dinheiro ou PIX na entrega. Ou se preferir, pode pagar agora via PIX!"
- Se for PIX agora: Envie a chave PIX ({empresa_rows.get('chave_pix', '')}) e peça o comprovante.
- Se for na entrega: Confirme a opção e finalize o pedido.

════════════════════════════════════════════════════════════
6. FOTOS E CARDÁPIO DIGITAL
════════════════════════════════════════════════════════════
- ZERO MARKDOWN IMAGES: NUNCA escreva `![nome](http...)` no texto.
- FOTOS NATIVAS: Se o cliente pedir foto, acione a ferramenta `enviar_foto_produto`.
- CARDÁPIO DIGITAL: Se solicitar o cardápio, envie o link limpo {cardapio_digital_url} .
"""

        history = await self.get_chat_history(session_id, limit=14)
        
        user_formatted_msg = f"Informações do contato: {contact_name}, {session_id}. Mensagem: {user_message}, Instancia: {instance}"
        await self.save_message_to_history(session_id, "user", user_formatted_msg)
        
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_formatted_msg})

        for _ in range(5):
            response = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                tools=TOOLS_SCHEMA,
                tool_choice="auto",
                temperature=0.2
            )

            response_msg = response.choices[0].message
            tool_calls = response_msg.tool_calls

            if not tool_calls:
                raw_text = response_msg.content or ""
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
                
                logger.info(f"SubAgente ({model_name}) executando Tool: {fn_name} com args: {fn_args}")
                tool_result_str = await self.execute_tool(fn_name, fn_args, default_user_id=user_id_empresa, instance=instance, remote_jid=remote_jid)
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": fn_name,
                    "content": tool_result_str
                })

        fallback_text = "Estou processando seu pedido. Como posso ajudar com mais alguma opção do cardápio?"
        await self.save_message_to_history(session_id, "assistant", fallback_text)
        return fallback_text

agent_service = AgentService()
