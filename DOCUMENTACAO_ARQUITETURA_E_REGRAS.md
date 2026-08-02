# 📖 DOCUMENTAÇÃO COMPLETA DE ARQUITETURA E REGRAS DO AGENTE PROIA DELIVERY

> **Data de Atualização**: 01/08/2026  
> **Repositório**: `proia-agent-api` (Branch `main`)  
> **Tecnologias**: Python FastAPI, OpenAI SDK, OpenRouter, Supabase PostgreSQL, Evolution API, Google Places & Distance Matrix API.

---

## 📑 SUMÁRIO
1. [Visão Geral e Arquitetura de Deploy](#1-visão-geral-e-arquitetura-de-deploy)
2. [Modelos de IA e Fallback Duplo](#2-modelos-de-ia-e-fallback-duplo)
3. [Webhook Único e Repasse Automático ao n8n](#3-webhook-único-e-repasse-automático-ao-n8n)
4. [Isolamento Multitenant por Empresa (`session_id`)](#4-isolamento-multitenant-por-empresa-session_id)
5. [Prompts Customizados por Empresa (`prompt_customizado`)](#5-prompts-customizados-por-empresa-prompt_customizado)
6. [Geolocalização de Alta Precisão (Google Places API)](#6-geolocalização-de-alta-precisão-google-places-api)
7. [Diretrizes de Atendimento Humanizado (1 a 3 Linhas)](#7-diretrizes-de-atendimento-humanizado-1-a-3-linhas)
8. [Regras Específicas de Checkout e PIX](#8-regras-específicas-de-checkout-e-pix)

---

## 1. Visão Geral e Arquitetura de Deploy

- **Repositório Git**: `/Users/gutemberguedourado/Documents/GitHub/proia-agent-api`
- **Deploy**: Automático no Coolify a cada `git push origin main`.
- **Evolution API**: `https://evo.proia.com.br` (Chave Global: `72055e41-9f72-4dac-97c2-7b5109890b50`).
- **Supabase URL**: `https://askqkwvpjhotytmxcfqx.supabase.co`

---

## 2. Modelos de IA e Fallback Duplo

- **Modelo Principal**: `google/gemini-3.6-flash` via OpenRouter SDK.
- **Fallback Nativo OpenAI**: Se a OpenRouter falhar (erro 402, instabilidade ou timeout), o sistema faz failover automático para a API nativa da OpenAI usando `gpt-4o`.
- **Proteção de Reserva de Créditos**: O parâmetro `max_tokens=2048` está explicitamente configurado em todas as chamadas `chat.completions.create` para evitar recusas de reserva de saldo pela OpenRouter.

---

## 3. Webhook Único e Repasse Automático ao n8n

- **Endpoint Único na Evolution API**: `http://tljykctcs216vr0dfr111zg7.72.61.54.174.sslip.io/webhook/whatsapp`
- **Funcionamento**:
  - 100% dos payloads recebidos (incluindo QR Code, Conexão, Instâncias e Mensagens) são **repassados em segundo plano (background tasks)** para a URL do n8n:  
    `https://n8n.proia.com.br/webhook/42456d3e-1951-4f2b-9290-c08dee4a52d0`
  - Se o evento for uma mensagem de chat de cliente (`messages.upsert`), o agente Python assume o atendimento e responde no WhatsApp.

---

## 4. Isolamento Multitenant por Empresa (`session_id`)

- **Chave de Sessão**: Para evitar o vazamento de contexto de conversas entre empresas diferentes, a chave de histórico `session_id` gravada em `public.n8n_chat_histories` e `public.clientes_whatsapp` é formatada estritamente como:
  ```text
  {EMPRESA_ID}_{TELEFONE_CLIENTE}
  ```
  *Exemplo*: `43_557798728307` (Cantinho do Frango) vs `4_557798728307` (ProIA Delivery / Adega).
- **Conversão de `user_id` / `instance`**: A API converte a `apikey` (UUID `user_id`) ou o nome da instância (`vendas-f534e36d-17`) no `empresa_id` numérico correspondente através das tabelas `public.empresa` e `public.conexoes`.

---

## 5. Prompts Customizados por Empresa (`prompt_customizado`)

- **Coluna no Supabase**: `public.empresa.prompt_customizado text`
- **Injeção Dinâmica**: O conteúdo gravado neste campo é injetado no topo do prompt do sistema sob a seção `REGRAS ESPECIFICAS DA LOJA (PROMPT CUSTOMIZADO)`.
- **Exemplo de Regra (Cantinho do Frango Assado - ID 43)**:
  > *"Se o cliente pedir 'um frango' ou 'frango', assuma Frango Inteiro (ID 1113). Meio frango só quando o cliente especificar 'meio frango'. Acompanha 1 cortesia grátis para Frango Inteiro e 2 para Meio Frango."*

---

## 6. Geolocalização de Alta Precisão (Google Places API)

- **Procedure SQL**: `public.calcular_entrega_completa(p_empresa_id text, p_endereco text)`
- **Passo 1 (Google Places Text Search API)**:
  - Pesquisa o nome do local, condomínio, residencial ou estabelecimento dentro de Luís Eduardo Magalhães - BA (ex: *"Residencial Gabrigil"* -> `Residencial Gabrigil, R. Vinte e Quatro de Julho, 205 - Jardim Paraíso III`).
- **Passo 2 (Google Distance Matrix API)**:
  - Calcula a distância real de rodagem em km e tempo de percurso a partir das coordenadas exatas da loja.
- **Recálculo OBRIGATÓRIO de Frete**:
  - Mesmo ao confirmar um endereço antigo salvo, o agente executa obrigatoriamente `calcular_entrega_completa`. O frete NUNCA é reaproveitado de um pedido passado, sendo calculado sempre com base no `valor_por_km` atualizado da loja.

---

## 7. Diretrizes de Atendimento Humanizado (1 a 3 Linhas)

1. **Tamanho das Mensagens**: Respostas curtas de 1 a 3 linhas por balão. Foco em objetividade, clareza e conversa natural.
2. **Cliente Novo vs. Cliente Recorrente**:
   - **Novo**: Recepção curta + link do cardápio digital (`https://app.proia.com.br/loja/{slug}`).
   - **Recorrente**: Chama pelo **Nome**, responde direto ao pedido e **não reenvia cardápio** a menos que solicitado.
3. **Endereço Salvo**: Confirmação direta do endereço cadastrado (*"Vai ser para entregar na Rua X, 123 (Bairro Y)?"*).
4. **Horário de Entrega vs. Retirada**:
   - **Entrega**: Não pergunta horário (evita acúmulo no pico da cozinha).
   - **Retirada**: Pergunta horário da retirada (*"Qual o horário da retirada?"*).
5. **Dedução de Produtos**: "Frango" -> Frango Inteiro ID 1113. "Meio Frango" -> especificado.
6. **Upsell Sucinto**: Uma pergunta rápida condizente (*"Deseja adicionar alguma bebida ou acompanhamento?"*).
7. **Pré-Fechamento**: *"Posso concluir o pedido ou deseja adicionar alguma observação?"*
8. **Identificação no Pedido Final**: Exibe obrigatoriamente o nome no encerramento (`Seu Pedido #249 em nome de *Guto* foi concluído com sucesso! 🎉`).

---

## 8. Regras Específicas de Checkout e PIX

- **Fluxo PIX**:
  - Quando o cliente escolhe PIX, o agente **NÃO envia a chave de imediato**.
  - Pergunta primeiro: *"Você prefere pagar no PIX agora pelo WhatsApp ou no PIX na entrega/retirada?"*
  - Se for na entrega/retirada: Chame `criar_pedido_completo` imediatamente.
  - Apenas se disser que quer pagar agora: Apresente o Resumo e a Chave PIX (`CHAVE_PIX`). Apenas conclua após a validação da foto do comprovante via visão computacional.
- **Dinheiro com Troco**: Pergunta "Troco para quanto?" e grava `p_troco_para`.
- **Cartão (Crédito/Débito)**: Conclui o pedido imediatamente.
