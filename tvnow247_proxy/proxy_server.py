import os
import re
import io
import time
import json
import base64
import logging
import asyncio
from typing import Optional, List, Dict, Tuple
from urllib.parse import urlparse, quote, unquote
from collections import OrderedDict

import httpx
from fastapi import FastAPI, Request, Query, HTTPException, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from PIL import Image

from deobfuscator import deobfuscate_segment, decode_token, encode_token

# Configuración de logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("tvnow247_proxy")

app = FastAPI(
    title="TVNow247 IPTV Deobfuscator & PRO Buffer Proxy",
    description="Proxy HLS de alta velocidad con desofuscación WebP/PNG, prefetching predictivo en RAM, ventana deslizante extendida y reproductor web integrado.",
    version="2.0.0"
)

# Constantes y configuración
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}
BASE_SITES = ["https://tvnow247.today", "https://tvnow247.top"]

# Configuración de búfer y caché optimizados
MAX_SEGMENT_CACHE_ITEMS = int(os.environ.get("MAX_CACHE_ITEMS", 120))
DEFAULT_BUFFER_OFFSET = float(os.environ.get("BUFFER_OFFSET_SECONDS", 16.0))
WINDOW_MAX_SEGMENTS = int(os.environ.get("WINDOW_MAX_SEGMENTS", 12))


# ---------------------------------------------------------------------------
# Gestor de Caché LRU de Segmentos con Deduplicación de Peticiones en Vuelo
# ---------------------------------------------------------------------------
class SegmentCache:
    def __init__(self, max_items: int = 60):
        self.max_items = max_items
        self.cache: OrderedDict[str, bytes] = OrderedDict()
        self.in_flight: Dict[str, asyncio.Future] = {}
        self.hits: int = 0
        self.misses: int = 0
        self.prefetches: int = 0

    def has(self, url: str) -> bool:
        return url in self.cache

    def get(self, url: str) -> Optional[bytes]:
        if url in self.cache:
            self.cache.move_to_end(url)
            self.hits += 1
            return self.cache[url]
        self.misses += 1
        return None

    def set(self, url: str, data: bytes):
        self.cache[url] = data
        self.cache.move_to_end(url)
        while len(self.cache) > self.max_items:
            self.cache.popitem(last=False)

    async def get_or_fetch(self, url: str, fetch_coro) -> bytes:
        if url in self.cache:
            self.cache.move_to_end(url)
            self.hits += 1
            return self.cache[url]

        if url in self.in_flight:
            return await self.in_flight[url]

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.in_flight[url] = fut
        self.misses += 1
        try:
            data = await fetch_coro()
            self.set(url, data)
            fut.set_result(data)
            return data
        except Exception as e:
            fut.set_exception(e)
            raise
        finally:
            self.in_flight.pop(url, None)


segment_cache = SegmentCache(max_items=MAX_SEGMENT_CACHE_ITEMS)


# ---------------------------------------------------------------------------
# Gestor de Ventana Deslizante de Canales (Sliding Window & Buffer Offset)
# ---------------------------------------------------------------------------
class SegmentItem:
    def __init__(self, seq: int, inf: str, raw_url: str):
        self.seq = seq
        self.inf = inf
        self.raw_url = raw_url
        # Extraer key canónica por path de CDN para deduplicación exacta
        parsed = urlparse(raw_url)
        self.path_key = parsed.path if parsed.path else raw_url


class ChannelWindowManager:
    """
    Mantiene un historial contiguo y deslizante de segmentos por canal ampliando
    la ventana M3U8 de 3 a 12 segmentos (~60 segundos de vídeo) e inyectando
    el tag RFC 8216 EXT-X-START:TIME-OFFSET para buffering instantáneo y cero congelaciones.

    Totalmente protegido contra:
    1. Reinicios de número de secuencia upstream (ej. de 244790 a 219).
    2. Cambios de dominio upstream transparentes sin cortes.
    3. Microcortes o caídas temporales (sirve la última ventana válida en memoria).
    """
    def __init__(self, max_segments: int = WINDOW_MAX_SEGMENTS):
        self.max_segments = max_segments
        # channel_id -> list of SegmentItem
        self.windows: Dict[str, List[SegmentItem]] = {}
        self.last_seq_assigned: Dict[str, int] = {}
        self.last_updated: Dict[str, float] = {}
        self.last_rendered_m3u8: Dict[str, str] = {}
        self.last_target_dur: Dict[str, int] = {}

    def get_cached_m3u8(self, channel_id: str) -> Optional[str]:
        """Devuelve la última lista M3U8 generada si existe y tiene menos de 120s de antigüedad."""
        if channel_id in self.last_rendered_m3u8:
            age = time.time() - self.last_updated.get(channel_id, 0)
            if age < 120.0:
                return self.last_rendered_m3u8[channel_id]
        return None

    def process_and_extend(
        self,
        channel_id: str,
        media_seq: int,
        target_dur: int,
        incoming: List[Tuple[str, str]],
        base_url: str,
        buffer_offset: Optional[float] = DEFAULT_BUFFER_OFFSET
    ) -> str:
        now = time.time()
        self.last_updated[channel_id] = now
        self.last_target_dur[channel_id] = target_dur

        # Limpiar canales inactivos hace más de 15 minutos
        stale = [cid for cid, t in self.last_updated.items() if now - t > 900]
        for cid in stale:
            self.windows.pop(cid, None)
            self.last_seq_assigned.pop(cid, None)
            self.last_updated.pop(cid, None)
            self.last_rendered_m3u8.pop(cid, None)
            self.last_target_dur.pop(cid, None)

        existing = self.windows.get(channel_id, [])

        if not existing:
            # Inicialización para un canal nuevo
            curr_seq = media_seq
            new_window = []
            for inf, url in incoming:
                new_window.append(SegmentItem(seq=curr_seq, inf=inf, raw_url=url))
                curr_seq += 1
            self.windows[channel_id] = new_window
            self.last_seq_assigned[channel_id] = curr_seq - 1
        else:
            # Ya existe ventana previa: identificamos qué segmentos entrantes son realmente nuevos
            existing_paths = {item.path_key for item in existing}
            last_seq = self.last_seq_assigned.get(channel_id, existing[-1].seq)

            new_items = []
            for inf, url in incoming:
                p_key = urlparse(url).path or url
                if p_key not in existing_paths:
                    last_seq += 1
                    new_item = SegmentItem(seq=last_seq, inf=inf, raw_url=url)
                    new_items.append(new_item)
                    existing_paths.add(p_key)

            if new_items:
                existing.extend(new_items)
                self.last_seq_assigned[channel_id] = last_seq

            # Recortar ventana a max_segments
            if len(existing) > self.max_segments:
                existing = existing[-self.max_segments:]
            self.windows[channel_id] = existing

        contiguous = self.windows[channel_id]
        if not contiguous:
            start_seq = media_seq
        else:
            start_seq = contiguous[0].seq

        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            f"#EXT-X-TARGETDURATION:{target_dur}",
            f"#EXT-X-MEDIA-SEQUENCE:{start_seq}",
            "#EXT-X-INDEPENDENT-SEGMENTS",
        ]

        # Tag de offset HLS estándar RFC 8216: inicia la reproducción X segundos por detrás del vivo
        # permitiendo acumular un gran colchón de búfer sin cortes.
        if buffer_offset is not None and buffer_offset != 0 and len(contiguous) >= 4:
            offset_val = -abs(buffer_offset)
            lines.append(f"#EXT-X-START:TIME-OFFSET={offset_val:.1f},PRECISE=NO")

        for item in contiguous:
            b64_seg = base64.urlsafe_b64encode(item.raw_url.encode("utf-8")).decode("utf-8")
            local_seg_url = f"{base_url}/segment?b64={b64_seg}"
            lines.append(item.inf)
            lines.append(local_seg_url)

        rendered = "\n".join(lines) + "\n"
        self.last_rendered_m3u8[channel_id] = rendered
        return rendered


window_manager = ChannelWindowManager(max_segments=WINDOW_MAX_SEGMENTS)


# ---------------------------------------------------------------------------
# Estado global y cliente HTTP
# ---------------------------------------------------------------------------
class ProxyState:
    active_domain: str = "chunk.tvnow247.today"
    domain_last_checked: float = 0
    channels: List[dict] = []
    channels_last_loaded: float = 0

