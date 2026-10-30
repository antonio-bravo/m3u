import re
import logging
from typing import Dict, Optional, AsyncGenerator
from urllib.parse import urljoin, quote, unquote
import asyncio
import httpx
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

logger = logging.getLogger("m3u_stream_proxy.acestream")


class AceStreamProxy:
    def __init__(self, get_engine_url_func):
        self.get_engine_url = get_engine_url_func

    @property
    def engine_url(self) -> str:
        url = self.get_engine_url().strip().rstrip("/")
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"http://{url}"
        return url

    async def check_engine_health(self) -> Dict:
        """Comprueba si el motor AceStream está activo y accesible."""
        base_url = self.engine_url
        test_urls = [
            f"{base_url}/ace/status",
            f"{base_url}/webui/api/service?method=get_version",
            f"{base_url}/app/monitor",
            base_url
        ]

        async with httpx.AsyncClient(timeout=4.0) as client:
            for u in test_urls:
                try:
                    r = await client.get(u)
                    if r.status_code < 500:
                        return {
                            "online": True,
                            "engine_url": base_url,
                            "status_code": r.status_code,
                            "message": f"Conectado correctamente a AceStream en {base_url}"
                        }
                except Exception:
                    continue

        return {
            "online": False,
            "engine_url": base_url,
            "message": f"No se pudo conectar al motor AceStream en {base_url}. Asegúrate de que el contenedor o aplicación AceStream esté iniciado en el puerto 6878."
        }

    async def get_hls_manifest(self, acestream_id: str, request_base_url: str) -> Response:
        """
        Obtiene el manifiesto HLS desde el motor AceStream y reescribe
        los enlaces de fragmentos para servirlos a través del proxy con soporte CORS.
        """
        base_url = self.engine_url
        manifest_url = f"{base_url}/ace/manifest.m3u8?id={acestream_id}"

        headers = {
            "User-Agent": "M3U-Stream-Hub-Proxy/1.0",
            "Accept": "*/*"
        }

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                r = await client.get(manifest_url, headers=headers)
                if r.status_code != 200:
                    logger.warning(f"AceStream engine devolvió status {r.status_code} para {manifest_url}")
                    # Si el motor no soporta manifest.m3u8, devolvemos un error explicativo
                    raise HTTPException(
                        status_code=r.status_code,
                        detail=f"El motor AceStream no pudo generar el manifiesto HLS ({r.status_code}). Verifica si el ID de AceStream es válido o si el motor tiene peers activos."
                    )

                original_manifest = r.text

            # Reescribir el manifiesto para que todas las URLs pasen por nuestro proxy
            # y no tengan bloqueos CORS en el navegador
            lines = original_manifest.splitlines()
            rewritten_lines = []

            for line in lines:
                s_line = line.strip()
                if not s_line:
                    rewritten_lines.append(line)
                    continue

                if s_line.startswith("#"):
                    # Reescribir si contiene URI="..."
                    if 'URI="' in s_line:
                        def replace_uri(m):
                            target_uri = m.group(1)
                            full_target = urljoin(manifest_url, target_uri)
                            proxy_uri = f"{request_base_url}/ace/segment?url={quote(full_target, safe='')}"
                            return f'URI="{proxy_uri}"'
                        s_line = re.sub(r'URI="([^"]+)"', replace_uri, s_line)
                    rewritten_lines.append(s_line)
                else:
                    # Es una URL de segmento de video (.ts o .aac o .m4s)
                    full_segment_url = urljoin(manifest_url, s_line)
                    proxy_segment_url = f"{request_base_url}/ace/segment?url={quote(full_segment_url, safe='')}"
                    rewritten_lines.append(proxy_segment_url)

            content = "\n".join(rewritten_lines)
            return Response(
                content=content,
                media_type="application/vnd.apple.mpegurl",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                    "Cache-Control": "no-cache, no-store, must-revalidate"
                }
            )

        except httpx.ConnectError:
            raise HTTPException(
                status_code=502,
                detail=f"No se puede conectar al motor AceStream en '{base_url}'. Comprueba la URL en Ajustes o si AceStream está ejecutándose."
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error obteniendo manifiesto AceStream {acestream_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Error comunicando con AceStream: {str(e)}")

    async def stream_direct_ts(self, acestream_id: str) -> StreamingResponse:
        """
        Proxy de flujo MPEG-TS directo para reproductores externos o navegadores
        compatibles desde /ace/getstream?id={acestream_id}.
        """
        base_url = self.engine_url
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
                logger.warning(f"Conexión finalizada con AceStream TS stream: {e}")
            finally:
                await client.aclose()

        return StreamingResponse(
            stream_generator(),
            media_type="video/mp2t",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-cache",
                "Connection": "close"
            }
        )
