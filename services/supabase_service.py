import json
import logging
from typing import Dict, Any, List, Optional
import httpx
from config import settings

logger = logging.getLogger("supabase_service")

class SupabaseService:
    def __init__(self):
        self.base_url = settings.SUPABASE_URL.rstrip('/')
        self.headers = {
            "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json"
        }

    async def rpc(self, function_name: str, payload: Dict[str, Any]) -> Any:
        url = f"{self.base_url}/rest/v1/rpc/{function_name}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(url, headers=self.headers, json=payload)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                logger.error(f"Erro ao chamar RPC {function_name}: {e}")
                return {"erro": str(e), "sucesso": False}

    async def get_empresa_by_identifier(self, apikey: str = "", instance: str = "") -> Optional[Dict[str, Any]]:
        """Busca empresa diretamente pelo apikey (user_id) ou pela RPC get_empresa_by_instance"""
        if apikey and len(apikey) > 20:
            try:
                url = f"{self.base_url}/rest/v1/empresa?user_id=eq.{apikey}&limit=1"
                async with httpx.AsyncClient(timeout=10.0) as client:
                    res = await client.get(url, headers=self.headers)
                    data = res.json()
                    if data:
                        return data[0]
            except Exception as e:
                logger.warning(f"Erro ao buscar empresa direta por user_id/apikey: {e}")

        search_target = instance or apikey
        if search_target:
            try:
                empresa_data = await self.rpc("get_empresa_by_instance", {"p_instance": search_target})
                if isinstance(empresa_data, dict) and empresa_data.get("user_id"):
                    return empresa_data
            except Exception as e:
                logger.warning(f"Erro ao buscar empresa via RPC: {e}")

        try:
            url_fallback = f"{self.base_url}/rest/v1/empresa?id=eq.43&limit=1"
            async with httpx.AsyncClient(timeout=10.0) as client:
                res_fb = await client.get(url_fallback, headers=self.headers)
                fb_data = res_fb.json()
                if fb_data:
                    return fb_data[0]
        except Exception as e:
            logger.error(f"Erro no fallback da empresa: {e}")
            
        return None

    async def get_produto_imagem(self, produto_id: int) -> Optional[Dict[str, Any]]:
        """Busca imagem_url e nome do produto diretamente na tabela produtos"""
        url = f"{self.base_url}/rest/v1/produtos?id=eq.{produto_id}&select=id,produto,imagem_url,preco&limit=1"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(url, headers=self.headers)
                data = res.json()
                return data[0] if data else None
            except Exception as e:
                logger.error(f"Erro ao buscar imagem do produto {produto_id}: {e}")
                return None

    async def get_cliente_whatsapp(self, empresa_id: Any, telefone: str) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/rest/v1/clientes_whatsapp?telefone=eq.{telefone}&limit=1"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(url, headers=self.headers)
                data = res.json()
                return data[0] if data else None
            except Exception as e:
                logger.error(f"Erro ao buscar cliente_whatsapp: {e}")
                return None

    async def registrar_cliente_se_nao_existir(self, empresa_id: Any, telefone: str, nome: str) -> bool:
        url = f"{self.base_url}/rest/v1/clientes_whatsapp"
        headers = {**self.headers, "Prefer": "resolution=merge-duplicates"}
        try:
            emp_id_numeric = int(empresa_id) if str(empresa_id).isdigit() else 43
        except Exception:
            emp_id_numeric = 43

        payload = {
            "empresa_id": emp_id_numeric,
            "telefone": telefone,
            "nome": f"{nome} - WhatsApp",
            "transbordo_humano": False
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                await client.post(url, headers=headers, json=payload)
                return True
            except Exception as e:
                logger.error(f"Erro ao registrar cliente whatsapp: {e}")
                return False

    async def set_transbordo_humano(self, telefone: str, status: bool = True) -> bool:
        url = f"{self.base_url}/rest/v1/clientes_whatsapp?telefone=eq.{telefone}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.patch(url, headers=self.headers, json={"transbordo_humano": status})
                return res.status_code < 300
            except Exception as e:
                logger.error(f"Erro ao atualizar transbordo: {e}")
                return False

    # --- AGENT TOOLS INTERFACE ---

    async def buscar_produtos(self, p_empresa_id: Any, p_busca: Optional[str] = None, p_categoria: Optional[str] = None, p_apenas_disponivel: bool = True):
        payload = {
            "p_empresa_id": str(p_empresa_id),
            "p_busca": p_busca or None,
            "p_categoria": p_categoria or None,
            "p_apenas_disponivel": p_apenas_disponivel
        }
        raw = await self.rpc("buscar_produtos", payload)
        if isinstance(raw, list) and len(raw) > 0 and isinstance(raw[0], dict) and "buscar_produtos" in raw[0]:
            return raw[0]["buscar_produtos"]
        return raw

    async def listar_categorias(self, p_empresa_id: Any):
        raw = await self.rpc("listar_categorias", {"p_empresa_id": str(p_empresa_id)})
        if isinstance(raw, list) and len(raw) > 0 and isinstance(raw[0], dict) and "listar_categorias" in raw[0]:
            return raw[0]["listar_categorias"]
        return raw

    async def info_empresa(self, p_empresa_id: Any):
        emp_id_str = str(p_empresa_id) if p_empresa_id else "43"
        return await self.rpc("info_empresa", {"p_empresa_id": emp_id_str})

    async def buscar_enderecos_cliente(self, p_telefone: str):
        return await self.rpc("buscar_enderecos_cliente", {"p_telefone": str(p_telefone)})

    async def calcular_entrega_completa(self, p_empresa_id: Any, p_endereco: str):
        emp_id_str = str(p_empresa_id) if p_empresa_id else "43"
        return await self.rpc("calcular_entrega_completa", {"p_empresa_id": emp_id_str, "p_endereco": str(p_endereco)})

    async def buscar_adicionais_produto(self, p_produto_id: int):
        raw = await self.rpc("buscar_adicionais_produto", {"p_produto_id": int(p_produto_id)})
        if isinstance(raw, list) and len(raw) > 0 and isinstance(raw[0], dict) and "buscar_adicionais_produto" in raw[0]:
            return raw[0]["buscar_adicionais_produto"]
        return raw

    async def criar_pedido_completo(self, payload: Dict[str, Any]):
        return await self.rpc("criar_pedido_completo", payload)

    async def consultar_pedido(self, p_pedido_id: int):
        return await self.rpc("consultar_pedido", {"p_pedido_id": int(p_pedido_id)})

    async def registrar_transbordo(self, p_empresa_id: Any, p_telefone: str, p_nome_cliente: str, p_motivo: str, p_mensagem_contexto: str, p_instancia: str):
        emp_id_str = str(p_empresa_id) if p_empresa_id else "43"
        try:
            emp_id_numeric = int(emp_id_str) if emp_id_str.isdigit() else 43
        except Exception:
            emp_id_numeric = 43

        payload = {
            "p_empresa_id": emp_id_numeric,
            "p_telefone": str(p_telefone),
            "p_nome_cliente": str(p_nome_cliente),
            "p_motivo": str(p_motivo),
            "p_mensagem_contexto": str(p_mensagem_contexto),
            "p_instancia": str(p_instancia),
            "p_origem_transbordo": "IA_AGENT"
        }
        await self.set_transbordo_humano(p_telefone, True)
        return await self.rpc("registrar_transbordo", payload)

supabase_service = SupabaseService()
