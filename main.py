import logging
from typing import Dict, Any
from fastapi import FastAPI, BackgroundTasks, Request, HTTPException
from fastapi.responses import JSONResponse

from config import settings
from services.supabase_service import supabase_service
from services.evolution_service import evolution_service
from services.vision_service import vision_service
from services.agent_service import agent_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("proia_agent_api")

app = FastAPI(
    title="ProIA Delivery Agent API",
    version="1.0.0",
    description="Microservico de Agente Inteligente de Delivery integrado com OpenAI SDK, Supabase e Evolution API"
)

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "ProIA Delivery Agent API",
        "supabase": "connected"
    }

async def process_whatsapp_message(body: Dict[str, Any]):
    try:
        event = body.get("event")
        if event != "messages.upsert":
            return

        data = body.get("data", {})
        key = data.get("key", {})
        
        if key.get("fromMe"):
            return

        remote_jid = key.get("remoteJid", "")
        if not remote_jid or "g.us" in remote_jid:
            return

        instance = body.get("instance", "")
        apikey = body.get("apikey", "")
        push_name = data.get("pushName", "Cliente")
        message_type = data.get("messageType", "conversation")
        message_obj = data.get("message", {})

        logger.info(f"Mensagem recebida de {push_name} ({remote_jid}) - Tipo: {message_type}")

        # 1. Indicador de presenca imediata no WhatsApp (digitando... ou gravando audio...)
        presence_type = "recording" if message_type == "audioMessage" else "composing"
        await evolution_service.send_presence(instance, remote_jid, presence_type)

        # 2. Resolucao Inteligente da Empresa no Supabase (por apikey, instance ou fallback)
        empresa_data = await supabase_service.get_empresa_by_identifier(apikey, instance)
        if not empresa_data:
            logger.warning(f"Empresa nao encontrada para apikey: {apikey}, instance: {instance}")
            return

        empresa_id = empresa_data.get("id", 43)
        empresa_rows = empresa_data

        # 3. Registrar cliente em clientes_whatsapp
        await supabase_service.registrar_cliente_se_nao_existir(empresa_id, remote_jid, push_name)

        # 4. Verificar se transbordo humano esta ativo
        cliente_db = await supabase_service.get_cliente_whatsapp(empresa_id, remote_jid)
        if cliente_db and cliente_db.get("transbordo_humano"):
            logger.info(f"Transbordo humano ativo para {remote_jid}. Ignorando IA.")
            return

        # 5. Extrair conteudo da mensagem (Texto, Imagem, PDF ou Audio)
        user_message_text = ""
        base64_data = message_obj.get("base64")

        if message_type in ["imageMessage", "documentMessage"] and base64_data:
            user_message_text = await vision_service.analyze_pix_receipt(base64_data, message_type)
        elif message_type == "conversation":
            user_message_text = message_obj.get("conversation", "")
        elif message_type == "extendedTextMessage":
            user_message_text = message_obj.get("extendedTextMessage", {}).get("text", "")
        else:
            user_message_text = message_obj.get("conversation") or "Mensagem recebida"

        if not user_message_text.strip():
            return

        # 6. Manter sinal de 'digitando...' ativado durante a resposta da IA
        await evolution_service.send_presence(instance, remote_jid, presence_type)

        # 7. Executar o Agente Inteligente com OpenAI SDK / OpenRouter
        reply_text = await agent_service.run_agent(
            empresa_data=empresa_data,
            empresa_rows=empresa_rows,
            contact_name=push_name,
            remote_jid=remote_jid,
            user_message=user_message_text,
            instance=instance
        )

        # 8. Disparar a resposta para o WhatsApp via Evolution API
        if reply_text.strip():
            await evolution_service.send_text_message(instance, remote_jid, reply_text)

    except Exception as e:
        logger.error(f"Erro no processamento da mensagem: {e}", exc_info=True)

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        background_tasks.add_task(process_whatsapp_message, body)
        return JSONResponse(status_code=200, content={"status": "received"})
    except Exception as e:
        logger.error(f"Erro ao receber webhook: {e}")
        raise HTTPException(status_code=400, detail="Payload invalido")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
