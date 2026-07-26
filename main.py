import re
import json
import traceback
import logging
from typing import Dict, Any
from fastapi import FastAPI, BackgroundTasks, Request, HTTPException
from fastapi.responses import JSONResponse

from config import settings
from services.supabase_service import supabase_service
from services.evolution_service import evolution_service
from services.vision_service import vision_service
from services.audio_service import audio_service
from services.agent_service import agent_service
from services.tts_service import tts_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("proia_agent_api")

app = FastAPI(
    title="ProIA Delivery Agent API",
    version="1.0.0",
    description="Microservico de Agente Inteligente de Delivery integrado com OpenAI SDK, OpenRouter TTS, Supabase e Evolution API"
)

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "ProIA Delivery Agent API",
        "evolution_url": settings.EVOLUTION_API_URL,
        "supabase_url": settings.SUPABASE_URL
    }

async def process_whatsapp_message(body: Dict[str, Any]):
    instance = ""
    remote_jid = ""
    empresa_id = None
    try:
        event = body.get("event")
        if event != "messages.upsert":
            return

        data = body.get("data", {})
        key = data.get("key", {})

        remote_jid = key.get("remoteJid", "")
        if not remote_jid or "g.us" in remote_jid:
            return

        instance_raw = body.get("instance", "")
        if isinstance(instance_raw, dict):
            instance = instance_raw.get("instanceName") or instance_raw.get("name") or ""
        else:
            instance = str(instance_raw)

        apikey = body.get("apikey", "")
        if isinstance(apikey, dict):
            apikey = ""
        push_name = data.get("pushName", "Cliente")
        message_type = data.get("messageType", "conversation")
        message_obj = data.get("message", {})

        # 1. REGRA ABSOLUTA: MENSAGEM ENVIADA MANUALMENTE PELO TELEFONE/INSTANCIA DA EMPRESA (fromMe == True)
        # Quando o dono/atendente da loja digita manualmente no WhatsApp, a IA fica em SILÊNCIO TOTAL (zero resposta),
        # mas registra o texto no historico de conversas para manter o contexto se o cliente responder depois.
        if key.get("fromMe"):
            session_id = remote_jid.split('@')[0] if remote_jid else ""
            manual_text = message_obj.get("conversation") or message_obj.get("extendedTextMessage", {}).get("text") or ""
            if session_id and manual_text:
                await agent_service.save_message_to_history(session_id, "assistant", f"[Atendente Humano da Loja]: {manual_text}")
                await supabase_service.registrar_log("INFO", f"Mensagem manual da loja para {session_id} salva no historico. IA em silencio.")
            return

        await supabase_service.registrar_log("INFO", f"Mensagem recebida: {push_name} ({remote_jid})", {
            "message_type": message_type,
            "instance": instance,
            "apikey": apikey[:10] if apikey else "N/A",
            "event": event,
            "has_base64": bool(message_obj.get("base64"))
        })

        # 2. Resolucao Inteligente da Empresa no Supabase (por apikey ou instance)
        empresa_data = await supabase_service.get_empresa_by_identifier(apikey, instance)
        if not empresa_data:
            await supabase_service.registrar_log("ERROR", "Empresa nao encontrada", {"apikey": apikey, "instance": instance})
            return

        empresa_id = empresa_data.get("id", 43)
        empresa_rows = empresa_data
        voz_agente = empresa_data.get("voz_agente", "feminina")

        # 3. VERIFICACAO DE REGRA OBRIGATORIA: agente_desabilitado (tabela empresa)
        # Se o agente estiver desabilitado para a loja, DEIXA DE RESPONDER qualquer mensagem!
        if empresa_data.get("agente_desabilitado"):
            await supabase_service.registrar_log("INFO", f"Agente esta DESABILITADO para a loja {empresa_id}. Ignorando mensagem.")
            return

        # 4. Registrar cliente em clientes_whatsapp
        await supabase_service.registrar_cliente_se_nao_existir(empresa_id, remote_jid, push_name)

        # 5. VERIFICACAO DE REGRA OBRIGATORIA: transbordo_humano (tabela clientes_whatsapp)
        # Se o atendimento humano estiver ativo para o numero deste cliente, a IA NAO responde.
        cliente_db = await supabase_service.get_cliente_whatsapp(empresa_id, remote_jid)
        if cliente_db and cliente_db.get("transbordo_humano"):
            await supabase_service.registrar_log("INFO", f"Transbordo humano ATIVO para cliente {remote_jid}. Ignorando mensagem da IA.")
            return

        # 6. Indicador de presenca imediata no WhatsApp (digitando... ou gravando audio...)
        presence_type = "recording" if message_type == "audioMessage" else "composing"
        await evolution_service.send_presence(instance, remote_jid, presence_type)

        # 7. Extrair conteudo da mensagem (Texto, Imagem, PDF, Audio ou Localizacao GPS)
        user_message_text = ""
        base64_data = message_obj.get("base64")
        if not base64_data:
            base64_data = data.get("base64")

        caption = (
            message_obj.get("imageMessage", {}).get("caption") or
            message_obj.get("documentMessage", {}).get("caption") or
            data.get("caption") or ""
        )

        if message_type == "audioMessage":
            if base64_data:
                try:
                    transcription = await audio_service.transcribe_audio_base64(base64_data)
                    user_message_text = transcription or "Mensagem de áudio recebida"
                    await supabase_service.registrar_log("INFO", f"Audio transcrito: {user_message_text[:80]}")
                except Exception as e:
                    await supabase_service.registrar_log("ERROR", f"Falha Whisper: {e}")
                    user_message_text = "Mensagem de áudio recebida"
            else:
                user_message_text = "Mensagem de áudio recebida"
                await supabase_service.registrar_log("WARN", "audioMessage sem base64")

        elif message_type in ["imageMessage", "documentMessage"]:
            if base64_data:
                try:
                    user_message_text = await vision_service.analyze_image_or_receipt(base64_data, user_caption=caption, message_type=message_type)
                    await supabase_service.registrar_log("INFO", f"Visao analisou: {user_message_text[:80]}")
                except Exception as e:
                    await supabase_service.registrar_log("ERROR", f"Falha visao: {e}")
                    user_message_text = caption or "Cliente enviou uma imagem"
            else:
                user_message_text = caption or "Cliente enviou uma imagem"
                await supabase_service.registrar_log("WARN", f"{message_type} sem base64")

        elif message_type in ["locationMessage", "liveLocationMessage"]:
            loc_obj = message_obj.get("locationMessage") or message_obj.get("liveLocationMessage") or data.get("locationMessage") or {}
            lat = loc_obj.get("degreesLatitude")
            lng = loc_obj.get("degreesLongitude")
            address = loc_obj.get("address") or loc_obj.get("name") or loc_obj.get("comment") or ""
            await supabase_service.registrar_log("INFO", f"Localizacao GPS recebida: Lat {lat}, Lng {lng}, Endereco: {address}")
            
            session_id = remote_jid.split('@')[0] if remote_jid else ""
            if session_id:
                await agent_service.save_message_to_history(session_id, "user", f"[Cliente enviou localização GPS via WhatsApp: Lat {lat}, Lng {lng}, Endereço/Nome: '{address}']")
            
            # Enviar apenas mensagem cordial de agradecimento sem alterar o pedido ou recalcular valores
            thanks_msg = "Muito obrigado por enviar sua localização! 📍 Já repassei para o nosso entregador para facilitar a sua entrega. 😊"
            await evolution_service.send_text_message(instance, remote_jid, thanks_msg)
            if session_id:
                await agent_service.save_message_to_history(session_id, "assistant", thanks_msg)
            return

        elif message_type == "conversation":
            user_message_text = message_obj.get("conversation", "")
        elif message_type == "extendedTextMessage":
            user_message_text = message_obj.get("extendedTextMessage", {}).get("text", "")
        else:
            user_message_text = message_obj.get("conversation") or caption or "Mensagem recebida"

        if not user_message_text.strip():
            user_message_text = "Olá!"

        # 8. Manter sinal de 'digitando...' ou 'gravando audio...' ativado durante o processamento da IA
        await evolution_service.send_presence(instance, remote_jid, presence_type)

        # 9. Executar o Agente Inteligente com OpenAI SDK / OpenRouter
        try:
            reply_text = await agent_service.run_agent(
                empresa_data=empresa_data,
                empresa_rows=empresa_rows,
                contact_name=push_name,
                remote_jid=remote_jid,
                user_message=user_message_text,
                instance=instance
            )
        except Exception as e:
            await supabase_service.registrar_log("ERROR", f"Falha no Agente LLM (tentando fallback OpenAI gpt-4o-mini): {e}", {"traceback": traceback.format_exc()[:500]})
            try:
                reply_text = await agent_service.run_agent(
                    empresa_data=empresa_data,
                    empresa_rows=empresa_rows,
                    contact_name=push_name,
                    remote_jid=remote_jid,
                    user_message=user_message_text,
                    instance=instance,
                    model_override="gpt-4o-mini"
                )
            except Exception as ex:
                await supabase_service.registrar_log("ERROR", f"Falha no fallback OpenAI: {ex}")
                reply_text = "Temos sim! Nossos pratos e produtos estão disponíveis hoje. O que você gostaria de pedir?"

        # Filtro de seguranca absoluto: remover qualquer formato markdown de imagem antes de enviar ao WhatsApp
        clean_text = re.sub(r'!\[.*?\]\([^\)]+\)', '', reply_text)
        clean_text = re.sub(r'https?://\S+\.(?:jpg|jpeg|png|webp)', '', clean_text)
        clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()

        # 10. Disparar a resposta para o WhatsApp via Evolution API (Texto e/ou Áudio TTS)
        if clean_text:
            await evolution_service.send_text_message(instance, remote_jid, clean_text)

            # Se a mensagem recebida foi audioMessage, sintetizar e enviar a resposta em áudio de voz PTT
            if message_type == "audioMessage":
                try:
                    audio_b64 = await tts_service.generate_speech_base64(clean_text, gender=voz_agente)
                    if audio_b64:
                        sent = await evolution_service.send_whatsapp_audio(instance, remote_jid, audio_b64)
                        if sent:
                            await supabase_service.registrar_log("INFO", "Audio TTS enviado com sucesso no WhatsApp")
                        else:
                            await supabase_service.registrar_log("ERROR", "Falha ao enviar audio via Evolution API")
                    else:
                        await supabase_service.registrar_log("WARN", "TTS retornou None (sem audio gerado)")
                except Exception as e:
                    await supabase_service.registrar_log("ERROR", f"Falha no fluxo TTS de audio: {e}")

    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"Erro no processamento da mensagem: {e}", exc_info=True)
        # Em caso de erro severo nao tratado, ativar transbordo humano e notificar
        try:
            if remote_jid:
                await supabase_service.set_transbordo_humano(remote_jid, status=True)
                await supabase_service.registrar_log("CRITICAL", f"Excecao severa -> Transbordo humano ativado para {remote_jid}: {e}", {
                    "traceback": error_trace[:800],
                    "instance": instance,
                    "remote_jid": remote_jid
                })
                fallback_msg = "Desculpe, ocorreu um erro no atendimento automático. Um de nossos atendentes humanos irá te ajudar em breve!"
                await evolution_service.send_text_message(instance, remote_jid, fallback_msg)
        except Exception as ex:
            logger.error(f"Falha no handler de erro severo: {ex}")

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        background_tasks.add_task(process_whatsapp_message, body)
        return JSONResponse(status_code=200, content={"status": "received"})
    except Exception as e:
        logger.error(f"Erro ao receber webhook: {e}")
        raise HTTPException(status_code=400, detail="Payload invalido")

