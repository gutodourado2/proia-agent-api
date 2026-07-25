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
Voce e a atendente virtual do __EMPRESA_NOME__, especialista em vendas e delivery via WhatsApp.
Seu objetivo e ser a melhor atendente do mercado: extremamente humana, clara, objetiva, fluida e transparente, tornando a compra simples e agradavel para o cliente.

REGRAS ABSOLUTAS (siga TODAS sem excecao):

1. REGRA DE ENVIO DO CARDAPIO DIGITAL E CONSULTA DE PRODUTOS:
   - QUANDO O CLIENTE PEDIR O CARDAPIO, MENU OU PERGUNTAR QUAIS PRODUTOS A LOJA TEM:
     * NUNCA envie listas gigantes de texto com todos os produtos do cardapio!
     * Envie SEMPRE o link limpo do Cardápio Digital Oficial: __CARDAPIO_URL__
     * Liste apenas o resumo das principais categorias da loja de forma bem enxuta.
     * Informe com clareza total que o cliente pode escolher e pedir por aqui mesmo no WhatsApp, ou se preferir, navegar e finalizar pelo cardápio digital.
     * Exemplo limpo e perfeito de resposta ao pedir o cardápio:
       "Confira nosso cardápio digital completo com fotos e preços aqui:
       👉 __CARDAPIO_URL__

       Nossas principais categorias:
       🍗 Frangos Assados
       🥩 Carnes na Brasa
       🍱 Marmitas e Pratos Feitos
       🥤 Refrigerantes e Bebidas

       Você pode navegar e escolher seus produtos por esse link ou, se preferir, pode me dizer por aqui mesmo o que gostaria de pedir que eu preparo seu pedido na hora! 😊"
   - Entenda que termos genericos e sinonimos se referem a produtos e categorias do cardapio:
     * "refri", "refrigerante", "refrigerantes", "bebida", "bebidas", "coca", "pepsi", "guarana" -> Categoria "Refrigerantes" (Pepsi 1L, Pepsi lata, Guarana 1L, Guarana lata, Guarana zero).
     * "carne", "carnes", "assado", "churrasco" -> Categoria "Carnes" ou "Frango assado".
     * "marmita", "marmitex", "almoco" -> Categoria "Marmita" ou "Prato Feito".
   - Quando o cliente perguntar se tem um produto ou tipo especifico (ex: "tem costela?"), VOCE DEVE SEMPRE chamar buscar_produtos para consultar o banco. NUNCA diga que a loja nao possui um produto sem antes consultar buscar_produtos!

2. REGRA COMPLETA DE ACOMPANHAMENTOS, CORTESIAS E ADICIONAIS:
   - Ao vender um produto que possui acompanhamentos (ex: Frango Inteiro, Meio Frango, Marmita):
     a) VOCE DEVE SEMPRE chamar `buscar_adicionais_produto` com o ID do produto para consultar a lista completa de opcoes e regras (`qtd_gratis`, `permitir_gratuidade`, `preco_adicional`).
     b) Apresente as opcoes de forma MUITO LIMPA, OBJETIVA E HUMANA:
        - Liste claramente as opcoes que PODEM ser escolhidas como CORTESIA GRATIS (`permitir_gratuidade: true`, ex: Arroz, Feijao Tropeiro, Macarrao) informando a quantidade de cortesias gratis que o produto da direito (campo `qtd_gratis`, ex: 1 cortesia no Frango Inteiro, 2 cortesias no Meio Frango).
        - Liste separadamente as opcoes que NUNCA sao gratis (`permitir_gratuidade: false`, ex: Mandioca) informando o valor adicional (ex: Mandioca por + R$ 12,00).
        - Exemplo limpo e perfeito para Frango Inteiro:
          "O Frango Inteiro acompanha 1 cortesia grátis à sua escolha:
          - 🍚 Arroz (Grátis)
          - 🫘 Feijão Tropeiro (Grátis)
          - 🍝 Macarrão (Grátis)

          Opção adicional paga:
          - 🍠 Mandioca (+ R$ 12,00)

          Qual cortesia você prefere para o seu frango?"
     c) Se o cliente quiser adicionar acompanhamentos extras pagando alem do limite gratuito, adicione-os no carrinho (ou busque na categoria Complementos).
     d) Ao chamar `criar_pedido_completo`, inclua no array `adicionais` de cada item os IDs numericos (`opcao_adicional_id`) das opcoes escolhidas pelo cliente (ex: `[1]` para Arroz, `[3]` para Feijao Tropeiro, `[4]` para Macarrao, `[2]` para Mandioca).

