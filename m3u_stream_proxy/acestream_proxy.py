import re
import time
import logging
from typing import Dict, List, Optional, Tuple, AsyncGenerator
from urllib.parse import urljoin, quote, unquote
import asyncio
import httpx
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

logger = logging.getLogger("m3u_stream_proxy.acestream")


class AceStreamProxy:
    def __init__(self, get_settings_or_url_func):
        self.get_settings_or_url = get_settings_or_url_func

    def _clean_url(self, url: str) -> str:
        if not url:
            return ""
        url = url.strip().rstrip("/")
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"http://{url}"
        return url

    def get_server_list(self) -> List[Dict]:
        val = self.get_settings_or_url()
        if isinstance(val, dict):
            servers = val.get("acestream_servers")
            if servers and isinstance(servers, list):
                valid = [s for s in servers if s.get("url")]
                if valid:
                    return valid
            url = val.get("acestream_engine_url", "http://host.docker.internal:6878")
            return [{"id": "direct", "name": "⚡ Sin VPN", "url": self._clean_url(url), "is_default": True}]
        return [{"id": "direct", "name": "⚡ Servidor AceStream", "url": self._clean_url(str(val)), "is_default": True}]

    def get_server_info(self, server_id: Optional[str] = None) -> Tuple[str, str, str]:
        """Devuelve (url_limpia, server_id, server_name)"""
        servers = self.get_server_list()
        val = self.get_settings_or_url()
        active_id = val.get("active_acestream_server_id") if isinstance(val, dict) else None

        # 1. Si se solicita un server_id específico
        if server_id:
            for s in servers:
                if s.get("id") == server_id:
                    return self._clean_url(s["url"]), s["id"], s.get("name", s["id"])

        # 2. Si hay un active_id configurado
        if active_id:
            for s in servers:
                if s.get("id") == active_id:
                    return self._clean_url(s["url"]), s["id"], s.get("name", s["id"])

        # 3. Servidor marcado como por defecto
        for s in servers:
            if s.get("is_default"):
                return self._clean_url(s["url"]), s["id"], s.get("name", s["id"])

        if servers:
            return self._clean_url(servers[0]["url"]), servers[0]["id"], servers[0].get("name", servers[0]["id"])

        return "http://host.docker.internal:6878", "direct", "Default"

    @property
    def engine_url(self) -> str:
        url, _, _ = self.get_server_info()
        return url

    async def check_single_server(self, base_url: str) -> Dict:
        """Comprueba el estado de un servidor AceStream midiendo su latencia en ms."""
        clean = self._clean_url(base_url)
        test_urls = [
            f"{clean}/ace/status",
            f"{clean}/webui/api/service?method=get_version",
            f"{clean}/app/monitor",
            clean
        ]
        start_t = time.time()
        async with httpx.AsyncClient(timeout=2.0) as client:
            for u in test_urls:
                try:
                    r = await client.get(u)
                    if r.status_code < 500:
                        lat = round((time.time() - start_t) * 1000)
                        return {
                            "online": True,
                            "engine_url": clean,
                            "latency_ms": lat,
                            "status_code": r.status_code,
                            "message": f"Conectado ({lat}ms)"
                        }
                except (httpx.ConnectTimeout, httpx.ConnectError):
                    # El host está inaccesible o caído, no tiene sentido reintentar otras rutas
                    break
                except Exception:
                    continue

        return {
            "online": False,
            "engine_url": clean,
            "latency_ms": None,
            "status_code": None,
            "message": f"No responde en {clean}"
        }

    async def check_engine_health(self, server_id: Optional[str] = None) -> Dict:
        """Comprueba la salud del servidor activo o especificado."""
        url, sid, sname = self.get_server_info(server_id)
        res = await self.check_single_server(url)
        res["server_id"] = sid
        res["server_name"] = sname
        return res

    async def check_all_servers(self) -> List[Dict]:
        """Comprueba concurrentemente todos los servidores configurados."""
        servers = self.get_server_list()
        val = self.get_settings_or_url()
        active_id = val.get("active_acestream_server_id") if isinstance(val, dict) else "direct"

        async def check_one(s):
            h = await self.check_single_server(s["url"])
            return {
                "id": s["id"],
                "name": s.get("name", s["id"]),
                "url": self._clean_url(s["url"]),
                "is_default": s.get("is_default", False),
                "is_active": s["id"] == active_id,
                "online": h["online"],
                "latency_ms": h.get("latency_ms"),
                "message": h["message"]
            }

        tasks = [check_one(s) for s in servers]
        return await asyncio.gather(*tasks)

    async def get_hls_manifest(
        self,
        acestream_id: str,
        request_base_url: str,
        server_id: Optional[str] = None
    ) -> Response:
        """
        Obtiene el manifiesto HLS desde el motor AceStream y reescribe
        los enlaces de fragmentos para servirlos a través del proxy con soporte CORS.
        Soporta failover automático entre múltiples servidores si está habilitado.
        """
        servers = self.get_server_list()
        val = self.get_settings_or_url()
        auto_failover = val.get("auto_failover_acestream", True) if isinstance(val, dict) else True

        primary_url, primary_id, primary_name = self.get_server_info(server_id)

        # Construir lista de servidores a probar: primero el elegido, luego el resto
        candidates: List[Tuple[str, str, str]] = [(primary_url, primary_id, primary_name)]
        if auto_failover and len(servers) > 1:
            for s in servers:
                s_url = self._clean_url(s["url"])
                s_id = s["id"]
                s_name = s.get("name", s_id)
                if s_id != primary_id and (s_url, s_id, s_name) not in candidates:
                    candidates.append((s_url, s_id, s_name))

        last_error = None
        for base_url, s_id, s_name in candidates:
            manifest_url = f"{base_url}/ace/manifest.m3u8?id={acestream_id}"
            headers = {
                "User-Agent": "M3U-Stream-Hub-Proxy/1.0",
                "Accept": "*/*"
            }

            try:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    r = await client.get(manifest_url, headers=headers)
                    if r.status_code != 200:
                        last_error = f"{s_name} devolvió código HTTP {r.status_code}"
                        logger.warning(f"AceStream engine {s_name} devolvió {r.status_code} para {manifest_url}")
                        continue

                    original_manifest = r.text

                # Si respondió correctamente, reescribir manifiesto
                logger.info(f"Stream AceStream {acestream_id} obtenido con éxito desde '{s_name}' ({base_url})")

                lines = original_manifest.splitlines()
                rewritten_lines = []

                for line in lines:
                    s_line = line.strip()
                    if not s_line:
                        rewritten_lines.append(line)
                        continue

                    if s_line.startswith("#"):
                        if 'URI="' in s_line:
                            def replace_uri(m):
                                target_uri = m.group(1)
                                full_target = urljoin(manifest_url, target_uri)
                                proxy_uri = f"{request_base_url}/ace/segment?url={quote(full_target, safe='')}&server_id={s_id}"
                                return f'URI="{proxy_uri}"'
                            s_line = re.sub(r'URI="([^"]+)"', replace_uri, s_line)
                        rewritten_lines.append(s_line)
                    else:
                        full_segment_url = urljoin(manifest_url, s_line)
                        proxy_segment_url = f"{request_base_url}/ace/segment?url={quote(full_segment_url, safe='')}&server_id={s_id}"
                        rewritten_lines.append(proxy_segment_url)

                content = "\n".join(rewritten_lines)
                return Response(
                    content=content,
                    media_type="application/vnd.apple.mpegurl",
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "X-AceStream-Server-Used": s_name
                    }
                )

            except httpx.ConnectError:
                last_error = f"No se pudo conectar a {s_name} en {base_url}"
                logger.warning(f"Error de conexión con AceStream {s_name} ({base_url}), intentando siguiente...")
                continue
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Error comunicando con AceStream {s_name} ({base_url}): {e}")
                continue

        # Si ningún candidato respondió
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo conectar con ningún motor AceStream configurado. Último error: {last_error}"
        )

    async def stream_direct_ts(self, acestream_id: str, server_id: Optional[str] = None) -> StreamingResponse:
        """
        Proxy de flujo MPEG-TS directo para reproductores externos o navegadores
        compatibles desde /ace/getstream?id={acestream_id}.
        """
        base_url, sid, sname = self.get_server_info(server_id)
        stream_url = f"{base_url}/ace/getstream?id={acestream_id}"

        async def stream_generator() -> AsyncGenerator[bytes, None]:
            client = httpx.AsyncClient(timeout=None)
            try:
                async with client.stream("GET", stream_url, headers={"User-Agent": "M3U-Stream-Hub-Proxy/1.0"}) as response:
                    if response.status_code != 200:
                        yield b""
                        return
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        yield chunk
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"Conexión finalizada con AceStream TS stream ({sname}): {e}")
            finally:
                await client.aclose()

        return StreamingResponse(
            stream_generator(),
            media_type="video/mp2t",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-cache",
                "Connection": "close",
                "X-AceStream-Server-Used": sname
            }
        )
