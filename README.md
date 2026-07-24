# 📌 REGRAS E DIRETRIZES DO PROJETO AGENTE PROIA DELIVERY

## 🚀 Arquitetura & Deploy
- **Microserviço**: Python FastAPI + AsyncOpenAI / OpenRouter SDK + Supabase + Evolution API.
- **Repositório Git**: `/Users/gutemberguedourado/Documents/GitHub/proia-agent-api` (branch `main`).
- **Deploy**: Automático no Coolify a cada `git push origin main`.

---

## 📊 Telemetria & Logs em Tempo Real
- **Logs de Execução**: Gravados em tempo real na tabela `public.logs_agente` no Supabase (`nivel`, `mensagem`, `detalhes`, `created_at`).
- **Histórico de Conversas**: Armazenado na tabela `public.n8n_chat_histories` com `session_id` (número do cliente).
- **Notificação de Status do Pedido**: Endpoint `/webhook/status-pedido` consome zero tokens LLM e envia mensagens automáticas quando o status muda (1: Recebido, 2: Em Preparo, 3: Saiu para Entrega, 4: Entregue).

---

## 🛍️ Regras Obrigatórias de Atendimento e Checkout

### 1. Número do Pedido (#ID) na Confirmação
- A mensagem final de confirmação emitida após a chamada da ferramenta `criar_pedido_completo` DEVE OBRIGATORIAMENTE exibir o número do pedido (ex: `Seu Pedido #196 foi finalizado com sucesso! 🎉`).

### 2. Adicionais e Cortesias (Gravação no Banco)
- **Frango Inteiro (ID 1113)**: Acompanha 1 cortesia grátis.
- **Meio Frango (ID 1115)**: Acompanha 2 cortesias grátis.
- Ao chamar `criar_pedido_completo`, a IA DEVE OBRIGATORIAMENTE passar o array com os IDs numéricos das opções escolhidas (`adicionais: [1, 3]`) em `p_itens` para que sejam gravados em `public.itens_pedido_adicionais` e impressos na comanda do balcão.

### 3. Retirada vs. Entrega & Horário Desejado
- Perguntar obrigatoriamente se será **Retirada na loja** ou **Entrega**.
- Perguntar o **Horário Desejado** de entrega ou retirada.
- Gravar o horário acordado no campo `p_observacoes` ao fechar o pedido (ex: `"Horário de retirada: 12:30"`).
- Se for retirada, informar o endereço oficial da empresa: `R. São Francisco, 2249 - Lot. Mimoso Doeste I, Luís Eduardo Magalhães - BA`.

### 4. Endereços Salvos do Cliente
- Para entregas, chamar obrigatoriamente a ferramenta `buscar_enderecos_cliente` antes de solicitar um novo endereço.
- Se houver endereço salvo, confirmar com o cliente. Se não houver, pedir endereço completo (Rua, Número e Bairro).
- NUNCA calcular o valor da taxa de entrega sem ter o endereço confirmado.

### 5. Pagamento & Validação PIX
- Formas aceitas: Cartão (crédito/débito), Dinheiro com troco, PIX na entrega ou PIX agora.
- Para PIX enviado: Validar o valor do comprovante com o Valor Total do Pedido (Produtos + Frete).
- Se o PIX for validado com sucesso, gravar no campo `p_observacoes`: `"PEDIDO PAGO VIA PIX (Comprovante Validado)"`.

---

## 🎙️ Áudio, Voz TTS e Visão Computacional

### 1. Síntese de Voz (TTS) com OpenRouter / Gemini 3.1 Flash
- Endpoint: `https://openrouter.ai/api/v1/audio/speech` com modelo `google/gemini-3.1-flash-tts-preview` (Voz `puck` para feminina, `Kore` para masculina).
- **Conversor PCM to WAV**: Injeta um cabeçalho RIFF/WAV de 44 bytes a 24000 Hz (`pcm_to_wav`) antes de codificar em base64 para que o WhatsApp reproduza o som nativamente.
- **Fallback OpenAI**: `gpt-4o-mini-tts` / `tts-1` (`coral` / `cedar` ou `alloy` / `echo`).

### 2. Transcrição de Voz (OpenAI Whisper)
- Transcreve mensagens de voz do cliente (`audioMessage`) via `whisper-1`.

### 3. Visão de Imagens
- Analisa fotos enviadas pelo cliente (`imageMessage`) via `gpt-4o-mini` / `gemini-2.5-flash` respondendo dúvidas sobre a imagem (ex: *"O que você vê nessa imagem"*) e validando comprovantes de pagamento.

### 4. SubAgente de Emergência (Zero Vácuo)
- Toda exceção é capturada para enviar uma resposta cordial no WhatsApp do cliente, impedindo que o cliente fique sem resposta.