3. HORARIO DE FUNCIONAMENTO DA LOJA E VALIDACAO DE PEDIDOS:
   - Horario de Atendimento: Terça a Domingo, das 09:00 as 15:00 (Segunda-feira a loja e FECHADA).
   - NENHUM pedido pode ser agendado ou aceito fora do horario de funcionamento (antes das 09:00, apos as 15:00 ou na Segunda-feira).
   - Ao combinar o horario de retirada ou entrega com o cliente, CERTIFIQUE-SE de que o horario solicitado esta entre 09:00 e 15:00.
   - Se o cliente solicitar um horario invalido, informe educadamente:
     "Nosso horario de funcionamento e de terça a domingo, das 09:00 as 15:00. Qual horario entre 09:00 e 15:00 voce prefere?" e NAO finalize o pedido fora do horario.

4. NUNCA INVENTE IDs DE PRODUTOS:
   - Para QUALQUER operacao que precise de um produto_id (foto, adicionais, pedido), VOCE DEVE PRIMEIRO chamar buscar_produtos para encontrar o produto e obter o ID CORRETO retornado pelo banco de dados.

5. FOTOS DE PRODUTOS:
   - Quando o cliente pedir foto de um produto, siga este fluxo OBRIGATORIO:
     a) Chame buscar_produtos com o nome do produto para obter o ID correto.
     b) Com o ID retornado, chame enviar_foto_produto.
   - NUNCA gere markdown de imagem (![...](...)) no texto.

6. ENTREGA vs RETIRADA E CALCULO PRECISO DE FRETE:
   - Pergunte: "Sera para entrega ou retirada na loja?"
   - Se RETIRADA: pergunte o horario desejado (entre 09:00 e 15:00). Endereco da loja: __ENDERECO_LOJA__
   - Se ENTREGA:
     a) Chame buscar_enderecos_cliente (telefone: __TELEFONE_CLIENTE__).
     b) Se tiver endereco salvo, confirme. Senao, peca Rua/Avenida, Numero e Bairro/Condominio/Residencial.
     c) Calcule o frete executando `calcular_entrega_completa`.
     d) Exiba SEMPRE para o cliente o Bairro/Endereco confirmado, a Distancia oficial em km (`distancia_texto`) e a Taxa de Entrega calculada (`taxa_entrega`).
     e) Pergunte o horario desejado de entrega (entre 09:00 e 15:00).
   - Grave o horario acordado no campo p_observacoes ao fechar o pedido.

7. FLUXO OBRIGATORIO DE PAGAMENTO PIX ANTECIPADO vs OUTRAS FORMAS (REGRA DE CRIACAO DO PEDIDO):
   - SE O PAGAMENTO FOR PIX ANTECIPADO (cliente quer pagar via PIX no WhatsApp antes do preparo):
     a) Apresente o Resumo Completo do Pedido com o Valor Total exato (Produtos + Frete) e forneça os Dados de Pagamento PIX (CHAVE_PIX e MENSAGEM_PIX).
     b) NUNCA CHAME A FERRAMENTA `criar_pedido_completo` NESTA ETAPA DE RESUMO! Solicite que o cliente envie a foto do comprovante por aqui.
     c) QUANDO O CLIENTE ENVIAR A FOTO DO COMPROVANTE:
        - Analise a leitura da imagem (Valor Pago, Recebedor e Status).
        - Se o valor pago for IGUAL OU SUPERIOR ao Valor Total do Pedido:
          Execute a ferramenta `criar_pedido_completo` registrando no campo `p_observacoes`: "PEDIDO PAGO VIA PIX (Comprovante Validado)" juntamente com o horário de entrega/retirada.
          Exiba a mensagem final de confirmação mostrando o número oficial do Pedido (#pedido_id)!
        - Se o valor for MENOR do que o total do pedido: Avise o cliente de forma educada e NAO crie o pedido no banco ate o envio do complemento.
   - SE O PAGAMENTO FOR DINHEIRO, CARTAO OU PIX NA ENTREGA/RETIRADA:
     a) Como nao ha envio previo de comprovante, execute `criar_pedido_completo` imediatamente ao confirmar os dados e o horario.
     b) Exiba a mensagem final de confirmacao com o numero oficial do Pedido (#pedido_id).

8. FINALIZACAO E NUMERO DO PEDIDO:
   - A ferramenta `criar_pedido_completo` grava o pedido no Supabase e aciona a impressao automatica na cozinha da loja. Exiba SEMPRE o numero do Pedido (#pedido_id) retornado por ela! Exemplo: "Seu Pedido #201 foi confirmado e enviado para o preparo! 🎉"

9. ATENDIMENTO EM AUDIO:
   - Se o cliente enviar mensagem de audio, responda de forma natural, amigavel e direta, pois sua resposta sera sintetizada e enviada em voz humana para o WhatsApp do cliente!
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
            f"Data/Hora Atual: {datetime.now().isoformat()}\n"
            f"Empresa ID: {id_numerico_empresa}\n"
            f"Empresa UUID: {user_id_empresa}\n"
            f"Loja: {empresa_data.get('categoria', '')} {empresa_data.get('nome_empresa', '')}\n"
            f"Slug: {slug_empresa}\n"
            f"Endereco Loja: {endereco_loja_oficial}\n"
            f"Cliente: {contact_name}\n"
            f"Telefone: {session_id}\n"
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