state = ProxyState()
http_client: Optional[httpx.AsyncClient] = None


@app.on_event("startup")
async def startup_event():
    global http_client
    http_client = httpx.AsyncClient(headers=HEADERS, timeout=12.0, follow_redirects=True)
    asyncio.create_task(refresh_domain_and_channels_background())


@app.on_event("shutdown")
async def shutdown_event():
    global http_client
    if http_client:
        await http_client.aclose()


# ---------------------------------------------------------------------------
# Funciones de Descubrimiento y Sondeo de Dominio
# ---------------------------------------------------------------------------
async def probe_proxy_domain(domain: str, test_channel_ids: Optional[List[str]] = None) -> bool:
    """Valida si un dominio proxy responde con lista válida probando canales de control fiables."""
    if not domain or not http_client:
        return False
    if test_channel_ids is None:
        test_channel_ids = ["303", "758", "51", "371"]

    for test_cid in test_channel_ids:
        token = encode_token(test_cid)
        test_url = f"https://{domain}/api/proxy/playlist?token={token}"
        headers = {**HEADERS, "Referer": f"https://{domain}/"}
        try:
            r = await http_client.get(test_url, headers=headers, timeout=3.5)
            if r.status_code == 200 and "#EXTM3U" in r.text:
                return True
        except Exception:
            pass
    return False


async def discover_proxy_domain() -> str:
    """Detecta dinámicamente el dominio proxy activo de TVNow247."""
    global http_client
    if not http_client:
        return state.active_domain

    for site in BASE_SITES:
        try:
            r = await http_client.get(site, timeout=5.0)
            if r.status_code != 200:
                continue

            html_text = r.text
            active_host = urlparse(str(r.url)).netloc.replace("www.", "")

            # Probar endpoints resolve en código HTML/JS
            api_matches = re.findall(r'https?://[a-zA-Z0-9_.-]+/api/resolve-[a-zA-Z0-9_-]+', html_text)
            for api_ep in set(api_matches):
                try:
                    res_api = await http_client.get(f"{api_ep}/51", timeout=3.0)
                    if res_api.status_code == 200:
                        data = res_api.json()
                        p_url = data.get("proxyPlaylistUrl") or data.get("m3u8")
                        if p_url:
                            cand = urlparse(p_url).netloc
                            if await probe_proxy_domain(cand):
                                logger.info(f"Dominio proxy detectado vía API: {cand}")
                                return cand
                except Exception:
                    pass

            # Probar candidatos comunes
            candidates = ["chunk.tvnow247.today", "tvnow247.top", active_host]
            for cand in candidates:
                if await probe_proxy_domain(cand):
                    logger.info(f"Dominio proxy validado: {cand}")
                    return cand

        except Exception as e:
            logger.warning(f"Error al sondear sitio base {site}: {e}")

    return state.active_domain


async def load_channels() -> List[dict]:
    """Carga el catálogo completo de canales desde la web oficial."""
    global http_client
    if not http_client:
        return []

    for site in BASE_SITES:
        try:
            r = await http_client.get(site, timeout=6.0)
            if r.status_code != 200:
                continue

            # Buscar bundle principal de canales
            js_paths = re.findall(r'assets/(?:channels|main)-[a-zA-Z0-9_-]+\.js', r.text)
            for path in js_paths:
                js_url = f"{site}/{path.lstrip('/')}"
                r_js = await http_client.get(js_url, timeout=6.0)
                if r_js.status_code == 200 and "channel_name" in r_js.text:
                    raw_channels = re.findall(r'\{[^{}]*?channel_id:[^{}]*?\}', r_js.text)
                    channels = []
                    for item in raw_channels:
                        name_m = re.search(r'channel_name:\s*\"([^\"]+)\"', item)
                        id_m = re.search(r'channel_id:\s*\"([^\"]+)\"', item)
                        logo_m = re.search(r'logoUrl:\s*\"([^\"]+)\"', item)
                        group_m = re.search(r'country:\s*\"([^\"]+)\"', item)
                        if name_m and id_m:
                            channels.append({
                                "name": name_m.group(1).strip(),
                                "id": id_m.group(1).strip(),
                                "logo": logo_m.group(1).strip() if logo_m else "",
                                "group": group_m.group(1).strip() if group_m else "General"
                            })
                    if channels:
                        logger.info(f"Catálogo cargado exitosamente: {len(channels)} canales.")
                        return channels
        except Exception as e:
            logger.warning(f"No se pudieron cargar canales de {site}: {e}")

    return []


async def refresh_domain_and_channels_background():
    """Tarea periódica de refresco en segundo plano."""
    while True:
        try:
            now = time.time()
            if now - state.domain_last_checked > 1800:
                domain = await discover_proxy_domain()
                state.active_domain = domain
                state.domain_last_checked = now

            if now - state.channels_last_loaded > 21600 or not state.channels:
                chans = await load_channels()
                if chans:
                    state.channels = chans
                    state.channels_last_loaded = now
        except Exception as e:
            logger.error(f"Error en tarea de refresco: {e}")

        await asyncio.sleep(600)


# ---------------------------------------------------------------------------
# Descarga, Desofuscación y Precarga Predictiva de Segmentos
# ---------------------------------------------------------------------------
async def fetch_and_deobfuscate_segment(raw_url: str) -> bytes:
    """Descarga de la CDN con reintentos y desofusca el contenedor WebP/PNG a MPEG-TS limpio."""
    global http_client
    if not http_client:
        raise HTTPException(status_code=500, detail="HTTP client no inicializado")

    raw_bytes = None
    last_err = None
    for attempt in range(3):
        try:
            r = await http_client.get(raw_url, headers=HEADERS, timeout=8.0)
            if r.status_code == 200:
                raw_bytes = r.content
                break
            last_err = f"HTTP {r.status_code}"
            logger.warning(f"Intento {attempt + 1}/3: Falló descarga ({last_err})")
        except Exception as e:
            last_err = str(e)
            logger.warning(f"Intento {attempt + 1}/3: Error descargando segmento: {e}")
        if attempt < 2:
            await asyncio.sleep(0.3)

    if raw_bytes is None:
        raise HTTPException(status_code=502, detail=f"Error al descargar fragmento de CDN: {last_err}")

    try:
        ts_bytes = deobfuscate_segment(raw_bytes)
        return ts_bytes
    except Exception as e:
        logger.error(f"Error desofuscando segmento: {e}")
        raise HTTPException(status_code=500, detail=f"Error al desofuscar segmento: {e}")


async def _prefetch_single(url: str, sem: asyncio.Semaphore):
    if segment_cache.has(url):
        return
    async with sem:
        try:
            await segment_cache.get_or_fetch(url, lambda: fetch_and_deobfuscate_segment(url))
            segment_cache.prefetches += 1
        except Exception:
            pass


async def prefetch_segments_task(urls: List[str]):
    """Precarga en memoria RAM en segundo plano los segmentos de forma concurrente."""
    sem = asyncio.Semaphore(4)
    tasks = [_prefetch_single(u, sem) for u in urls if not segment_cache.has(u)]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def parse_playlist_body(text: str) -> Tuple[int, int, List[Tuple[str, str]]]:
    """Extrae media_seq, target_dur y los pares (extinf, url) del playlist."""
    media_seq = 0
    target_dur = 4
    m_seq = re.search(r'#EXT-X-MEDIA-SEQUENCE:(\d+)', text)
    if m_seq:
        media_seq = int(m_seq.group(1))
    m_dur = re.search(r'#EXT-X-TARGETDURATION:(\d+)', text)
    if m_dur:
        target_dur = int(m_dur.group(1))

    segments = []
    curr_inf = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("## by HACKERMAN"):
            continue
        if line.startswith("#EXTINF:"):
            curr_inf = line
        elif line and not line.startswith("#") and curr_inf:
            segments.append((curr_inf, line))
            curr_inf = None
    return media_seq, target_dur, segments


