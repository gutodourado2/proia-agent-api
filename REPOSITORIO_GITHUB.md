# 📌 Repositório Oficial — Agente Delivery proIA (proia-agent-api)

Este é o projeto oficial da **API do Agente de Delivery para Restaurantes** (proIA Delivery). Toda alteração e atualização neste projeto deve ser sincronizada com o repositório Git local abaixo:

---

### 📂 Caminho do Repositório Git Local:
```
/Users/gutemberguedourado/Documents/GitHub/proia-agent-api
```

### 🔗 Repositório no GitHub:
- **Branch Principal**: `main`
- **Deploy Automático no Coolify**: Configurado a partir de `git push origin main` para `https://api.proia.com.br` (Produção) e `https://staging-api.proia.com.br` (Staging).

---

### 🛠️ Estrutura de Perfis de IA (Hot-Swap):
- `profiles/gemini_profile.py`: Prompt e configurações do **Gemini 3.6 Flash**.
- `profiles/deepseek_profile.py`: Prompt e configurações do **DeepSeek V4 Flash**.
- Seleção do perfil ativo via variável de ambiente `.env`: `ACTIVE_AGENT_PROFILE="deepseek"` ou `"gemini"`.
