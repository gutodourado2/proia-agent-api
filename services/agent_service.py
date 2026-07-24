import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import httpx
from openai import AsyncOpenAI
from config import settings
from services.supabase_service import supabase_service

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
            "description": "Calcula a distancia em KM e a taxa oficial de entrega para o endereco.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_empresa_id": {"type": "string", "description": "ID da empresa"},
                    "p_endereco": {"type": "string", "description": "Endereco completo do cliente (rua, numero, bairro)"}
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
                        messages.append({"role": "user" if role == "user" else "assistant", "content": content})
                return messages
            except Exception as e:
                logger.error(f"Erro ao ler n8n_chat_histories: {e}")
                return []

    async def save_message_to_history(self, session_id: str, role: str, content: str):
        url = f"{supabase_service.base_url}/rest/v1/n8n_chat_histories"
        msg_type = "human" if role == "user" else "ai"
        payload = {
            "session_id": session_id,
            "message": {
                "type": msg_type,
                "content": content,
                "additional_kwargs": {},
                "response_metadata": {}
            }
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                await client.post(url, headers=supabase_service.headers, json=payload)
            except Exception as e:
                logger.error(f"Erro ao salvar mensagem no historico: {e}")

    async def execute_tool(self, name: str, args: Dict[str, Any], default_user_id: str = "") -> str:
        try:
            if "p_empresa_id" in args:
                args["p_empresa_id"] = default_user_id or str(args["p_empresa_id"])

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

        chosen_model = model_override or empresa_data.get("modelo_ia") or settings.MODEL_NAME
        client, model_name = self.get_client_for_model(chosen_model)

        logger.info(f"Executando Agente de Delivery para {contact_name} - Modelo: {model_name} - Loja: {slug_empresa}")

        system_prompt = f"""AGORA: {datetime.now().isoformat()}
EMPRESA_USER_ID: {user_id_empresa}
EMPRESA_NUMERIC_ID: {id_numerico_empresa}
EMPRESA_NOME: {empresa_data.get('categoria', '')} {empresa_data.get('nome_empresa', '')}
LOJA_SLUG: {slug_empresa}
CARDAPIO_DIGITAL_URL: {cardapio_digital_url}
LOJA_ENDEREÇO: {empresa_rows.get('endereco', '')}
CLIENTE_NOME: {contact_name}
CLIENTE_CONTATO: {remote_jid}
REGRAS_ESPECIFICAS_DA_LOJA: {empresa_data.get('regras_adicionais', '')}
VALOR_POR_KM: {empresa_data.get('valor_por_km', 0)}
VALOR_MINIMO_ENTREGA: {empresa_data.get('valor_minimo_entrega', 0)}
DISTANCIA_MAXIMA_KM: {empresa_data.get('distancia_maxima_km', 0)}
LOJA_FECHADA_MANUAL: {empresa_rows.get('loja_fechada_manual', False)}
CHAVE_PIX: {empresa_rows.get('chave_pix', '')}
MENSAGEM_PIX: {empresa_rows.get('mensagem_pix', '')}

Você é o atendente virtual DE DELIVERY profissional, direto e eficiente da {empresa_data.get('categoria', '')} {empresa_data.get('nome_empresa', '')}.
Sua missão é realizar atendimentos rápidos, objetivos e converter pedidos com perfeição, sem erros e sem perda de tempo.

════════════════════════════════════════════════════════════
1. REGRA ABSOLUTA DE MÍDIA E CARDÁPIO DIGITAL
════════════════════════════════════════════════════════════
- NUNCA inclua links de imagem ou sintaxe de imagem em markdown (como `![nome](http...)`) em nenhuma mensagem. O WhatsApp não exibe imagens em markdown.
- QUANDO O CLIENTE PEDIR O CARDÁPIO (ex: "me envia o cardápio", "cardapio"):
  1. Chame a ferramenta `listar_categorias` com `p_empresa_id`: "{user_id_empresa}".
  2. Apresente as categorias de forma limpa e elegante em texto.
  3. Envie o Link Oficial do Cardápio Digital: {cardapio_digital_url}

════════════════════════════════════════════════════════════
2. FLUXO DE PAGAMENTO PADRÃO VS PIX SOB DEMANDA
════════════════════════════════════════════════════════════
- FLUXO PADRÃO: O método padrão de pagamento é NA ENTREGA (Cartão de Crédito/Débito ou Dinheiro com troco). Pergunte a forma de pagamento na entrega apenas na etapa de finalização do pedido.
- PIX SOMENTE SE SOLICITADO: NÃO peça pagamento por PIX no início. APENAS se o cliente pedir expressamente (ex: "quero pagar no pix", "envia o pix"):
  - Envie a Chave PIX: {empresa_rows.get('chave_pix', '')}
  - Envie as orientações da loja: {empresa_rows.get('mensagem_pix', '')}
  - Solicite o envio do comprovante.
- RECEBIMENTO DE COMPROVANTE: Quando a mensagem iniciar com "[Cliente enviou um comprovante...":
  - Leia atentamente os dados extraídos da visão computacional (status, valor e recebedor).
  - Se o pedido já tiver sido gravado via `criar_pedido_completo`: confirme que o pagamento do Pedido #[pedido_id] foi confirmado e que ele já está em preparo!
  - Se o pedido ainda não tiver sido gravado: recupere os itens e o endereço das mensagens anteriores do histórico, execute `criar_pedido_completo` e confirme o pedido! NUNCA diga que o pedido está vazio.

════════════════════════════════════════════════════════════
3. VENDA DIRETA: FRANGO INTEIRO E ACOMPANHAMENTOS/CORTESIAS
════════════════════════════════════════════════════════════
- Quando o cliente pedir "1 frango inteiro", ele JÁ QUER COMPRAR!
- Chame imediatamente a ferramenta `buscar_adicionais_produto` para o produto Frango Inteiro.
- Faça uma pergunta ultra direta e objetiva sobre o acompanhamento de cortesia:
  Exemplo: "Frango Inteiro (R$ 70,00) anotado! Qual cortesia você prefere: Farofa ou Maionese?"
- Se o cliente desejar adicionais pagos extras, inclua-os no pedido sem enrolação.

════════════════════════════════════════════════════════════
4. CONVERSÃO INTELIGENTE DE PESOS E GRAMAS (SUÍNA VS BOVINA)
════════════════════════════════════════════════════════════
- Entenda "meio quilo" / "1/2kg" como 500g.
- Entenda "1 quilo" / "1kg" como 1000g.
- Se o cliente solicitar uma quantidade que não existe exatamente no cardápio (ex: 400g):
  - Sugira e ofereça a porção mais próxima disponível (ex: 500g suína ou bovina/gado).
  - Exemplo: "Não temos porção de 400g exata, mas temos a porção de 500g (R$ XX). Podemos adicionar?"
- DESAMBIGUAÇÃO DIRETA: Se o cliente pedir "costela" sem especificar o tipo, faça UMA ÚNICA Pergunta Objetiva:
  Exemplo: "Você prefere Costela Suína (Porco) ou Bovina (Gado)?"
  NÃO envie listas gigantes de produtos desnecessários.

════════════════════════════════════════════════════════════
5. RESPOSTAS OBJETIVAS, FECHAMENTO DE PEDIDO E ALTERAÇÕES PÓS-FECHAMENTO
════════════════════════════════════════════════════════════
- Respostas curtas, elegantes e objetivas (1 a 2 emojis no máximo).
- SEMPRE ao adicionar um item ao carrinho, pergunte de forma ativa:
  "Deseja adicionar mais algum item ou podemos calcular a entrega e finalizar?"
- PEDIDO JÁ CONFIRMADO E SALVO:
  - Quando gravar o pedido via `criar_pedido_completo`, informe o número do pedido e que ele foi enviado para a cozinha.
  - Se o cliente quiser adicionar novos itens DEPOIS que o pedido foi fechado:
    - Consulte o status do pedido via `consultar_pedido`.
    - Se o pedido já estiver em preparo ou "saiu para entrega", informe educadamente que os novos itens serão adicionados em um NOVO PEDIDO (com nova taxa de entrega se o anterior já tiver saído).
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
                final_text = response_msg.content or ""
                await self.save_message_to_history(session_id, "assistant", final_text)
                return final_text

            messages.append(response_msg)

            for tool_call in tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except Exception:
                    fn_args = {}
                
                logger.info(f"SubAgente ({model_name}) executando Tool: {fn_name} com args: {fn_args}")
                tool_result_str = await self.execute_tool(fn_name, fn_args, default_user_id=user_id_empresa)
                
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