def make_segment_response(ts_bytes: bytes, request: Request) -> Response:
    """Genera respuesta HTTP optimizada para streaming MPEG-TS con soporte Range y Cache."""
    total_len = len(ts_bytes)
    range_header = request.headers.get("range")
    headers = {
        "Content-Type": "video/mp2t",
        "Accept-Ranges": "bytes",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
        "Cache-Control": "public, max-age=86400, immutable",
    }

    if request.method == "HEAD":
        headers["Content-Length"] = str(total_len)
        return Response(status_code=200, headers=headers)

    if range_header and range_header.startswith("bytes="):
        try:
            r_spec = range_header.replace("bytes=", "").strip()
            parts = r_spec.split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if len(parts) > 1 and parts[1] else total_len - 1
            if start < total_len:
                end = min(end, total_len - 1)
                chunk = ts_bytes[start:end + 1]
                headers["Content-Range"] = f"bytes {start}-{end}/{total_len}"
                headers["Content-Length"] = str(len(chunk))
                return Response(content=chunk, status_code=206, headers=headers, media_type="video/mp2t")
        except Exception:
            pass

    headers["Content-Length"] = str(total_len)
    return Response(content=ts_bytes, status_code=200, headers=headers, media_type="video/mp2t")


# ---------------------------------------------------------------------------
# Endpoints de Streaming HLS (Listas y Fragmentos)
# ---------------------------------------------------------------------------
@app.get("/live/{channel_id}.m3u8")
@app.head("/live/{channel_id}.m3u8")
async def get_live_playlist(
    channel_id: str,
    request: Request,
    offset: Optional[float] = Query(None, description="Offset de buffer en segundos (ej. 16.0)")
):
    """Obtiene, amplía la ventana deslizante y reescribe la lista M3U8 para un canal con buffer offset."""
    global http_client
    if not http_client:
        raise HTTPException(status_code=500, detail="HTTP client no inicializado")

    base_url = str(request.base_url).rstrip("/")
    token = encode_token(channel_id)
    time_offset = offset if offset is not None else DEFAULT_BUFFER_OFFSET

    # Probar dominios proxy candidatos en orden de prioridad óptima
    candidate_domains = [state.active_domain, "tvnow247.top", "chunk.tvnow247.today", "tvnow247.today"]
    seen_domains = set()
    candidate_domains = [d for d in candidate_domains if d and not (d in seen_domains or seen_domains.add(d))]

    r = None
    last_res = None
    for d in candidate_domains:
        upstream_url = f"https://{d}/api/proxy/playlist?token={token}"
        try:
            res = await http_client.get(upstream_url, headers={**HEADERS, "Referer": f"https://{d}/"}, timeout=3.5)
            last_res = res
            if "#EXTM3U" in res.text and (res.status_code == 200 or res.status_code == 503):
                r = res
                if d != state.active_domain:
                    logger.info(f"Cambiando dominio activo a {d}")
                    state.active_domain = d
                break
            if res.status_code in (403, 502) and "upstream error" in res.text.lower():
                break
        except Exception as ex:
            logger.debug(f"Error consultando upstream {d}: {ex}")

    # Fallback a resolver API si los dominios directos no respondieron
    if not r or "#EXTM3U" not in r.text:
        try:
            fallback_res = await http_client.get(f"https://tvnow247.top/api/resolve-dlstream/{channel_id}", timeout=3.5)
            if fallback_res.status_code != 200:
                fallback_res = await http_client.get(f"https://chat.cfbu247.sbs/api/resolve-dlstream/{channel_id}", timeout=3.5)
            if fallback_res.status_code == 200:
                data = fallback_res.json()
                alt_url = data.get("proxyPlaylistUrl") or data.get("m3u8")
                ref = data.get("referer") or f"https://{state.active_domain}/"
                if alt_url:
                    fb_r = await http_client.get(alt_url, headers={**HEADERS, "Referer": ref}, timeout=4.0)
                    last_res = fb_r
                    if "#EXTM3U" in fb_r.text and fb_r.status_code in (200, 503):
                        r = fb_r
        except Exception as ex:
            logger.debug(f"Error consultando API resolve fallback para canal {channel_id}: {ex}")

    # Si upstream falló momentáneamente, entregar la última ventana buena en caché para mantener el búfer vivo
    if not r or "#EXTM3U" not in r.text:
        cached_m3u8 = window_manager.get_cached_m3u8(channel_id)
        if cached_m3u8:
            logger.warning(f"Upstream momentáneamente inactivo para canal {channel_id}; entregando ventana en memoria para evitar rotura de búfer.")
            return Response(
                content=cached_m3u8,
                media_type="application/vnd.apple.mpegurl",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS"
                }
            )

        status_code = last_res.status_code if last_res else (r.status_code if r else 502)
        resp_text = (last_res.text if last_res else (r.text if r else "")).strip().lower()
        err_detail = f"Upstream no devolvió una lista válida (estado {status_code})"
        if "upstream error" in resp_text or "unavailable" in resp_text or status_code in (403, 502):
            err_detail += f": el canal {channel_id} está temporalmente inactivo o caído en los servidores de TVNow247"
        raise HTTPException(status_code=502, detail=err_detail)

    # Procesar ventana deslizante e inyectar buffer offset
    media_seq, target_dur, incoming_segs = parse_playlist_body(r.text)
    m3u8_content = window_manager.process_and_extend(
        channel_id=channel_id,
        media_seq=media_seq,
        target_dur=target_dur,
        incoming=incoming_segs,
        base_url=base_url,
        buffer_offset=time_offset
    )

    # Lanzar precarga proactiva en segundo plano de los segmentos detectados
    raw_urls = [url for _, url in incoming_segs]
    if raw_urls:
        asyncio.create_task(prefetch_segments_task(raw_urls))

    return Response(
        content=m3u8_content,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS"
        }
    )


@app.get("/proxy")
@app.head("/proxy")
async def proxy_direct_url(
    url: str = Query(..., description="URL de playlist upstream"),
    offset: Optional[float] = Query(None, description="Offset de buffer en segundos"),
    request: Request = None
):
    """
    Permite pasar directamente una URL como https://chunk.tvnow247.today/api/proxy/playlist?token=...
    Detecta automáticamente el canal, regenera el token y aplica ventana deslizante y prefetching.
    """
    base_url = str(request.base_url).rstrip("/") if request else ""
    parsed = urlparse(url)

    # Extraer token si existe
    token_match = re.search(r'token=([^&]+)', parsed.query)
    if token_match:
        token_str = token_match.group(1)
        token_info = decode_token(token_str)
        if token_info and "channelId" in token_info:
            cid = str(token_info["channelId"])
            return await get_live_playlist(cid, request, offset=offset)

    global http_client
    r = await http_client.get(url, timeout=6.0)
    if "#EXTM3U" not in r.text and r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="Error al consultar URL")

    # Reescribir segmentos, ampliar ventana deslizante y precargar
    media_seq, target_dur, incoming_segs = parse_playlist_body(r.text)
    pseudo_cid = f"custom_{abs(hash(parsed.path)) % 100000}"
    time_offset = offset if offset is not None else DEFAULT_BUFFER_OFFSET

    m3u8_content = window_manager.process_and_extend(
        channel_id=pseudo_cid,
        media_seq=media_seq,
        target_dur=target_dur,
        incoming=incoming_segs,
        base_url=base_url,
        buffer_offset=time_offset
    )

    raw_urls = [s_url for _, s_url in incoming_segs]
    if raw_urls:
        asyncio.create_task(prefetch_segments_task(raw_urls))

    return Response(
        content=m3u8_content,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS"
        }
    )


@app.get("/segment")
@app.head("/segment")
async def get_segment(
    request: Request,
    b64: Optional[str] = Query(None, description="URL en Base64 seguro para URL"),
    url: Optional[str] = Query(None, description="URL directa sin doble unquote")
):
    """
    Entrega el fragmento desofuscado en MPEG-TS limpio.
    Responde con latencia cero (<1ms) si ya fue precargado en la caché LRU de RAM.
    """
    if b64:
        try:
            raw_url = base64.urlsafe_b64decode(b64.encode("utf-8")).decode("utf-8")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Parámetro b64 inválido: {e}")
    elif url:
        raw_url = url
    else:
        raise HTTPException(status_code=400, detail="Falta el parámetro de segmento (b64 o url)")

    # Obtener de caché en RAM o descargar y desofuscar con deduplicación concurrente
    ts_bytes = await segment_cache.get_or_fetch(raw_url, lambda: fetch_and_deobfuscate_segment(raw_url))
    return make_segment_response(ts_bytes, request)


