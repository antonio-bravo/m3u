import re
import logging
from typing import Dict, Optional, Tuple
from urllib.parse import urljoin, quote, unquote
from collections import OrderedDict

import httpx
from fastapi import Request, HTTPException
from fastapi.responses import Response

logger = logging.getLogger("m3u_stream_proxy.cors_proxy")

DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


class SegmentCache:
    """Caché LRU en memoria RAM para acelerar segmentos de video y reducir latencia."""
    def __init__(self, max_items: int = 80):
        self.max_items = max_items
        self.cache: OrderedDict[str, Tuple[bytes, str]] = OrderedDict() # url -> (data, content_type)
        self.hits: int = 0
        self.misses: int = 0

    def get(self, url: str) -> Optional[Tuple[bytes, str]]:
        if url in self.cache:
            self.cache.move_to_end(url)
            self.hits += 1
            return self.cache[url]
        self.misses += 1
        return None

    def set(self, url: str, data: bytes, content_type: str):
        self.cache[url] = (data, content_type)
        self.cache.move_to_end(url)
        while len(self.cache) > self.max_items:
            self.cache.popitem(last=False)


segment_cache = SegmentCache(max_items=100)


async def proxy_m3u8_manifest(
    target_url: str,
    request_base_url: str,
    buffer_offset: float = 16.0
) -> Response:
    """
    Descarga una lista de reproducción HLS (.m3u8), reescribe los fragmentos
    y sub-listas para que pasen por el proxy y entrega cabeceras CORS permisivas.
    """
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "*/*"
    }

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(target_url, headers=headers)
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code, detail=f"Error obteniendo stream remoto: {r.status_code}")
            
            content = r.text
            final_url = str(r.url)

        lines = content.splitlines()
        rewritten = []
        has_time_offset = any("#EXT-X-START" in l for l in lines)
        is_media_playlist = any(l.startswith("#EXTINF") for l in lines)

        for line in lines:
            s_line = line.strip()
            if not s_line:
                rewritten.append(line)
                continue

            # Inyectar TIME-OFFSET para estabilidad de búfer si es una lista de medios
            if is_media_playlist and not has_time_offset and s_line.startswith("#EXT-X-VERSION"):
                rewritten.append(line)
                if buffer_offset > 0:
                    rewritten.append(f"#EXT-X-START:TIME-OFFSET=-{buffer_offset:.1f},PRECISE=YES")
                    has_time_offset = True
                continue

            if s_line.startswith("#"):
                # Reescribir URIs en etiquetas como #EXT-X-KEY o #EXT-X-MEDIA
                if 'URI="' in s_line:
                    def replace_uri(m):
                        sub_uri = m.group(1)
                        full_sub = urljoin(final_url, sub_uri)
                        if ".m3u8" in full_sub:
                            proxy_uri = f"{request_base_url}/stream/proxy.m3u8?url={quote(full_sub, safe='')}"
                        else:
                            proxy_uri = f"{request_base_url}/stream/segment?url={quote(full_sub, safe='')}"
                        return f'URI="{proxy_uri}"'
                    s_line = re.sub(r'URI="([^"]+)"', replace_uri, s_line)
                rewritten.append(s_line)
            else:
                # Línea de URL (puede ser sub-manifiesto de bitrate o segmento .ts / .m4s)
                full_item_url = urljoin(final_url, s_line)
                clean_item = full_item_url.lower().split("?")[0]

                if clean_item.endswith(".m3u8") or ".m3u8" in clean_item:
                    # Sub-manifiesto
                    proxy_line = f"{request_base_url}/stream/proxy.m3u8?url={quote(full_item_url, safe='')}"
                else:
                    # Segmento de video
                    proxy_line = f"{request_base_url}/stream/segment?url={quote(full_item_url, safe='')}"

                rewritten.append(proxy_line)

        output_manifest = "\n".join(rewritten)
        return Response(
            content=output_manifest,
            media_type="application/vnd.apple.mpegurl",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Cache-Control": "no-cache, no-store, must-revalidate"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error proxificando m3u8 ({target_url}): {e}")
        raise HTTPException(status_code=502, detail=f"No se pudo acceder al stream HLS: {str(e)}")


async def proxy_segment_request(target_url: str, request: Request) -> Response:
    """
    Descarga y sirve un fragmento de video (.ts, .m4s, .aac) con caché y CORS.
    Soporta peticiones parciales Range (206 Partial Content).
    """
    # Comprobar si está en caché RAM
    cached = segment_cache.get(target_url)
    if cached:
        data, content_type = cached
        return Response(
            content=data,
            media_type=content_type,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=86400, immutable",
                "X-Proxy-Cache": "HIT"
            }
        )

    # Descargar desde origen
    req_headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "*/*"
    }

    # Trasladar cabecera Range si el reproductor la solicita
    range_header = request.headers.get("Range")
    if range_header:
        req_headers["Range"] = range_header

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r = await client.get(target_url, headers=req_headers)
            if r.status_code not in (200, 206):
                raise HTTPException(status_code=r.status_code, detail="Error descargando fragmento")

            data = r.content
            c_type = r.headers.get("content-type", "video/mp2t")
            if "text/html" in c_type:
                c_type = "video/mp2t"

            # Guardar en caché si fue un 200 completo y no excede 10MB
            if r.status_code == 200 and len(data) <= 10 * 1024 * 1024:
                segment_cache.set(target_url, data, c_type)

            resp_headers = {
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=86400, immutable",
                "X-Proxy-Cache": "MISS"
            }
            if "content-range" in r.headers:
                resp_headers["Content-Range"] = r.headers["content-range"]
            if "accept-ranges" in r.headers:
                resp_headers["Accept-Ranges"] = r.headers["accept-ranges"]

            return Response(
                content=data,
                status_code=r.status_code,
                media_type=c_type,
                headers=resp_headers
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en proxy de segmento ({target_url}): {e}")
        raise HTTPException(status_code=502, detail=f"Error obteniendo fragmento: {str(e)}")
