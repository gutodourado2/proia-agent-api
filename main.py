import re
import json
import time
import asyncio
import traceback
import logging
from datetime import datetime, timezone, timedelta
import httpx
from typing import Dict, Any, List
from fastapi import FastAPI, BackgroundTasks, Request, HTTPException
from fastapi.responses import JSONResponse

from config import settings
from services.supabase_service import supabase_service
from services.evolution_service import evolution_service
from services.vision_service import vision_service
from services.audio_service import audio_service
from services.agent_service import agent_service
from services.tts_service import tts_service

async def forward_webhook_to_n8n(body: Dict[str, Any]):
    webhooks = [url for url in [settings.N8N_FORWARD_WEBHOOK_URL, settings.N8N_SECONDARY_FORWARD_WEBHOOK_URL] if url]
    if not webhooks:
        return
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
        for target_url in webhooks:
            try:
                res = await client.post(target_url, json=body)
                if res.status_code != 200:
                    await supabase_service.registrar_log("WARN", f"n8n webhook ({target_url}) respondeu status {res.status_code}: {res.text[:200]}")
            except Exception as e:
                logger.error(f"Erro ao repassar webhook para n8n ({target_url}): {e}")

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

# 🔒 Estruturas de controle de concorrência, idempotência e anti-loop
active_locks: Dict[str, asyncio.Lock] = {}
PROCESSED_MESSAGE_CACHE: Dict[str, float] = {}
SESSION_MESSAGE_TIMESTAMPS: Dict[str, List[float]] = {}
SESSION_MESSAGE_TEXTS: Dict[str, List[str]] = {}

BOT_AUTO_REPLY_MARKERS = (
    "mensagem automática",
    "mensagem automatica",
    "atendimento automático",
    "atendimento automatico",
    "ausente no momento",
    "não estamos disponíveis no momento",
    "nao estamos disponiveis no momento",
)


def _extract_text_from_message_obj(message_obj: Dict[str, Any], caption: str = "") -> str:
    """Extrai texto de qualquer envelope comum do Baileys/Evolution API v2."""
    if not isinstance(message_obj, dict):
        return caption or ""

    # Desembrulha mensagens efêmeras / viewOnce / documentWithCaption se presentes
    for wrapper_key in ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2", "documentWithCaptionMessage"):
        wrapped = message_obj.get(wrapper_key)
        if isinstance(wrapped, dict) and isinstance(wrapped.get("message"), dict):
            nested = _extract_text_from_message_obj(wrapped["message"], caption=caption)
            if nested:
                return nested

    direct_text = (
        message_obj.get("conversation")
        or (message_obj.get("extendedTextMessage") or {}).get("text")
        or (message_obj.get("buttonsResponseMessage") or {}).get("selectedDisplayText")
        or (message_obj.get("listResponseMessage") or {}).get("title")
        or (message_obj.get("templateButtonReplyMessage") or {}).get("selectedDisplayText")
        or ""
    )
    if direct_text and str(direct_text).strip():
        return str(direct_text).strip()

    # Interactive / Native flow responses
    interactive = message_obj.get("interactiveResponseMessage") or {}
    if isinstance(interactive, dict):
        body_text = (interactive.get("body") or {}).get("text")
        native_params = (interactive.get("nativeFlowResponseMessage") or {}).get("paramsJson")
        if body_text and str(body_text).strip():
            return str(body_text).strip()
        if native_params and str(native_params).strip():
            return str(native_params).strip()

    # Template messages (hydratedTemplate / fourRowTemplate)
    template_msg = message_obj.get("templateMessage") or {}
    if isinstance(template_msg, dict):
        hydrated = template_msg.get("hydratedTemplate") or template_msg.get("hydratedFourRowTemplate") or {}
        if isinstance(hydrated, dict):
            tpl_text = hydrated.get("hydratedContentText") or hydrated.get("hydratedTitleText") or ""
            if tpl_text and str(tpl_text).strip():
                return str(tpl_text).strip()

    return str(caption).strip() if caption else ""


async def _send_text_with_log(instance: str, remote_jid: str, text: str, context: str = "resposta_ia") -> bool:
    """Envia mensagem pela Evolution API e registra ERROR no Supabase se falhar."""
    sent = await evolution_service.send_text_message(instance, remote_jid, text)
    if not sent:
        await supabase_service.registrar_log(
            "ERROR",
            f"Falha ao enviar texto ({context}) via Evolution API para {remote_jid} na instancia {instance}.",
            {"instance": instance, "remote_jid": remote_jid, "context": context, "preview": text[:160]},
        )
    return sent