# ---------------------------------------------------------------------------
# Endpoints de API, Estado y Lista M3U
# ---------------------------------------------------------------------------
@app.get("/api/channels")
async def api_get_channels():
    """Devuelve el catálogo indexado de canales con nombre, logo, id y grupo."""
    if not state.channels:
        state.channels = await load_channels()
    return state.channels


@app.get("/api/status")
@app.get("/health")
async def health():
    """Estadísticas en tiempo real de salud, caché y búfer del proxy."""
    return {
        "status": "healthy",
        "version": "2.0.0",
        "active_domain": state.active_domain,
        "channels_count": len(state.channels),
        "cached_segments": len(segment_cache.cache),
        "cache_hits": segment_cache.hits,
        "cache_misses": segment_cache.misses,
        "prefetches": segment_cache.prefetches,
        "hit_ratio": f"{(segment_cache.hits / max(1, segment_cache.hits + segment_cache.misses) * 100):.1f}%",
        "buffer_offset_seconds": DEFAULT_BUFFER_OFFSET,
        "window_max_segments": WINDOW_MAX_SEGMENTS,
        "active_windows": len(window_manager.windows)
    }


@app.get("/channels.m3u", response_class=PlainTextResponse)
@app.get("/playlist.m3u", response_class=PlainTextResponse)
async def get_channels_m3u(request: Request):
    """Genera la lista M3U completa con todos los canales apuntando a este proxy."""
    if not state.channels:
        state.channels = await load_channels()

    base_url = str(request.base_url).rstrip("/")
    lines = ["#EXTM3U"]

    for ch in state.channels:
        cid = ch["id"]
        cname = ch["name"]
        clogo = ch["logo"]
        cgroup = ch["group"]
        stream_url = f"{base_url}/live/{cid}.m3u8"
        lines.append(f'#EXTINF:-1 tvg-id="{cid}" tvg-name="{cname}" tvg-logo="{clogo}" group-title="{cgroup}",{cname}')
        lines.append(stream_url)

    content = "\n".join(lines) + "\n"
    return Response(
        content=content,
        media_type="application/vnd.apple.mpegurl",
        headers={"Content-Disposition": 'inline; filename="tvnow247.m3u"'}
    )


