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
        """Busca empresa por user_id, por conexoes (instance) ou retorna a primeira ativa"""
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Tentar por user_id = apikey
            if apikey:
                try:
                    url = f"{self.base_url}/rest/v1/empresa?user_id=eq.{apikey}&limit=1"
                    res = await client.get(url, headers=self.headers)
                    data = res.json()
                    if data:
                        return data[0]
                except Exception as e:
                    logger.warning(f"Erro ao buscar por apikey: {e}")

            # 2. Tentar por conexoes (instance_name = instance)
            if instance:
                try:
                    url = f"{self.base_url}/rest/v1/conexoes?nome_contato=eq.{instance}&limit=1"
                    res = await client.get(url, headers=self.headers)
                    conexoes_data = res.json()
                    if conexoes_data and conexoes_data[0].get("emp_id"):
                        emp_id = conexoes_data[0].get("emp_id")
                        url_emp = f"{self.base_url}/rest/v1/empresa?id=eq.{emp_id}&limit=1"
                        res_emp = await client.get(url_emp, headers=self.headers)
                        emp_list = res_emp.json()
                        if emp_list:
                            return emp_list[0]
                except Exception as e:
                    logger.warning(f"Erro ao buscar por conexao instance: {e}")

            # 3. Fallback: buscar a primeira empresa cadastrada no sistema
            try:
                url_fallback = f"{self.base_url}/rest/v1/empresa?limit=1"
                res_fb = await client.get(url_fallback, headers=self.headers)
                fb_data = res_fb.json()
                if fb_data:
                    return fb_data[0]
            except Exception as e:
                logger.error(f"Erro no fallback da empresa: {e}")
                
            return None

    async def get_cliente_whatsapp(self, empresa_id: int, telefone: str) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/rest/v1/clientes_whatsapp?empresa_id=eq.{empresa_id}&telefone=eq.{telefone}&limit=1"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(url, headers=self.headers)
                data = res.json()
                return data[0] if data else None
            except Exception as e:
                logger.error(f"Erro ao buscar cliente_whatsapp: {e}")
                return None

    async def registrar_cliente_se_nao_existir(self, empresa_id: int, telefone: str, nome: str) -> bool:
        url = f"{self.base_url}/rest/v1/clientes_whatsapp"
        headers = {**self.headers, "Prefer": "resolution=merge-duplicates"}
        payload = {
            "empresa_id": empresa_id,
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

    async def buscar_produtos(self, p_empresa_id: int, p_busca: Optional[str] = None, p_categoria: Optional[str] = None, p_apenas_disponivel: bool = True):
        payload = {
            "p_empresa_id": int(p_empresa_id),
            "p_busca": p_busca or None,
            "p_categoria": p_categoria or None,
            "p_apenas_disponivel": p_apenas_disponivel
        }
        return await self.rpc("buscar_produtos", payload)

    async def listar_categorias(self, p_empresa_id: int):
        return await self.rpc("listar_categorias", {"p_empresa_id": int(p_empresa_id)})

    async def info_empresa(self, p_empresa_id: int):
        return await self.rpc("info_empresa", {"p_empresa_id": int(p_empresa_id)})

    async def buscar_enderecos_cliente(self, p_telefone: str):
        return await self.rpc("buscar_enderecos_cliente", {"p_telefone": str(p_telefone)})

    async def calcular_entrega_completa(self, p_empresa_id: int, p_endereco: str):
        return await self.rpc("calcular_entrega_completa", {"p_empresa_id": int(p_empresa_id), "p_endereco": str(p_endereco)})

    async def buscar_adicionais_produto(self, p_produto_id: int):
        return await self.rpc("buscar_adicionais_produto", {"p_produto_id": int(p_produto_id)})

    async def criar_pedido_completo(self, payload: Dict[str, Any]):
        return await self.rpc("criar_pedido_completo", payload)

    async def consultar_pedido(self, p_pedido_id: int):
        return await self.rpc("consultar_pedido", {"p_pedido_id": int(p_pedido_id)})

    async def registrar_transbordo(self, p_empresa_id: int, p_telefone: str, p_nome_cliente: str, p_motivo: str, p_mensagem_contexto: str, p_instancia: str):
        payload = {
            "p_empresa_id": int(p_empresa_id),
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