@app.post("/webhook/status-pedido")
async def status_pedido_webhook(request: Request):
    try:
        body = await request.json()
        record = body.get("record") or body.get("data") or body
        pedido_id = record.get("id")
        status = record.get("status")
        telefone = record.get("telefone_cliente")
        instance = record.get("instancia_whatsapp") or "vendas-72055e41-11"
        nome_cliente = record.get("nome_cliente", "Cliente").split(" - ")[0]

        if not telefone or not status:
            return JSONResponse(status_code=400, content={"erro": "Dados insuficientes"})

        remote_jid = telefone if "@s.whatsapp.net" in telefone else f"{telefone}@s.whatsapp.net"

        STATUS_MENSAGENS = {
            1: f"Olá, {nome_cliente}! 👨‍🍳 Seu Pedido #{pedido_id} foi RECEBIDO pelo restaurante e já está aguardando preparo!",
            2: f"Olá, {nome_cliente}! 👨‍🍳🔥 Seu Pedido #{pedido_id} está EM PREPARO pela nossa equipe de cozinha!",
            3: f"Olá, {nome_cliente}! 🛵💨 Notícia boa! Seu Pedido #{pedido_id} SAIU PARA ENTREGA! Nosso entregador já está a caminho do seu endereço.",
            4: f"Olá, {nome_cliente}! 🎉 Seu Pedido #{pedido_id} foi ENTREGUE com sucesso! Agradecemos a preferência e bom apetite! 😋",
            5: f"Olá, {nome_cliente}. Seu Pedido #{pedido_id} foi CANCELADO. Se tiver qualquer dúvida, estamos à disposição."
        }

        msg = STATUS_MENSAGENS.get(status)
        if msg:
            await evolution_service.send_text_message(instance, remote_jid, msg)

        return JSONResponse(status_code=200, content={"sucesso": True, "status": status})
    except Exception as e:
        logger.error(f"Erro ao processar webhook status-pedido: {e}")
        return JSONResponse(status_code=500, content={"erro": str(e)})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