# ---------------------------------------------------------------------------
# Interfaz Web PRO: Reproductor Moderno con Control de Búfer y Explorador
# ---------------------------------------------------------------------------
def render_pro_web_player(base_url: str, initial_cid: str = "303") -> str:
    """Genera una interfaz web premium con Hls.js optimizado, medidor de búfer y selector de canales."""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TVNow247 PRO Stream Player</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <style>
        :root {{
            --bg-base: #080c14;
            --bg-surface: #0f172a;
            --bg-elevated: #1e293b;
            --bg-card: rgba(30, 41, 59, 0.7);
            --border-line: rgba(51, 65, 85, 0.8);
            --border-glow: rgba(56, 189, 248, 0.4);
            --primary: #38bdf8;
            --primary-hover: #0ea5e9;
            --accent: #818cf8;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --font-main: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            --font-mono: 'JetBrains Mono', monospace;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: var(--bg-base);
            color: var(--text-main);
            font-family: var(--font-main);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            overflow-x: hidden;
        }}
        /* Header */
        header {{
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(16px);
            border-bottom: 1px solid var(--border-line);
            padding: 0.75rem 1.5rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: sticky;
            top: 0;
            z-index: 100;
        }}
        .brand {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
            text-decoration: none;
            color: var(--text-main);
        }}
        .brand-logo {{
            width: 36px;
            height: 36px;
            background: linear-gradient(135deg, #0284c7, #6366f1);
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 1.1rem;
            box-shadow: 0 4px 15px rgba(2, 132, 199, 0.4);
        }}
        .brand-title {{
            font-size: 1.15rem;
            font-weight: 700;
            letter-spacing: -0.02em;
        }}
        .brand-badge {{
            background: linear-gradient(90deg, #0284c7, #38bdf8);
            color: #030712;
            padding: 0.15rem 0.5rem;
            border-radius: 6px;
            font-size: 0.7rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        .header-center {{
            display: flex;
            align-items: center;
            gap: 1rem;
        }}
        /* Buffer status badge */
        .buffer-pill {{
            background: var(--bg-elevated);
            border: 1px solid var(--border-line);
            padding: 0.4rem 0.9rem;
            border-radius: 9999px;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.85rem;
            font-family: var(--font-mono);
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
            transition: all 0.3s ease;
        }}
        .status-dot {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
            transition: background 0.3s ease;
        }}
        .dot-green {{ background: var(--success); box-shadow: 0 0 8px var(--success); }}
        .dot-yellow {{ background: var(--warning); box-shadow: 0 0 8px var(--warning); }}
        .dot-red {{ background: var(--danger); box-shadow: 0 0 8px var(--danger); }}
        .header-actions {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }}
        .btn {{
            background: var(--bg-elevated);
            border: 1px solid var(--border-line);
            color: var(--text-main);
            padding: 0.5rem 0.9rem;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
        }}
        .btn:hover {{
            background: rgba(56, 189, 248, 0.15);
            border-color: var(--primary);
            color: var(--primary);
        }}
        .btn-primary {{
            background: var(--primary);
            color: #030712;
            border-color: var(--primary);
        }}
        .btn-primary:hover {{
            background: var(--primary-hover);
            color: #030712;
        }}
        /* Main layout */
        .main-container {{
            flex: 1;
            display: grid;
            grid-template-columns: 1fr 380px;
            gap: 1.5rem;
            padding: 1.5rem;
            max-width: 1800px;
            margin: 0 auto;
            width: 100%;
        }}
        @media (max-width: 1024px) {{
            .main-container {{
                grid-template-columns: 1fr;
            }}
        }}
        /* Video Section */
        .player-card {{
            background: var(--bg-surface);
            border: 1px solid var(--border-line);
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
            display: flex;
            flex-direction: column;
        }}
        .video-wrapper {{
            position: relative;
            aspect-ratio: 16 / 9;
            background: #000;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        video {{
            width: 100%;
            height: 100%;
            object-fit: contain;
            outline: none;
        }}
        /* Stats HUD overlay */
        .stats-hud {{
            position: absolute;
            top: 1rem;
            left: 1rem;
            background: rgba(8, 12, 20, 0.85);
            backdrop-filter: blur(8px);
            border: 1px solid var(--border-line);
            border-radius: 8px;
            padding: 0.75rem 1rem;
            font-family: var(--font-mono);
            font-size: 0.75rem;
            color: #e2e8f0;
            display: none;
            z-index: 20;
            pointer-events: none;
            line-height: 1.5;
        }}
        .stats-hud.show {{ display: block; }}
        .hud-row {{ display: flex; justify-content: space-between; gap: 1.5rem; }}
        .hud-val {{ color: var(--primary); font-weight: 600; }}
        /* Channel Info under video */
        .player-info {{
            padding: 1.25rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid var(--border-line);
            flex-wrap: wrap;
            gap: 1rem;
        }}
        .channel-details {{
            display: flex;
            align-items: center;
            gap: 1rem;
        }}
        .channel-logo-large {{
            width: 52px;
            height: 52px;
            border-radius: 12px;
            background: var(--bg-elevated);
            object-fit: contain;
            padding: 6px;
            border: 1px solid var(--border-line);
        }}
        .channel-title {{
            font-size: 1.25rem;
            font-weight: 700;
            margin-bottom: 0.2rem;
        }}
        .channel-meta {{
            display: flex;
            align-items: center;
            gap: 0.6rem;
            font-size: 0.8rem;
            color: var(--text-muted);
        }}
        .tag-pill {{
            background: rgba(56, 189, 248, 0.15);
            color: var(--primary);
            padding: 0.15rem 0.5rem;
            border-radius: 4px;
            font-weight: 600;
        }}
        /* Buffer Toolbar */
        .buffer-toolbar {{
            padding: 1rem 1.25rem;
            background: var(--bg-surface);
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            flex-wrap: wrap;
        }}
        .mode-group {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}
        .mode-label {{
            font-size: 0.85rem;
            color: var(--text-muted);
            font-weight: 500;
        }}
        .mode-btn {{
            background: var(--bg-elevated);
            border: 1px solid var(--border-line);
            color: var(--text-muted);
            padding: 0.4rem 0.8rem;
            border-radius: 6px;
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .mode-btn.active {{
            background: rgba(56, 189, 248, 0.18);
            border-color: var(--primary);
            color: var(--primary);
        }}
        .audio-boost-btn {{
            background: var(--bg-elevated);
            border: 1px solid var(--border-line);
            color: var(--text-main);
            padding: 0.4rem 0.8rem;
            border-radius: 6px;
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
        }}
        /* Sidebar Channels */
        .sidebar-card {{
            background: var(--bg-surface);
            border: 1px solid var(--border-line);
            border-radius: 16px;
            display: flex;
            flex-direction: column;
            height: calc(100vh - 120px);
            position: sticky;
            top: 5rem;
            overflow: hidden;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
        }}
        .sidebar-header {{
            padding: 1rem;
            border-bottom: 1px solid var(--border-line);
        }}
        .search-box {{
            position: relative;
            margin-bottom: 0.75rem;
        }}
        .search-input {{
            width: 100%;
            background: var(--bg-elevated);
            border: 1px solid var(--border-line);
            padding: 0.6rem 1rem 0.6rem 2.2rem;
            border-radius: 8px;
            color: var(--text-main);
            font-size: 0.85rem;
            outline: none;
            transition: border-color 0.2s;
        }}
        .search-input:focus {{
            border-color: var(--primary);
        }}
        .search-icon {{
            position: absolute;
            left: 0.75rem;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted);
            font-size: 0.9rem;
        }}
        .filter-tabs {{
            display: flex;
            gap: 0.4rem;
            overflow-x: auto;
            padding-bottom: 0.3rem;
            scrollbar-width: none;
        }}
        .filter-tab {{
            background: var(--bg-elevated);
            border: 1px solid var(--border-line);
            color: var(--text-muted);
            padding: 0.25rem 0.65rem;
            border-radius: 6px;
            font-size: 0.75rem;
            cursor: pointer;
            white-space: nowrap;
            font-weight: 500;
        }}
        .filter-tab.active {{
            background: var(--primary);
            color: #030712;
            border-color: var(--primary);
            font-weight: 700;
        }}
        .channel-list {{
            flex: 1;
            overflow-y: auto;
            padding: 0.5rem;
        }}
        .channel-item {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.6rem 0.75rem;
            border-radius: 8px;
            cursor: pointer;
            transition: background 0.15s, border-color 0.15s;
            margin-bottom: 0.3rem;
            border: 1px solid transparent;
        }}
        .channel-item:hover {{
            background: rgba(30, 41, 59, 0.7);
            border-color: var(--border-line);
        }}
        .channel-item.active {{
            background: rgba(56, 189, 248, 0.12);
            border-color: var(--primary);
        }}
        .channel-item-logo {{
            width: 34px;
            height: 34px;
            border-radius: 6px;
            background: var(--bg-elevated);
            object-fit: contain;
            padding: 4px;
            flex-shrink: 0;
        }}
        .channel-item-name {{
            font-size: 0.85rem;
            font-weight: 600;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            flex: 1;
        }}
        .channel-item-group {{
            font-size: 0.7rem;
            color: var(--text-muted);
            background: var(--bg-elevated);
            padding: 0.1rem 0.4rem;
            border-radius: 4px;
        }}
        /* Modal */
        .modal-backdrop {{
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.7);
            backdrop-filter: blur(8px);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 200;
        }}
        .modal-backdrop.show {{ display: flex; }}
        .modal-content {{
            background: var(--bg-surface);
            border: 1px solid var(--border-line);
            border-radius: 14px;
            width: 90%;
            max-width: 580px;
            padding: 1.5rem;
            box-shadow: 0 20px 40px rgba(0,0,0,0.6);
        }}
        .modal-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 1.25rem;
        }}
        .modal-title {{ font-size: 1.2rem; font-weight: 700; color: var(--primary); }}
        .close-btn {{ background: none; border: none; color: var(--text-muted); font-size: 1.4rem; cursor: pointer; }}
        .form-group {{ margin-bottom: 1rem; }}
        .form-label {{ display: block; font-size: 0.85rem; margin-bottom: 0.4rem; color: var(--text-muted); }}
        .form-input {{ width: 100%; background: var(--bg-elevated); border: 1px solid var(--border-line); padding: 0.6rem 0.8rem; border-radius: 8px; color: var(--text-main); font-family: var(--font-mono); font-size: 0.85rem; }}
        .btn-recover {{
            background: linear-gradient(135deg, #0284c7, #2563eb);
            color: #fff;
            font-size: 0.78rem;
            padding: 0.35rem 0.75rem;
            border-radius: 9999px;
            font-weight: 600;
            border: 1px solid rgba(56, 189, 248, 0.4);
            cursor: pointer;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            margin-left: 0.5rem;
        }}
        .btn-recover:hover {{
            background: linear-gradient(135deg, #0ea5e9, #3b82f6);
            box-shadow: 0 0 12px rgba(56, 189, 248, 0.5);
            transform: translateY(-1px);
        }}
    </style>
</head>
<body>
    <header>
        <a href="/" class="brand">
            <div class="brand-logo">▶</div>
            <div>
                <span class="brand-title">TVNow247</span>
                <span class="brand-badge">PRO BUFFER</span>
            </div>
        </a>

        <div class="header-center" style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
            <div id="buffer-pill" class="buffer-pill" title="Segundos de vídeo acumulados en memoria por delante del punto actual de reproducción">
                <span id="buffer-dot" class="status-dot dot-yellow"></span>
                <span id="buffer-text">Búfer: 0.0s (Iniciando)</span>
            </div>
            <span id="auto-recover-badge" class="badge-auto" style="font-size:0.75rem; color:#10b981; background:rgba(16,185,129,0.15); border:1px solid rgba(16,185,129,0.3); border-radius:999px; padding:4px 10px; font-weight:600; display:flex; align-items:center; gap:4px;" title="Supervisa y salta micro-cortes y recupera el búfer automáticamente sin necesidad de pulsar botones">🛡️ Auto-Recuperación Activa</span>
            <button class="btn-recover" id="recover-buffer-btn" onclick="recoverBuffer(false)" title="Fuerza la recuperación del búfer sincronizando con el directo">⚡ Recuperar Búfer</button>
        </div>

        <div class="header-actions">
            <button class="btn" onclick="openDirectUrlModal()">🔗 Probar URL</button>
            <button class="btn" onclick="openM3uModal()">📋 Enlaces M3U</button>
            <button class="btn" id="hud-toggle-btn" onclick="toggleStatsHud()">📊 HUD</button>
        </div>
    </header>

    <main class="main-container">
        <!-- Reproductor Central -->
        <section class="player-card">
            <div class="video-wrapper">
                <video id="video-player" controls autoplay playsinline></video>
                <!-- HUD de Estadísticas Avanzadas -->
                <div id="stats-hud" class="stats-hud">
                    <div class="hud-row"><span>Canal:</span> <span id="hud-channel" class="hud-val">-</span></div>
                    <div class="hud-row"><span>Búfer adelante:</span> <span id="hud-buffer" class="hud-val">0.0 s</span></div>
                    <div class="hud-row"><span>Resolución:</span> <span id="hud-res" class="hud-val">Detectando...</span></div>
                    <div class="hud-row"><span>Tasa de bits:</span> <span id="hud-bitrate" class="hud-val">-</span></div>
                    <div class="hud-row"><span>Caché Proxy Hits:</span> <span id="hud-cache" class="hud-val">-</span></div>
                    <div class="hud-row"><span>Latencia upstream:</span> <span id="hud-latency" class="hud-val">&lt; 1 ms (RAM)</span></div>
                </div>
            </div>

            <!-- Fila de Detalles del Canal -->
            <div class="player-info">
                <div class="channel-details">
                    <img id="current-logo" class="channel-logo-large" src="/placeholder.png" alt="Logo" onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'40\\' height=\\'40\\' viewBox=\\'0 0 24 24\\' fill=\\'none\\' stroke=\\'%2394a3b8\\' stroke-width=\\'2\\'><rect x=\\'2\\' y=\\'7\\' width=\\'20\\' height=\\'15\\' rx=\\'2\\'/><polyline points=\\'17 2 12 7 7 2\\'/></svg>'">
                    <div>
                        <div id="current-name" class="channel-title">Cargando canal...</div>
                        <div class="channel-meta">
                            <span id="current-group" class="tag-pill">En directo</span>
                            <span>•</span>
                            <span id="current-stream-info">HLS Desofuscado MPEG-TS</span>
                        </div>
                    </div>
                </div>

                <div class="header-actions">
                    <button class="audio-boost-btn" id="boost-btn" onclick="cycleAudioBoost()" title="Amplifica el audio si la emisión original tiene volumen bajo">🔊 Volumen: 100%</button>
                    <button class="btn" onclick="video.requestPictureInPicture()" title="Ver en miniatura flotante">🔲 PiP</button>
                </div>
            </div>

            <!-- Barra de Herramientas de Búfer & Offset -->
            <div class="buffer-toolbar">
                <div class="mode-group">
                    <span class="mode-label">Perfil de Búfer:</span>
                    <button class="mode-btn active" id="mode-stable" onclick="setBufferProfile('stable')">🛡️ Ultra Estable (20s)</button>
                    <button class="mode-btn" id="mode-balanced" onclick="setBufferProfile('balanced')">⚡ Equilibrado (12s)</button>
                    <button class="mode-btn" id="mode-low" onclick="setBufferProfile('low')">🚀 Baja Latencia (4s)</button>
                </div>

                <div class="mode-group">
                    <span class="mode-label" id="prefetch-stats-badge" style="font-family: var(--font-mono); font-size: 0.78rem;">Precarga activa en RAM</span>
                </div>
            </div>
        </section>

        <!-- Barra Lateral de Canales -->
        <aside class="sidebar-card">
            <div class="sidebar-header">
                <div class="search-box">
                    <span class="search-icon">🔍</span>
                    <input type="text" id="channel-search" class="search-input" placeholder="Buscar entre 800+ canales..." oninput="filterChannels()">
                </div>
                <div class="filter-tabs">
                    <button class="filter-tab active" onclick="setCategoryFilter('ALL', this)">Todos</button>
                    <button class="filter-tab" onclick="setCategoryFilter('US', this)">USA</button>
                    <button class="filter-tab" onclick="setCategoryFilter('ES', this)">España</button>
                    <button class="filter-tab" onclick="setCategoryFilter('UK', this)">UK</button>
                    <button class="filter-tab" onclick="setCategoryFilter('Sports', this)">Deportes</button>
                    <button class="filter-tab" onclick="setCategoryFilter('News', this)">Noticias</button>
                </div>
            </div>

            <div id="channel-list" class="channel-list">
                <div style="padding: 2rem; text-align: center; color: var(--text-muted); font-size: 0.85rem;">Cargando catálogo...</div>
            </div>
        </aside>
    </main>

    <!-- Modal Probar URL Directa -->
    <div id="direct-url-modal" class="modal-backdrop">
        <div class="modal-content">
            <div class="modal-header">
                <h3 class="modal-title">Probar URL o Token Upstream</h3>
                <button class="close-btn" onclick="closeModal('direct-url-modal')">&times;</button>
            </div>
            <p style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 1rem;">
                Pega directamente cualquier URL de playlist de TVNow247 o un token con <code>channelId</code>:
            </p>
            <div class="form-group">
                <input type="text" id="direct-url-input" class="form-input" placeholder="https://chunk.tvnow247.today/api/proxy/playlist?token=...">
            </div>
            <div style="display: flex; justify-content: flex-end; gap: 0.75rem;">
                <button class="btn" onclick="closeModal('direct-url-modal')">Cancelar</button>
                <button class="btn btn-primary" onclick="submitDirectUrl()">Reproducir con Búfer Pro</button>
            </div>
        </div>
    </div>

    <!-- Modal M3U y Enlaces -->
    <div id="m3u-modal" class="modal-backdrop">
        <div class="modal-content">
            <div class="modal-header">
                <h3 class="modal-title">Enlaces M3U e Integración IPTV</h3>
                <button class="close-btn" onclick="closeModal('m3u-modal')">&times;</button>
            </div>
            <div class="form-group">
                <label class="form-label">Lista M3U Completa (TiviMate, Kodi, VLC, OTT Navigator):</label>
                <input type="text" class="form-input" readonly value="{base_url}/channels.m3u" onclick="this.select()">
            </div>
            <div class="form-group">
                <label class="form-label">URL del canal actual:</label>
                <input type="text" id="current-m3u8-url" class="form-input" readonly value="" onclick="this.select()">
            </div>
            <p style="font-size: 0.8rem; color: var(--text-muted); margin-top: 1rem;">
                💡 <strong>Consejo PRO:</strong> Todas las listas M3U generadas por este proxy utilizan automáticamente la ventana deslizante ampliada de 8 fragmentos y el tag RFC 8216 TIME-OFFSET para evitar búfer en cualquier reproductor.
            </p>
            <div style="display: flex; justify-content: flex-end; margin-top: 1.25rem;">
                <button class="btn btn-primary" onclick="closeModal('m3u-modal')">Cerrar</button>
            </div>
        </div>
    </div>

    <script>
        const INITIAL_CHANNEL_ID = "{initial_cid}";
        const BASE_URL = "{base_url}";
        const video = document.getElementById('video-player');
        let hls = null;
        let allChannels = [];
        let currentChannel = null;
        let activeCategory = 'ALL';
        let currentBufferOffset = 20.0;
        let audioCtx = null, gainNode = null, boostLevel = 1.0;

        // Iniciar aplicación
        window.addEventListener('DOMContentLoaded', () => {{
            initAudioContextOnUserGesture();
            loadChannelsCatalog();
            startStatsPolling();
            setupKeyboardShortcuts();
        }});

        async function loadChannelsCatalog() {{
            try {{
                const res = await fetch('/api/channels');
                allChannels = await res.json();
                renderChannelList(allChannels);

                // Seleccionar canal inicial
                let target = allChannels.find(c => c.id === INITIAL_CHANNEL_ID);
                if (!target && allChannels.length > 0) target = allChannels[0];
                if (target) selectChannel(target);
            }} catch (e) {{
                console.error("Error al cargar canales:", e);
                document.getElementById('channel-list').innerHTML = '<div style="padding:2rem;text-align:center;color:#ef4444;">Error al cargar canales</div>';
            }}
        }}

        function renderChannelList(channels) {{
            const container = document.getElementById('channel-list');
            if (channels.length === 0) {{
                container.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-muted);">No se encontraron canales</div>';
                return;
            }}

            const html = channels.map(c => `
                <div class="channel-item ${{currentChannel && currentChannel.id === c.id ? 'active' : ''}}" onclick="onChannelClick('${{c.id}}')">
                    <img class="channel-item-logo" src="${{c.logo || '/placeholder.png'}}" alt="" onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'30\\' height=\\'30\\' viewBox=\\'0 0 24 24\\' fill=\\'none\\' stroke=\\'%2394a3b8\\' stroke-width=\\'2\\'><rect x=\\'2\\' y=\\'7\\' width=\\'20\\' height=\\'15\\' rx=\\'2\\'/><polyline points=\\'17 2 12 7 7 2\\'/></svg>'">
                    <span class="channel-item-name">${{c.name}}</span>
                    <span class="channel-item-group">${{c.group || 'Live'}}</span>
                </div>
            `).join('');
            container.innerHTML = html;
        }}

        function onChannelClick(cid) {{
            const target = allChannels.find(c => c.id === cid);
            if (target) selectChannel(target);
        }}

        function selectChannel(ch) {{
            currentChannel = ch;
            document.getElementById('current-name').textContent = ch.name;
            document.getElementById('current-logo').src = ch.logo || '/placeholder.png';
            document.getElementById('current-group').textContent = ch.group || 'Live';
            document.getElementById('current-m3u8-url').value = `${{BASE_URL}}/live/${{ch.id}}.m3u8`;

            // Actualizar clase activa en la lista
            document.querySelectorAll('.channel-item').forEach(el => {{
                el.classList.remove('active');
            }});
            const clicked = Array.from(document.querySelectorAll('.channel-item')).find(el => el.textContent.includes(ch.name));
            if (clicked) clicked.classList.add('active');

            // Actualizar URL del navegador sin recarga
            history.replaceState(null, '', `/player/${{ch.id}}`);

            // Cargar stream con el offset de búfer configurado
            const streamUrl = `/live/${{ch.id}}.m3u8?offset=${{currentBufferOffset}}`;
            loadStream(streamUrl);
        }}

        function loadStream(url) {{
            if (hls) {{
                hls.destroy();
            }}

            if (Hls.isSupported()) {{
                // Configuración de Hls.js optimizada para BÚFER ULTRA ESTABLE Y RESILIENTE CON TOLERANCIA A HUECOS PTS
                const hlsConfig = {{
                    enableWorker: true,
                    maxBufferLength: 60,              // Mantener hasta 60s en búfer local
                    maxMaxBufferLength: 120,          // Tope máximo 120s
                    maxBufferSize: 150 * 1024 * 1024, // Búfer de hasta 150MB en memoria
                    maxBufferHole: 0.8,               // Tolera y salta automáticamente micro-huecos PTS de hasta 800ms
                    maxSeekHole: 2.0,                 // Tolera huecos de hasta 2s en saltos
                    backBufferLength: 30,             // Retroceso fluido
                    liveSyncDuration: 18.0,           // Mantener 18s de distancia al vivo para búfer blindado
                    liveMaxLatencyDuration: 45.0,
                    liveDurationInfinity: true,
                    manifestLoadingTimeOut: 6000,
                    manifestLoadingMaxRetry: 6,
                    fragLoadingTimeOut: 10000,
                    fragLoadingMaxRetry: 5,
                    nudgeMaxRetry: 10,
                    nudgeOffset: 0.1,
                    highBufferWatchdogPeriod: 1,      // Supervisión interna de Hls.js cada 1 segundo
                    lowLatencyMode: false             // Priorizar estabilidad absoluta sin congelaciones
                }};

                hls = new Hls(hlsConfig);
                hls.loadSource(url);
                hls.attachMedia(video);

                hls.on(Hls.Events.MANIFEST_PARSED, () => {{
                    video.play().catch(e => console.log("Autoplay bloqueado por navegador:", e));
                }});

                hls.on(Hls.Events.LEVEL_LOADED, (e, data) => {{
                    if (data.details && data.details.totalduration) {{
                        document.getElementById('hud-channel').textContent = currentChannel ? currentChannel.name : 'Stream';
                    }}
                }});

                hls.on(Hls.Events.ERROR, (event, data) => {{
                    if (data.details === Hls.ErrorDetails.BUFFER_STALLED_ERROR) {{
                        console.warn("[HLS] Buffer stalled reportado por Hls.js. Auto-recuperando...");
                        hls.startLoad();
                        recoverBuffer(true);
                    }}
                    if (data.fatal) {{
                        switch (data.type) {{
                            case Hls.ErrorTypes.NETWORK_ERROR:
                                console.warn("[HLS] Error de red HLS fatal, recuperando flujo...");
                                hls.startLoad();
                                break;
                            case Hls.ErrorTypes.MEDIA_ERROR:
                                console.warn("[HLS] Error de medios HLS fatal, recuperando códec/buffer...");
                                hls.recoverMediaError();
                                break;
                            default:
                                console.error("[HLS] Error fatal irrecuperable, reintentando carga:", data);
                                setTimeout(() => hls.loadSource(url), 1200);
                                break;
                        }}
                    }}
                }});
            }} else if (video.canPlayType('application/vnd.apple.mpegurl')) {{
                // Safari iOS/macOS nativo
                video.src = url;
                video.play().catch(e => console.log("Autoplay:", e));
            }}
        }}

        // Cálculo del búfer continuo real puenteando micro-huecos (< 0.8s) entre fragmentos TS
        function getRealContinuousBufferAhead() {{
            if (!video || !video.buffered.length) return 0;
            const cur = video.currentTime;
            let total = 0;
            let matched = false;

            for (let i = 0; i < video.buffered.length; i++) {{
                const s = video.buffered.start(i);
                const e = video.buffered.end(i);
                if (!matched) {{
                    if (cur >= s && cur <= e) {{
                        matched = true;
                        total += (e - cur);
                    }} else if (s > cur && (s - cur) <= 0.8) {{
                        // El cabezal está en un micro-hueco justo antes del rango i
                        matched = true;
                        total += (e - s);
                    }}
                }} else {{
                    const prevEnd = video.buffered.end(i - 1);
                    if (s - prevEnd <= 0.8) {{
                        total += (e - s);
                    }} else {{
                        break;
                    }}
                }}
            }}
            return total;
        }}

        // Función para forzar recuperación manual o automática del búfer
        function recoverBuffer(isAuto = false) {{
            if (!video) return;
            const text = document.getElementById('buffer-text');
            const dot = document.getElementById('buffer-dot');
            if (dot) dot.className = 'status-dot dot-yellow';
            if (text) text.textContent = isAuto ? "⚡ Auto-recuperando búfer..." : "⚡ Recuperando búfer...";

            console.log(`[BufferRecovery] Sincronizando búfer (isAuto=${{isAuto}})...`);
            if (hls) {{
                hls.startLoad();
                // Colocar el cabezal con un colchón seguro de ~16 segundos por detrás del vivo
                if (hls.liveSyncPosition && hls.liveSyncPosition > 20) {{
                    const targetPos = Math.max(0, hls.liveSyncPosition - 16.0);
                    video.currentTime = targetPos;
                }} else if (video.buffered.length > 0) {{
                    const end = video.buffered.end(video.buffered.length - 1);
                    video.currentTime = Math.max(0, end - 12.0);
                }}
            }}
            video.play().catch(() => {{}});
        }}

        // -------------------------------------------------------------------
        // Centinela Autónomo: Salto de Micro-Gaps y Auto-Recuperación de Búfer
        // -------------------------------------------------------------------
        let lastPlayheadPos = -1;
        let stallTicks = 0;
        let lastAutoRecoverTimestamp = 0;
        let consecutiveStallCount = 0;

        setInterval(() => {{
            if (!video) return;

            const curTime = video.currentTime;
            const bufferAhead = getRealContinuousBufferAhead();

            const dot = document.getElementById('buffer-dot');
            const text = document.getElementById('buffer-text');
            const hudBuf = document.getElementById('hud-buffer');
            if (hudBuf) hudBuf.textContent = `${{bufferAhead.toFixed(1)}} s`;

            if (bufferAhead >= 14) {{
                dot.className = 'status-dot dot-green';
                text.textContent = `Búfer: ${{bufferAhead.toFixed(1)}}s (Ultra Estable)`;
            }} else if (bufferAhead >= 6) {{
                dot.className = 'status-dot dot-green';
                text.textContent = `Búfer: ${{bufferAhead.toFixed(1)}}s (Estable)`;
            }} else if (bufferAhead >= 2) {{
                dot.className = 'status-dot dot-yellow';
                text.textContent = `Búfer: ${{bufferAhead.toFixed(1)}}s (Llenando...)`;
            }} else if (video.paused) {{
                dot.className = 'status-dot dot-yellow';
                text.textContent = `Pausado`;
            }} else {{
                dot.className = 'status-dot dot-red';
                text.textContent = `Búfer: ${{bufferAhead.toFixed(1)}}s (Sincronizando)`;
            }}

            // HUD Stats
            if (video.videoWidth && document.getElementById('hud-res')) {{
                document.getElementById('hud-res').textContent = `${{video.videoWidth}}x${{video.videoHeight}}`;
            }}

            // Si está pausado deliberadamente por el usuario o ha terminado, no intervenir
            if (video.paused || video.ended) {{
                stallTicks = 0;
                lastPlayheadPos = curTime;
                return;
            }}

            // Comprobar si el vídeo está avanzando
            const isAdvancing = Math.abs(curTime - lastPlayheadPos) > 0.04;
            lastPlayheadPos = curTime;

            if (isAdvancing) {{
                stallTicks = 0;
                consecutiveStallCount = 0;
                return;
            }}

            // Si el vídeo debería reproducirse pero no avanza:
            stallTicks++;

            // NIVEL 1: Micro-gap hopper (salto de hueco PTS de 40ms entre fragmentos TS)
            // Se activa a partir de ~600ms de estancamiento
            if (stallTicks >= 2) {{
                for (let i = 0; i < video.buffered.length; i++) {{
                    const bStart = video.buffered.start(i);
                    const bEnd = video.buffered.end(i);
                    // Si el siguiente rango empieza justo después del tiempo actual (<= 1.5s)
                    if (bStart > curTime && (bStart - curTime) <= 1.5) {{
                        console.log(`[Watchdog] Saltando micro-gap PTS (${{(bStart - curTime).toFixed(3)}}s) hacia ${{bStart + 0.04}}s`);
                        video.currentTime = bStart + 0.04;
                        video.play().catch(() => {{}});
                        stallTicks = 0;
                        return;
                    }}
                }}
            }}

            // NIVEL 2: Auto-recuperación de búfer sin interacción del usuario
            // Si lleva congelado >= 6 ticks (~1.8s) y el búfer inmediato está agotado
            if (stallTicks >= 6) {{
                const now = Date.now();
                if (now - lastAutoRecoverTimestamp > 3500) {{
                    lastAutoRecoverTimestamp = now;
                    consecutiveStallCount++;
                    console.warn(`[Watchdog] Congelación detectada (#${{consecutiveStallCount}}). Disparando auto-recuperación de búfer...`);

                    if (consecutiveStallCount >= 4) {{
                        // Múltiples congelaciones consecutivas: recargar origen limpiamente
                        console.warn("[Watchdog] Reinicializando pipeline tras múltiples congelaciones...");
                        consecutiveStallCount = 0;
                        if (currentChannel) {{
                            loadStream(`/live/${{currentChannel.id}}.m3u8?offset=${{currentBufferOffset}}`);
                        }} else {{
                            recoverBuffer(true);
                        }}
                    }} else {{
                        recoverBuffer(true);
                    }}
                    stallTicks = 0;
                }}
            }}
        }}, 300);

        // Perfiles de búfer
        function setBufferProfile(mode) {{
            document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
            if (mode === 'stable') {{
                currentBufferOffset = 20.0;
                document.getElementById('mode-stable').classList.add('active');
            }} else if (mode === 'balanced') {{
                currentBufferOffset = 12.0;
                document.getElementById('mode-balanced').classList.add('active');
            }} else if (mode === 'low') {{
                currentBufferOffset = 4.0;
                document.getElementById('mode-low').classList.add('active');
            }}
            if (currentChannel) {{
                selectChannel(currentChannel);
            }}
        }}

        // Audio Boost (100% -> 150% -> 200%) vía Web Audio API GainNode
        function initAudioContextOnUserGesture() {{
            const resumeAudio = () => {{
                if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
            }};
            window.addEventListener('click', resumeAudio, {{ once: true }});
            window.addEventListener('keydown', resumeAudio, {{ once: true }});
        }}

        function cycleAudioBoost() {{
            if (boostLevel === 1.0) boostLevel = 1.5;
            else if (boostLevel === 1.5) boostLevel = 2.0;
            else boostLevel = 1.0;

            if (!audioCtx) {{
                try {{
                    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                    const src = audioCtx.createMediaElementSource(video);
                    gainNode = audioCtx.createGain();
                    src.connect(gainNode);
                    gainNode.connect(audioCtx.destination);
                }} catch (e) {{
                    console.warn("Audio Context error:", e);
                }}
            }}
            if (audioCtx && audioCtx.state === 'suspended') {{
                audioCtx.resume();
            }}
            if (gainNode) {{
                gainNode.gain.value = boostLevel;
            }}
            document.getElementById('boost-btn').textContent = `🔊 Volumen: ${{Math.round(boostLevel * 100)}}%`;
        }}

        function toggleStatsHud() {{
            document.getElementById('stats-hud').classList.toggle('show');
            document.getElementById('hud-toggle-btn').classList.toggle('btn-primary');
        }}

        // Polling de estadísticas de caché y backend
        async function startStatsPolling() {{
            setInterval(async () => {{
                try {{
                    const res = await fetch('/api/status');
                    const data = await res.json();
                    document.getElementById('hud-cache').textContent = `${{data.cache_hits}} hits / ${{data.cache_misses}} misses (${{data.hit_ratio}})`;
                    document.getElementById('prefetch-stats-badge').textContent = `RAM: ${{data.cached_segments}} segs (${{data.prefetches}} precargas)`;
                }} catch (e) {{}}
            }}, 3000);
        }}

        // Filtro y búsqueda de canales
        function filterChannels() {{
            const query = document.getElementById('channel-search').value.toLowerCase().trim();
            const filtered = allChannels.filter(c => {{
                const matchesQuery = c.name.toLowerCase().includes(query) || (c.group && c.group.toLowerCase().includes(query));
                if (!matchesQuery) return false;
                if (activeCategory === 'ALL') return true;
                if (activeCategory === 'US') return c.group && (c.group.includes('US') || c.group.includes('USA'));
                if (activeCategory === 'ES') return c.group && (c.group.includes('ES') || c.group.includes('Spain'));
                if (activeCategory === 'UK') return c.group && (c.group.includes('UK') || c.group.includes('GB'));
                if (activeCategory === 'Sports') return c.name.toLowerCase().includes('sport') || (c.group && c.group.toLowerCase().includes('sport'));
                if (activeCategory === 'News') return c.name.toLowerCase().includes('news') || (c.group && c.group.toLowerCase().includes('news'));
                return true;
            }});
            renderChannelList(filtered);
        }}

        function setCategoryFilter(cat, btn) {{
            activeCategory = cat;
            document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
            btn.classList.add('active');
            filterChannels();
        }}

        // Modales
        function openDirectUrlModal() {{ document.getElementById('direct-url-modal').classList.add('show'); }}
        function openM3uModal() {{ document.getElementById('m3u-modal').classList.add('show'); }}
        function closeModal(id) {{ document.getElementById(id).classList.remove('show'); }}

        function submitDirectUrl() {{
            let url = document.getElementById('direct-url-input').value.trim();
            if (!url) return;
            closeModal('direct-url-modal');
            document.getElementById('current-name').textContent = "Stream Personalizado";
            document.getElementById('current-group').textContent = "Direct URL";

            // Si el usuario pegó la URL de su contenedor local con /proxy?url=..., extraer la url interna
            if (url.includes('/proxy?url=')) {{
                try {{
                    const parsedUrl = new URL(url, window.location.origin);
                    const innerUrl = parsedUrl.searchParams.get('url');
                    if (innerUrl) url = innerUrl;
                }} catch(e) {{}}
            }}

            const proxyUrl = `/proxy?url=${{encodeURIComponent(url)}}&offset=${{currentBufferOffset}}`;
            loadStream(proxyUrl);
        }}

        // Atajos de teclado
        function setupKeyboardShortcuts() {{
            window.addEventListener('keydown', (e) => {{
                if (['input', 'textarea'].includes(document.activeElement.tagName.toLowerCase())) return;
                if (e.code === 'Space') {{
                    e.preventDefault();
                    video.paused ? video.play() : video.pause();
                }} else if (e.code === 'KeyR') {{
                    e.preventDefault();
                    recoverBuffer();
                }} else if (e.code === 'KeyF') {{
                    e.preventDefault();
                    document.fullscreenElement ? document.exitFullscreen() : video.requestFullscreen();
                }} else if (e.code === 'KeyP') {{
                    e.preventDefault();
                    video.requestPictureInPicture();
                }} else if (e.code === 'KeyM') {{
                    video.muted = !video.muted;
                }} else if (e.code === 'KeyS') {{
                    toggleStatsHud();
                }} else if (e.code === 'KeyB') {{
                    cycleAudioBoost();
                }}
            }});
        }}
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index_web_player(request: Request):
    """Página principal: Reproductor Web PRO de alta fidelidad con control de búfer y explorador."""
    base_url = str(request.base_url).rstrip("/")
    return render_pro_web_player(base_url, initial_cid="303")


@app.get("/player/{channel_id}", response_class=HTMLResponse)
@app.get("/play/{channel_id}", response_class=HTMLResponse)
async def player_direct_channel(channel_id: str, request: Request):
    """Abre el reproductor web apuntando directamente a un canal específico."""
    base_url = str(request.base_url).rstrip("/")
    return render_pro_web_player(base_url, initial_cid=channel_id)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("proxy_server:app", host="0.0.0.0", port=port, reload=False)
