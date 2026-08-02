# 🚀 DOCUMENTAÇÃO ARQUITETURAL: AGENTE GERENTE & CANAL DE COMANDO DA LOJA

Documento de especificações e plano de implementação para o **Canal de Comando Interno e Copiloto Operacional da Loja**.

---

## 🎯 1. Visão Geral do Sistema

O **Agente Gerente (Copiloto Operacional)** é um canal seguro e inteligente onde o dono, gerente ou atendente da loja conversa com a IA via **texto ou áudio** no WhatsApp ou Painel Web para:

1. **Recalcular e Corrigir Pedidos em Tempo Real**:
   - Ajustar taxas de entrega com base no endereço digitado/falado.
   - Atualizar a forma de pagamento, troco ou itens do pedido.
   - **Notificação Automática ao Cliente**: O agente dispara uma mensagem cortês no WhatsApp do cliente informando o reajuste.

2. **Lançamento Rápido de Pedidos de Balcão (por Voz/Texto)**:
   - *"Crie o pedido de um Frango Inteiro com Feijão Tropeiro para retirada às 12h em nome de Maria."*
   - Cria o pedido no Supabase e aciona a impressora térmica no balcão da cozinha automaticamente.

3. **Gestão Dinâmica de Cardápio e Regras**:
   - Pausar produtos esgotados no dia.
   - Alterar horário de atendimento em dias excepcionais.

---

## 🏗️ 2. Arquitetura Técnica

```text
               ┌────────────────────────────────────────────────────────┐
               │    PROIA AGENTE CANAL DE COMANDO (MODO GERENTE)       │
               └───────────────────────────┬────────────────────────────┘
                                           │
                ┌──────────────────────────┴──────────────────────────┐
                ▼                                                     ▼
     ┌─────────────────────┐                               ┌─────────────────────┐
     │ CANAL 1: WHATSAPP   │                               │ CANAL 2: WEB CHAT   │
     │ Telefone Autorizado │                               │ Painel ProIA        │
     │ (Dono/Gerente)      │                               │ (app.proia.com.br)  │
     └──────────┬──────────┘                               └──────────┬──────────┘
                │                                                     │
                └──────────────────────────┬──────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────┐
                    │    FASTAPI BACKEND (admin_agent_service.py)   │
                    │  - Ferramentas de Gerência                   │
                    │  - Recálculo de Frete (Google Maps API)       │
                    │  - Notificação Automática ao Cliente         │
                    │  - Transcrição de Áudio Whisper (OpenAI)     │
                    └──────────────────────────────────────────────┘
```

---

## 🛠️ 3. Novas Ferramentas (Tools) a Serem Criadas

1. **`recalcular_e_notificar_pedido`**:
   - Argumentos: `p_pedido_id`, `p_novo_endereco`, `p_motivo`.
   - Recalcula a taxa de frete no Google Maps, atualiza `public.pedidos` e envia mensagem automática ao cliente via Evolution API.

2. **`alterar_status_pedido`**:
   - Argumentos: `p_pedido_id`, `p_novo_status` (ex: Cancelado, Em Preparo, Entregue).

3. **`criar_pedido_balcao_rapido`**:
   - Argumentos: `p_itens`, `p_nome_cliente`, `p_horario_retirada`.
   - Gera o pedido e insere a comanda na `public.fila_impressao`.

4. **`pausar_ativar_produto`**:
   - Argumentos: `p_produto_id`, `p_disponivel` (boolean).

---

## 📋 4. Etapas Futuras de Implementação

- **Etapa 1**: Criar tabela `public.gerentes_empresa` no Supabase com os números de WhatsApp autorizados.
- **Etapa 2**: Criar `services/admin_agent_service.py` com o prompt do Gerente e as novas ferramentas.
- **Etapa 3**: Adicionar o middleware de roteamento no `main.py` para redirecionar mensagens de números gerentes para o `AdminAgentService`.
- **Etapa 4**: Integrar transcrição de áudio via Whisper OpenAI para comandos gravados por voz.

---
*Documento criado em: 2026-08-02*
