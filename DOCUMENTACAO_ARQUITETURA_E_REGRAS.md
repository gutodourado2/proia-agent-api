# 📖 DOCUMENTAÇÃO COMPLETA DE ARQUITETURA E REGRAS DO ECOSSISTEMA PROIA

> **Data de Atualização**: 06/08/2026  
> **Repositórios**: `proia-agent-api` (Delivery) e `proia-agent-vendas` (Vendas B2B)  
> **Tecnologias**: Python FastAPI, OpenAI SDK, OpenRouter, Supabase PostgreSQL, Evolution API, Google Places & Distance Matrix API, Nixpacks / Coolify.

---

## 📑 SUMÁRIO
1. [Visão Geral e Arquitetura de Deploy no Coolify](#1-visão-geral-e-arquitetura-de-deploy-no-coolify)
2. [Modelos de IA, Modo Tester Staging e Fallbacks](#2-modelos-de-ia-modo-tester-staging-e-fallbacks)
3. [Fusos Horários do Brasil (ZoneInfo) & Trigger Automático no Banco](#3-fusos-horários-do-brasil-zoneinfo--trigger-automático-no-banco)
4. [Solução Anti-Reimpressão Duplicada de Comandas](#4-solução-anti-reimpressão-duplicada-de-comandas)
5. [Inteligência de Janela de Tempo de 20 Minutos e Intenção de Pedidos](#5-inteligência-de-janela-de-tempo-de-20-minutos-e-intenção-de-pedidos)
6. [Cálculo da Diferença PIX em Adições](#6-cálculo-da-diferença-pix-em-adições)
7. [Leitura de Comprovantes PIX em PDF (documentMessage)](#7-leitura-de-comprovantes-pix-em-pdf-documentmessage)
8. [Formatação Visível de Adicionais nas Comandas](#8-formatação-visível-de-adicionais-nas-comandas)
9. [Duplo Webhook de Repasse para o n8n](#9-duplo-webhook-de-repasse-para-o-n8n)
10. [Agente Autônomo de Vendas proIA Delivery (agente_venda_proia)](#10-agente-autônomo-de-vendas-proia-delivery-agente_venda_proia)

---

## 1. Visão Geral e Arquitetura de Deploy no Coolify

- **Serviço 1 - Agente Delivery Produção**:
  - Repositório Git: `proia-agent-api` (Branch `main`)
  - Porta: `8000` | Subdomínio: `https://api.proia.com.br`
- **Serviço 2 - Agente Delivery Staging (Testes)**:
  - Repositório Git: `proia-agent-api` (Branch `main`)
  - Porta: `8000` | Subdomínio: `https://staging-api.proia.com.br`
- **Serviço 3 - Agente de Vendas proIA Delivery**:
  - Repositório Git: `proia-agent-vendas` (Branch `main`)
  - Porta: `8001` | Subdomínio: `https://vendas-api.proia.com.br`
- **Infraestrutura**: Evolution API (`https://evo.proia.com.br`), Supabase (`https://askqkwvpjhotytmxcfqx.supabase.co`).

---

## 2. Modelos de IA, Modo Tester Staging e Fallbacks

- **Modelo Oficial de Produção**: `deepseek/deepseek-v4-flash-0731` via OpenRouter SDK.
- **Modo Tester Staging (Isolamento Total por Telefone)**:
  - **Telefones de Teste Cadastrados**: `5577998238209` e `557791662913`.
  - **Loja Staging no Supabase**: `empresa_id: 99` (*Cantinho do Frango Assado - Testes Staging*).
  - **Modelo em Teste/Calibração**: `deepseek/deepseek-v4-flash-0731`.
  - **Prompt Exclusivo**: `TESTER_SYSTEM_PROMPT_BODY` (calibrado para a arquitetura MoE do DeepSeek sem alterar 1 vírgula do prompt oficial do Gemini).
  - **Garantias de Segurança**: Pedidos dos testes são salvos **apenas na loja 99** e **NUNCA são impressos na comanda da cozinha oficial (loja 43)**!
- **Cascata de Fallbacks**: `deepseek/deepseek-v4-flash-0731` (Tier 1) ➔ `google/gemini-3.7-flash` (Tier 2) ➔ `google/gemini-3.1-flash-lite` (Tier 3).

---

## 3. Fusos Horários do Brasil (ZoneInfo) & Trigger Automático no Banco

- **Trigger PostgreSQL (`trg_auto_fuso_horario_empresa`)**:
  Sempre que uma loja for cadastrada ou atualizar a coluna `estado` em `public.empresa`, a função `fn_auto_mapear_fuso_horario_empresa` calcula e salva automaticamente a coluna `fuso_horario`:
  - `AC` ➔ `America/Rio_Branco` (UTC-5)
  - `AM`, `RO`, `RR` ➔ `America/Manaus` (UTC-4)
  - `MT`, `MS` ➔ `America/Cuiaba` (UTC-4)
  - `BA`, `SP`, `RJ`, `MG`, `DF`, `CE`, `PE`, `GO`, `PR`, `RS`, etc. ➔ `America/Sao_Paulo` (UTC-3 - Horário de Brasília)
- **Data/Hora Local Exata no Prompt**:
  A API calcula a data/hora local da loja via `ZoneInfo` e injeta no topo do System Prompt:
  `Data/Hora Atual Local da Loja: Quarta-feira, 05/08/2026 às 20:28:15 (Fuso: America/Sao_Paulo)`
- **Validação de Expediente**: O agente identifica se a loja está fechada e oferece o agendamento do pedido para o próximo dia útil.

---

## 4. Solução Anti-Reimpressão Duplicada de Comandas

- **Constraint `UNIQUE (pedido_id)` na `public.fila_impressao`**:
  O banco proíbe fisicamente a criação de mais de 1 linha para o mesmo pedido na fila de impressão.
- **Eliminação de INSERT Redundante**:
  A função `criar_pedido_completo` não insere mais manualmente na `fila_impressao`, deixando essa responsabilidade unicamente para o Trigger oficial `trg_pedido_para_fila`.
- **Checagem de Alteração Real (`v_houve_alteracao_real`)**:
  A RPC `atualizar_pedido_completo` verifica se produtos, endereço ou taxa de entrega mudaram de fato antes de alterar o status da impressora para `pendente`.

---

## 5. Inteligência de Janela de Tempo de 20 Minutos e Intenção de Pedidos

- **Janela de 20 Minutos**:
  Se o cliente fizer um novo pedido dentro de 20 minutos após o pedido anterior (e for do mesmo tipo de entrega), a RPC `criar_pedido_completo` redireciona para `atualizar_pedido_completo`.
- **Pedidos > 20 Minutos ou Status >= 3**:
  Se tiverem passado mais de 20 minutos ou o status do pedido anterior for `>= 3` (Saiu para entrega), o agente passa `p_forcar_novo: true` e o banco gera um **NOVO PEDIDO COM NOVO #ID**.
- **Retirada vs. Entrega**:
  Pedidos para Retirada no Balcão e Pedidos para Entrega a Domicílio **SEMPRE geram #IDs independentes**.

---

## 6. Cálculo da Diferença PIX em Adições

- Se o pedido já foi pago via PIX e o cliente adicionar um novo item (ex: Guaraná 1L R$ 11,00):
  - A IA mantém o pedido existente e envia a chave PIX cobrando **apenas a diferença a pagar (R$ 11,00)**.
  - Ao receber o comprovante de R$ 11,00, a visão computacional valida e grava o comprovante complementar em `p_observacoes`.

---

## 7. Leitura de Comprovantes PIX em PDF (documentMessage)

- O `vision_service.py` aceita arquivos PDF enviados no WhatsApp (`mimetype: application/pdf`).
- O base64 do PDF é enviado para o modelo de visão multimodal que extrai o valor pago, o recebedor e valida o PIX com 100% de precisão.

---

## 8. Formatação Visível de Adicionais nas Comandas

- **Vínculo em `public.produto_grupos_adicionais`**: Todos os produtos possuem os acompanhamentos e adicionais vinculados no Supabase.
- **Concatenação em `produto_nome`**: A RPC `criar_pedido_completo` salva a descrição do item já com os acompanhamentos escolhidos (ex: `Frango inteiro (Feijão tropeiro)`), garantindo que eles apareçam claramente no painel web e na impressão térmica.

---

## 9. Duplo Webhook de Repasse para o n8n

- Todos os eventos recebidos da Evolution API (mensagens, atualizações de conexão, QR Code `qrcode.updated`) são repassados em segundo plano (Background Task) para os webhooks do n8n:
  - Webhook de Conexão / Eventos: `https://n8n.proia.com.br/webhook/cf0c69c8-01ab-4a34-907a-febed00cc5db`
  - Webhook Secundário: `https://n8n.proia.com.br/webhook/42456d3e-1951-4f2b-9290-c08dee4a52d0`

---

## 10. Agente Autônomo de Vendas proIA Delivery (`agente_venda_proia`)

- **Repositório Git**: `proia-agent-vendas` (Porta `8001` no Coolify).
- **Tabelas Exclusivas no Supabase**: `public.leads_vendas` e `public.vendas_chat_histories`.
- **Qualificação em 4 Perguntas**: Tipo de Negócio, Sistema Atual, Volume de Pedidos e Maior Dor Operacional.
- **Base de Conhecimento `PROIA_KNOWLEDGE_BASE`**: Conhecimento completo sobre a plataforma, atendimento 24h, leitura PIX, impressão térmica e onboarding 100% assistido.
- **Handoff Automático (`registrar_lead_e_handoff`)**: Ao detectar pergunta sobre valores ou interesse de compra, ativa `transbordo_humano = true`, salva o resumo executivo do lead e transfere amigavelmente para o consultor humano na mesma conversa.

---

## 11. Sanitização de Endereços e Blindagem de Frete no Supabase

- **Sanitização Inteligente e Alta Disponibilidade em `public.calcular_entrega_completa`**:
  - Expressões em parênteses `\(.*?\)` (ex: pontos de referência como `(descendo a rua...)`) e termos como `ponto de referencia`, `ref:`, `proximo a`, `descendo` são automaticamente removidos antes da geocodificação.
  - Garante que ruídos e pontos de referência extensos não façam a busca cair em municípios homônimos distante (ex: Cristópolis - BA a 162 km).
  - **Arquitetura Multi-Provedor Resiliente**: Tenta prioritariamente Google Places e Geocoding. Se a API do Google falhar, retornar erro ou tiver suspensão de faturamento no Google Cloud, o Supabase aciona imediatamente o fallback autônomo via **OpenStreetMap / Nominatim** e estima a distância via **Haversine**, garantindo que o agente e a loja nunca fiquem travados sem calcular frete.
- **Trava de Recálculo Backend (`criar_pedido_completo` / `atualizar_pedido_completo`)**:
  - Se um pedido de entrega for enviado com `p_taxa_entrega <= 8` ou coordenadas/distância zeradas (`distancia_km = 0`), o banco invoca automaticamente a função `calcular_entrega_completa`.
  - O valor real do frete retornado é aplicado e as coordenadas (`latitude_entrega`, `longitude_entrega`, `distancia_km`) são salvas no pedido, impedindo que fretes zerados ou mínimos sejam gravados indevidamente.
