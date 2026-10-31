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


def format_speed(bps: float) -> str:
    """Convierte bytes/segundo a formato legible (KB/s, MB/s)."""
    if bps <= 0:
        return "0 KB/s"
    elif bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps / (1024 * 1024):.2f} MB/s"


def format_bytes_size(b: float) -> str:
    """Convierte total de bytes a formato legible."""
    if b <= 0:
        return "0 MB"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    return f"{b / (1024 * 1024 * 1024):.2f} GB"


class AceStreamProxy:
    def __init__(self, get_settings_or_url_func):
        self.get_settings_or_url = get_settings_or_url_func
        self._stat_url_cache: Dict[str, Tuple[str, float]] = {}

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
            f"{clean}/webui/api/service?method=get_version",
            f"{clean}/ace/status",
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

    async def get_stream_stats(
        self,
        acestream_id: str,
        server_id: Optional[str] = None
    ) -> Dict:
        """
        Consulta estadísticas en tiempo real (peers, velocidad, búfer)
        directamente del motor AceStream.
        """
        clean_id = acestream_id.strip().lower()
        base_url, sid, sname = self.get_server_info(server_id)
        cache_key = f"{sid}:{clean_id}"
        now = time.time()

        stat_url = None
        if cache_key in self._stat_url_cache:
            cached_url, cached_time = self._stat_url_cache[cache_key]
            # Usar stat_url si tiene menos de 20 minutos
            if now - cached_time < 1200:
                stat_url = cached_url

        async with httpx.AsyncClient(timeout=2.5, follow_redirects=True) as client:
            # Si no tenemos stat_url, pedir el manifiesto con format=json
            if not stat_url:
                manifest_api = f"{base_url}/ace/manifest.m3u8?format=json&id={clean_id}"
                try:
                    r = await client.get(manifest_api)
                    if r.status_code == 200:
                        data = r.json()
                        resp_obj = data.get("response") or {}
                        stat_url = resp_obj.get("stat_url")
                        if stat_url:
                            self._stat_url_cache[cache_key] = (stat_url, now)
                except Exception as e:
                    logger.debug(f"No se pudo obtener stat_url para {clean_id}: {e}")

            # Si aún no tenemos stat_url, intentar getstream?format=json
            if not stat_url:
                getstream_api = f"{base_url}/ace/getstream?format=json&id={clean_id}"
                try:
                    r = await client.get(getstream_api)
                    if r.status_code == 200:
                        data = r.json()
                        resp_obj = data.get("response") or {}
                        stat_url = resp_obj.get("stat_url")
                        if stat_url:
                            self._stat_url_cache[cache_key] = (stat_url, now)
                except Exception as e:
                    logger.debug(f"No se pudo obtener stat_url vía getstream para {clean_id}: {e}")

            # Si finalmente tenemos stat_url, consultarlo
            if stat_url:
                try:
                    r = await client.get(stat_url)
                    if r.status_code == 200:
                        data = r.json()
                        stats = data.get("response") or {}
                        status = stats.get("status", "unknown")
                        peers = int(stats.get("peers", 0) or 0)
                        speed_down = float(stats.get("speed_down", 0) or 0)
                        speed_up = float(stats.get("speed_up", 0) or 0)
                        downloaded = float(stats.get("downloaded", 0) or 0)
                        progress = int(stats.get("progress", 0) or 0)

                        # Clasificación de salud de señal
                        if peers >= 15:
                            health = "excellent"
                            health_label = "🟢 Excelente"
                        elif peers >= 5:
                            health = "good"
                            health_label = "🟢 Buena"
                        elif peers >= 1:
                            health = "fair"
                            health_label = "🟡 Débil"
                        else:
                            health = "poor"
                            health_label = "🔴 Buscando fuentes"

                        # Etiqueta de estado en español
                        status_labels = {
                            "dl": "Descargando / Reproduciendo",
                            "prebuf": f"Llenando búfer ({progress}%)",
                            "check": "Comprobando fuentes",
                            "wait": "Buscando peers",
                            "starting": "Iniciando motor",
                            "loading": "Cargando stream"
                        }
                        status_label = status_labels.get(status, f"Estado: {status}")

                        return {
                            "online": True,
                            "acestream_id": clean_id,
                            "server_id": sid,
                            "server_name": sname,
                            "status": status,
                            "status_label": status_label,
                            "peers": peers,
                            "speed_down": speed_down,
                            "speed_down_formatted": format_speed(speed_down),
                            "speed_up": speed_up,
                            "speed_up_formatted": format_speed(speed_up),
                            "downloaded": downloaded,
                            "downloaded_formatted": format_bytes_size(downloaded),
                            "progress": progress,
                            "health": health,
                            "health_label": health_label
                        }
                except Exception as e:
                    logger.debug(f"Error consultando stat_url {stat_url}: {e}")
                    self._stat_url_cache.pop(cache_key, None)

        # Fallback si aún no hay conexión o no hay datos
        is_server_online = False
        try:
            srv_health = await self.check_single_server(base_url)
            is_server_online = srv_health.get("online", False)
        except Exception:
            pass

        return {
            "online": is_server_online,
            "acestream_id": clean_id,
            "server_id": sid,
            "server_name": sname,
            "status": "idle",
            "status_label": "Esperando conexión P2P" if is_server_online else "Servidor no disponible",
            "peers": 0,
            "speed_down": 0,
            "speed_down_formatted": "0 KB/s",
            "speed_up": 0,
            "speed_up_formatted": "0 KB/s",
            "downloaded": 0,
            "downloaded_formatted": "0 MB",
            "progress": 0,
            "health": "poor",
            "health_label": "🔴 Buscando peers..." if is_server_online else "🔴 Servidor offline"
        }

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
                # Se asigna timeout generoso (30s) para permitir el prebuffering P2P inicial en arranques en frío
                transport_limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
                async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0), limits=transport_limits, follow_redirects=True) as client:
                    r = await client.get(manifest_url, headers=headers)

                    # Si el motor devuelve 500, significa que la sesión HLS previa expiró o se colgó en el motor.
                    # Solicitamos una nueva sesión limpia pasando un sid único basado en timestamp.
                    if r.status_code == 500:
                        logger.info(f"Sesión HLS previa en {s_name} expirada (500). Solicitando nueva sesión limpia...")
                        fresh_url = f"{base_url}/ace/manifest.m3u8?id={acestream_id}&sid={int(time.time())}"
                        r = await client.get(fresh_url, headers=headers)

                    if r.status_code != 200:
                        last_error = f"{s_name} devolvió código HTTP {r.status_code}"
                        logger.warning(f"AceStream engine {s_name} devolvió {r.status_code} para {manifest_url}")
                        continue

                    original_manifest = r.text
                    if not original_manifest or not original_manifest.strip().startswith("#EXTM3U"):
                        last_error = f"{s_name} devolvió respuesta no válida"
                        logger.warning(f"AceStream {s_name} devolvió respuesta no M3U8 para {manifest_url}")
                        continue

                    effective_url = str(r.url)

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
                                full_target = urljoin(effective_url, target_uri)
                                proxy_uri = f"{request_base_url}/ace/segment?url={quote(full_target, safe='')}&server_id={s_id}"
                                return f'URI="{proxy_uri}"'
                            s_line = re.sub(r'URI="([^"]+)"', replace_uri, s_line)
                        rewritten_lines.append(s_line)
                    else:
                        full_segment_url = urljoin(effective_url, s_line)
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
                        "X-AceStream-Server-Used": s_id
                    }
                )

            except httpx.ConnectError:
                last_error = f"No se pudo conectar a {s_name} en {base_url}"
                logger.warning(f"Error de conexión con AceStream {s_name} ({base_url}), intentando siguiente...")
                continue
            except httpx.TimeoutException:
                last_error = f"Tiempo de espera agotado en {s_name} (sin respuesta o prebuffering lento)"
                logger.warning(f"Timeout comunicando con AceStream {s_name} ({base_url}), intentando siguiente...")
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
        compatibles desde /ace/getstream?id={acestream_id}, con failover automático.
        """
        servers = self.get_server_list()
        val = self.get_settings_or_url()
        auto_failover = val.get("auto_failover_acestream", True) if isinstance(val, dict) else True

        primary_url, primary_id, primary_name = self.get_server_info(server_id)
        candidates: List[Tuple[str, str, str]] = [(primary_url, primary_id, primary_name)]
        if auto_failover and len(servers) > 1:
            for s in servers:
                s_url = self._clean_url(s["url"])
                s_id = s["id"]
                s_name = s.get("name", s_id)
                if s_id != primary_id and (s_url, s_id, s_name) not in candidates:
                    candidates.append((s_url, s_id, s_name))

        async def stream_generator() -> AsyncGenerator[bytes, None]:
            client = httpx.AsyncClient(timeout=None)
            connected = False
            try:
                for base_url, sid, sname in candidates:
                    stream_url = f"{base_url}/ace/getstream?id={acestream_id}"
                    try:
                        async with client.stream("GET", stream_url, headers={"User-Agent": "M3U-Stream-Hub-Proxy/1.0"}) as response:
                            if response.status_code == 200:
                                connected = True
                                async for chunk in response.aiter_bytes(chunk_size=65536):
                                    yield chunk
                                break
                            else:
                                logger.warning(f"AceStream TS {sname} devolvió {response.status_code}, probando siguiente...")
                    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                        logger.warning(f"Error de conexión TS con {sname} ({base_url}): {e}")
                        continue
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"Conexión finalizada con AceStream TS stream: {e}")
            finally:
                await client.aclose()

        return StreamingResponse(
            stream_generator(),
            media_type="video/mp2t",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-cache",
                "Connection": "close",
                "X-AceStream-Server-Used": primary_id
            }
        )