def get_session_lock(session_id: str) -> asyncio.Lock:
    if session_id not in active_locks:
        active_locks[session_id] = asyncio.Lock()
    return active_locks[session_id]

@app.get("/health")
async def health_check():
    current_key = (settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY).strip().strip('"\'')
    masked_key = f"{current_key[:8]}...{current_key[-4:]}" if len(current_key) >= 12 else ("CONFIGURADA" if current_key else "AUSENTE")
    return {
        "status": "healthy",
        "service": "ProIA Delivery Agent API",
        "evolution_url": settings.EVOLUTION_API_URL,
        "supabase_url": settings.SUPABASE_URL,
        "llm_provider": settings.LLM_PROVIDER,
        "model_name": settings.MODEL_NAME,
        "fallback_model": settings.FALLBACK_MODEL_NAME,
        "second_fallback_model": settings.SECOND_FALLBACK_MODEL_NAME,
        "openrouter_key_status": masked_key,
        "openrouter_key_length": len(current_key),
        "openrouter_key_valid_syntax": bool(current_key.startswith("sk-or-v1-") and " " not in current_key)
    }

async def process_whatsapp_message(body: Dict[str, Any]):
    instance = ""
    remote_jid = ""
    empresa_id = None
    try:
        # 1. Filtro estrito de eventos: processar apenas messages.upsert
        event = str(body.get("event") or "").lower().replace("_", ".")
        if event != "messages.upsert":
            return

        data = body.get("data", {})
        key = data.get("key", {})

        remote_jid = key.get("remoteJid", "")
        remote_jid_alt = key.get("remoteJidAlt", "") or key.get("senderPn", "") or data.get("senderPn", "")
        if remote_jid_alt and "@" not in str(remote_jid_alt):
            remote_jid_alt = f"{remote_jid_alt}@s.whatsapp.net"
        if remote_jid.endswith("@lid") and remote_jid_alt and "@s.whatsapp.net" in remote_jid_alt:
            remote_jid = remote_jid_alt
        if not remote_jid or "g.us" in remote_jid or "broadcast" in remote_jid:
            return

        message_type = data.get("messageType", "conversation")
        # Ignorar tipos que não exigem resposta (reações, edição, protocolo)
        if message_type in ["reactionMessage", "editedMessage", "protocolMessage", "reaction"]:
            return

        # 🛡️ 2. BLINDAGEM CONTRA SINCRONIZAÇÃO DE HISTÓRICO ANTIGO DO WHATSAPP:
        # Quando um aparelho conecta à Evolution API, o WhatsApp Web descarrega mensagens antigas de sincronização.
        # Mensagens enviadas há mais de 15 minutos são descartadas imediatamente.
        message_timestamp = data.get("messageTimestamp")
        if message_timestamp:
            try:
                # Extrair timestamp numérico caso venha como dict protobuf {'low': ...} ou int/str
                if isinstance(message_timestamp, dict):
                    ts_raw = float(message_timestamp.get("low", 0))
                else:
                    ts_raw = float(message_timestamp)
                
                # Se estiver em milissegundos (13 dígitos), normalizar para segundos
                if ts_raw > 10_000_000_000:
                    ts_raw = ts_raw / 1000.0

                now_ts = time.time()
                age_seconds = now_ts - ts_raw

                # Apenas descartar se for histórico antigo real (> 15 minutos / 900s)
                # Permitir folga ampla para celulares com relógio adiantado ou atrasado
                if age_seconds > 900:
                    logger.info(f"Ignorando mensagem de histórico antigo ({age_seconds:.1f}s atrás) de {remote_jid}")
                    return
                if age_seconds < -3600:
                    logger.warning(f"Timestamp futuro anômalo ({age_seconds:.1f}s). Ignorando.")
                    return
            except Exception as e_ts:
                logger.warning(f"Erro ao validar messageTimestamp: {e_ts}")

        # 🛡️ 3. BLINDAGEM DE DEDUPLICAÇÃO DE MENSAGEM (Evita processar o mesmo webhook repetido):
        message_id = key.get("id")
        if message_id:
            if message_id in PROCESSED_MESSAGE_CACHE:
                logger.info(f"Mensagem duplicada já processada ({message_id}). Ignorando.")
                return
            PROCESSED_MESSAGE_CACHE[message_id] = time.time()
            if len(PROCESSED_MESSAGE_CACHE) > 5000:
                for k in list(PROCESSED_MESSAGE_CACHE.keys())[:1000]:
                    PROCESSED_MESSAGE_CACHE.pop(k, None)

        instance_raw = body.get("instance", "")
        if isinstance(instance_raw, dict):
            instance = instance_raw.get("instanceName") or instance_raw.get("name") or ""
        else:
            instance = str(instance_raw)

        apikey = body.get("apikey", "")
        if isinstance(apikey, dict):
            apikey = ""
        push_name = data.get("pushName", "Cliente")
        message_obj = data.get("message", {})

        # 4. Resolução da Empresa no Supabase
        empresa_data = await supabase_service.get_empresa_by_identifier(apikey, instance)
        if not empresa_data:
            await supabase_service.registrar_log("ERROR", "Empresa nao encontrada", {"apikey": apikey, "instance": instance})
            return

        phone_number = remote_jid.split('@')[0] if remote_jid else ""

        # 🛡️ INTERCEPTAÇÃO DE SEGURANÇA MODO TESTER STAGING:
        is_tester = settings.ENABLE_TESTER_MODE and (phone_number in settings.TESTER_PHONE_NUMBERS)
        if is_tester:
            empresa_data_tester = await supabase_service.get_empresa_by_identifier(str(settings.TESTER_EMPRESA_ID), "")
            if empresa_data_tester:
                empresa_data = empresa_data_tester

        empresa_id = empresa_data.get("id", 43)
        empresa_rows = empresa_data
        nome_empresa_raw = empresa_data.get("nome_empresa", "")
        
        if not push_name or push_name.strip() in ["Cantinho do Frango Assado", nome_empresa_raw, "null", "None", ""]:
            push_name = "Cliente"

        voz_agente = empresa_data.get("voz_agente", "feminina")
        session_id = f"{empresa_id}_{phone_number}"

        # 🔒 BLOQUEIO COMPLETO DE SESSÃO: Toda a lógica restante executa dentro do lock por cliente
        async with get_session_lock(session_id):
            # 5. REGRA ABSOLUTA: MENSAGEM ENVIADA MANUALMENTE PELO TELEFONE/INSTANCIA DA EMPRESA (fromMe == True)
            extracted_text = _extract_text_from_message_obj(message_obj)

            if key.get("fromMe"):
                if session_id and extracted_text:
                    await agent_service.save_message_to_history(session_id, "assistant", f"[Atendente Humano da Loja]: {extracted_text}")
                return

            # 🛡️ 6. BLINDAGEM ANTI-FLOOD / ANTI-BOT / PING-PONG INFINITO (Proteção de Custos de API):
            now_mono = time.time()
            timestamps = SESSION_MESSAGE_TIMESTAMPS.get(session_id, [])
            timestamps = [t for t in timestamps if now_mono - t < 300]
            timestamps.append(now_mono)
            SESSION_MESSAGE_TIMESTAMPS[session_id] = timestamps

            norm_msg = " ".join(extracted_text.lower().split()) if extracted_text else ""
            recent_texts = SESSION_MESSAGE_TEXTS.get(session_id, [])
            # Mantém textos alinhados à janela de 300s (armazenados como "ts|texto" ou via lista paralela)
            recent_texts = [item for item in recent_texts if now_mono - item[0] < 300]
            if norm_msg:
                recent_texts.append((now_mono, norm_msg))
            SESSION_MESSAGE_TEXTS[session_id] = recent_texts

            recent_60s = [t for t in timestamps if now_mono - t < 60]
            same_long_count = sum(1 for _, txt in recent_texts if len(norm_msg) >= 15 and txt == norm_msg)
            has_auto_reply_marker = bool(norm_msg and any(marker in norm_msg for marker in BOT_AUTO_REPLY_MARKERS))
            auto_reply_count = sum(
                1 for _, txt in recent_texts if any(marker in txt for marker in BOT_AUTO_REPLY_MARKERS)
            )

            flood_reason = ""
            if len(recent_60s) > 10 or len(timestamps) > 28:
                flood_reason = f"volume excessivo ({len(recent_60s)} em 60s, {len(timestamps)} em 5min)"
            elif same_long_count >= 5:
                flood_reason = f"repetição idêntica ({same_long_count}x em 5min)"
            elif has_auto_reply_marker and auto_reply_count >= 2:
                flood_reason = "resposta automática repetida de outro bot"

            if flood_reason:
                # Evita inundar logs_agente em cada nova mensagem repetida do mesmo flood
                if same_long_count in (5, 10) or len(timestamps) in (29, 40) or auto_reply_count == 2:
                    await supabase_service.registrar_log(
                        "WARN",
                        f"Loop ou flood detectado para {remote_jid} na loja {empresa_id}: {flood_reason}. Ativando transbordo humano para proteger custos de API.",
                        {
                            "session_id": session_id,
                            "reason": flood_reason,
                            "total_5min": len(timestamps),
                            "recent_60s": len(recent_60s),
                            "same_long_count": same_long_count,
                        },
                    )
                    try:
                        await supabase_service.set_transbordo_humano(empresa_id, phone_number, True)
                    except Exception as ex_tb:
                        logger.error(f"Erro ao setar transbordo humano: {ex_tb}")
                return

            # 🛡️ 7. BLINDAGEM DE DESPEDIDAS E PING-PONG DE FECHAMENTO ENTRE BOTS:
            raw_text_for_check = extracted_text.strip().lower()
            
            CLOSING_WORDS = {
                "obrigado", "obrigada", "valeu", "valeu!", "tchau", "tchau!", "flw", "tmj",
                "de nada", "por nada", "encerrar", "encerrando", "até mais", "ate mais"
            }
            recent_interaction = len([t for t in timestamps[:-1] if now_mono - t < 180]) > 0
            if raw_text_for_check in CLOSING_WORDS and recent_interaction:
                history = await agent_service.get_chat_history(session_id, limit=5)
                if history and history[-1].get("role") == "assistant":
                    last_ai = history[-1].get("content", "").lower()
                    if any(w in last_ai for w in ["boa noite", "bom dia", "boa tarde", "disposição", "obrigad", "qualquer coisa", "abraço", "até mais", "encerrar", "disponíveis"]):
                        logger.info(f"Ping-pong de despedida evitado para {remote_jid}: cliente='{raw_text_for_check}'. Silenciando.")
                        await agent_service.save_message_to_history(session_id, "user", raw_text_for_check)
                        return

            await supabase_service.registrar_log("INFO", f"Mensagem recebida para loja {empresa_id} ({empresa_data.get('nome_empresa')}): {push_name} ({remote_jid})", {
                "message_type": message_type,
                "instance": instance,
                "apikey": apikey[:10] if apikey else "N/A",
                "event": event,
                "empresa_id": empresa_id,
                "has_base64": bool(message_obj.get("base64"))
            })

            # 8. VERIFICAÇÃO DE REGRA OBRIGATÓRIA: agente_desabilitado (tabela empresa)
            if empresa_data.get("agente_desabilitado"):
                await supabase_service.registrar_log(
                    "WARN",
                    f"Agente DESABILITADO para a loja {empresa_id} ({empresa_data.get('nome_empresa')}). Ignorando mensagem de {remote_jid}.",
                    {"empresa_id": empresa_id, "remote_jid": remote_jid, "instance": instance},
                )
                return

            # 9. Registrar cliente em clientes_whatsapp
            await supabase_service.registrar_cliente_se_nao_existir(empresa_id, phone_number, push_name)

            # 10. VERIFICAÇÃO DE REGRA OBRIGATÓRIA: transbordo_humano
            cliente_db = await supabase_service.get_cliente_whatsapp(empresa_id, phone_number)
            if cliente_db and cliente_db.get("transbordo_humano"):
                pausa_ate_str = cliente_db.get("pausa_ia_ate")
                transbordo_at_str = cliente_db.get("transbordo_at")
                is_expired = False
                now_utc = datetime.now(timezone.utc)

                if pausa_ate_str:
                    try:
                        pausa_ate = datetime.fromisoformat(pausa_ate_str.replace("Z", "+00:00"))
                        if now_utc > pausa_ate:
                            is_expired = True
                    except Exception:
                        pass
                elif transbordo_at_str:
                    try:
                        transbordo_at = datetime.fromisoformat(transbordo_at_str.replace("Z", "+00:00"))
                        if now_utc - transbordo_at > timedelta(hours=1):
                            is_expired = True
                    except Exception:
                        pass

                if is_expired:
                    logger.info(f"Pausa de IA expirada para {phone_number}. Reativando IA automaticamente.")
                    await supabase_service.set_transbordo_humano(empresa_id, phone_number, status=False)
                else:
                    await supabase_service.registrar_log(
                        "WARN",
                        f"Transbordo humano ATIVO para {phone_number} na loja {empresa_id}. Ignorando mensagem.",
                        {
                            "empresa_id": empresa_id,
                            "phone_number": phone_number,
                            "pausa_ia_ate": pausa_ate_str,
                            "transbordo_at": transbordo_at_str,
                        },
                    )
                    return

            # 11. Indicador de presença imediata no WhatsApp
            presence_type = "recording" if message_type == "audioMessage" else "composing"
            await evolution_service.send_presence(instance, remote_jid, presence_type)

            # 12. Extrair conteúdo da mensagem (Texto, Imagem, PDF, Áudio ou Localização GPS)
            user_message_text = ""
            base64_data = message_obj.get("base64")
            if not base64_data:
                base64_data = data.get("base64")

            caption = (
                message_obj.get("imageMessage", {}).get("caption") or
                message_obj.get("documentMessage", {}).get("caption") or
                data.get("caption") or ""
            )

            if message_type in ["imageMessage", "documentMessage"]:
                if base64_data:
                    user_message_text = await vision_service.analyze_image_or_receipt(base64_data, caption, message_type)
                    await supabase_service.registrar_log("INFO", f"Visao analisou: {caption or 'documento'}")
                else:
                    user_message_text = caption or "[Cliente enviou uma imagem/documento]"

            elif message_type == "audioMessage":
                if base64_data:
                    user_message_text = await audio_service.transcribe_audio(base64_data)
                    logger.info(f"Audio transcrito para {remote_jid}: '{user_message_text}'")
                else:
                    user_message_text = ""

                if not user_message_text or not str(user_message_text).strip():
                    fallback_audio_msg = "Desculpe, não consegui entender o áudio com clareza. 😊 Você poderia digitar ou enviar outro áudio?"
                    await agent_service.save_message_to_history(session_id, "assistant", fallback_audio_msg)
                    await _send_text_with_log(instance, remote_jid, fallback_audio_msg, context="fallback_audio")
                    return

            elif message_type in ["locationMessage", "liveLocationMessage"]:
                loc_data = message_obj.get("locationMessage") or message_obj.get("liveLocationMessage") or {}
                lat = loc_data.get("degreesLatitude")
                lng = loc_data.get("degreesLongitude")
                address = loc_data.get("address") or loc_data.get("name") or "Localização enviada via GPS"

                await agent_service.save_message_to_history(
                    session_id,
                    "user",
                    f"[Cliente enviou localização GPS via WhatsApp: Lat {lat}, Lng {lng}, Endereço/Nome: '{address}']"
                )

                written_addr_msg = (
                    "Recebi sua localização pelo WhatsApp! 📍 Mas para garantir que seu pedido chegue certinho "
                    "e calcularmos a taxa de entrega exata, por favor digite seu endereço completo por escrito: "
                    "Nome da Rua, Número da casa e Bairro. 😊"
                )
                await agent_service.save_message_to_history(session_id, "assistant", written_addr_msg)
                await _send_text_with_log(instance, remote_jid, written_addr_msg, context="localizacao_gps")
                return

            else:
                user_message_text = _extract_text_from_message_obj(message_obj, caption=caption) or "Mensagem recebida"

            if not user_message_text or not str(user_message_text).strip():
                user_message_text = "Olá!"
            else:
                user_message_text = str(user_message_text).strip()

            # Manter presença ativa durante processamento da IA
            await evolution_service.send_presence(instance, remote_jid, presence_type)

            # 13. Executar o Agente Inteligente com cascata econômica e segura
            chosen_model_initial = settings.TESTER_MODEL_NAME if is_tester else settings.MODEL_NAME
            try:
                reply_text = await agent_service.run_agent(
                    empresa_data=empresa_data,
                    empresa_rows=empresa_rows,
                    contact_name=push_name,
                    remote_jid=remote_jid,
                    user_message=user_message_text,
                    instance=instance,
                    model_override=chosen_model_initial,
                    save_user_message=True
                )
            except Exception as e:
                await supabase_service.registrar_log("WARN", f"Modelo principal ({chosen_model_initial}) falhou. Ativando Sub-Agente ({settings.FALLBACK_MODEL_NAME}): {e}")
                try:
                    reply_text = await agent_service.run_agent(
                        empresa_data=empresa_data,
                        empresa_rows=empresa_rows,
                        contact_name=push_name,
                        remote_jid=remote_jid,
                        user_message=user_message_text,
                        instance=instance,
                        model_override=settings.FALLBACK_MODEL_NAME,
                        save_user_message=False
                    )
                except Exception as ex:
                    await supabase_service.registrar_log("ERROR", f"Sub-Agente ({settings.FALLBACK_MODEL_NAME}) falhou. Ativando emergência ({settings.SECOND_FALLBACK_MODEL_NAME}): {ex}")
                    try:
                        reply_text = await agent_service.run_agent(
                            empresa_data=empresa_data,
                            empresa_rows=empresa_rows,
                            contact_name=push_name,
                            remote_jid=remote_jid,
                            user_message=user_message_text,
                            instance=instance,
                            model_override=settings.SECOND_FALLBACK_MODEL_NAME,
                            save_user_message=False
                        )
                    except Exception as ex2:
                        current_key = (settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY).strip().strip('"\'')
                        masked_key = f"{current_key[:8]}...{current_key[-4:]}" if len(current_key) >= 12 else ("VAZIA" if not current_key else "CURTA")
                        await supabase_service.registrar_log("ERROR", f"Falha no fallback de emergência ({settings.SECOND_FALLBACK_MODEL_NAME}): {ex2} | KeyInfo: len={len(current_key)}, mask={masked_key}")
                        reply_text = f"Olá! Seja bem-vindo ao {nome_empresa_raw or 'Cantinho do Frango Assado'}! 😊 Um instante, por favor, já estou consultando nosso sistema para te atender."

            # Filtro de segurança: remover links ou markdown de imagem que possam vazar
            clean_text = re.sub(r'!\[.*?\]\([^\)]+\)', '', reply_text)
            clean_text = re.sub(r'https?://\S+\.(?:jpg|jpeg|png|webp)', '', clean_text)
            clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()

            # 14. Enviar a resposta via Evolution API
            if clean_text:
                await _send_text_with_log(instance, remote_jid, clean_text, context="resposta_ia")

                # Se a mensagem recebida foi áudio, sintetizar áudio TTS
                if message_type == "audioMessage":
                    try:
                        audio_b64 = await tts_service.synthesize_speech(clean_text, voz=voz_agente)
                        if audio_b64:
                            sent = await evolution_service.send_whatsapp_audio(instance, remote_jid, audio_b64)
                            if sent:
                                await supabase_service.registrar_log("INFO", "Audio TTS enviado com sucesso no WhatsApp")
                            else:
                                await supabase_service.registrar_log("ERROR", "Falha ao enviar audio via Evolution API")
                        else:
                            await supabase_service.registrar_log("WARN", "TTS retornou None (sem audio gerado)")
                    except Exception as e_tts:
                        await supabase_service.registrar_log("ERROR", f"Falha no fluxo TTS de audio: {e_tts}")

    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"Erro no processamento da mensagem: {e}", exc_info=True)
        try:
            if remote_jid and empresa_id:
                phone_num = remote_jid.split('@')[0] if remote_jid else ""
                await supabase_service.set_transbordo_humano(empresa_id, phone_num, status=True)
                await supabase_service.registrar_log("CRITICAL", f"Excecao severa -> Transbordo humano ativado para {remote_jid}: {e}", {
                    "traceback": error_trace[:800],
                    "instance": instance,
                    "remote_jid": remote_jid
                })
                fallback_msg = "Desculpe, ocorreu uma instabilidade momentânea no atendimento. Um atendente da nossa equipe irá te responder em breve!"
                await _send_text_with_log(instance, remote_jid, fallback_msg, context="fallback_erro_critico")
        except Exception as ex_critical:
            logger.error(f"Falha no handler de erro severo: {ex_critical}")

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        
        # 1. Se for evento de mensagem (messages.upsert), disparar a IA de Vendas IMEDIATAMENTE sem fila bloqueante
        event = str(body.get("event") or "").lower().replace("_", ".")
        if event == "messages.upsert":
            asyncio.create_task(process_whatsapp_message(body))

        # 2. Repassar eventos do Evolution API para o n8n apenas se explicitamente habilitado
        if getattr(settings, "ENABLE_N8N_FORWARD", False) and settings.N8N_FORWARD_WEBHOOK_URL:
            asyncio.create_task(forward_webhook_to_n8n(body))

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
