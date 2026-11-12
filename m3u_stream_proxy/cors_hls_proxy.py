import io
import re
import os
import time
import gzip
import json
import base64
import logging
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, quote, unquote
from collections import OrderedDict

import httpx
from fastapi import Request, HTTPException
from fastapi.responses import Response

logger = logging.getLogger("m3u_stream_proxy.cors_proxy")

DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
WINDOW_MAX_SEGMENTS = int(os.environ.get("WINDOW_MAX_SEGMENTS", 14))


# ---------------------------------------------------------------------------
# Desofuscador de Segmentos MPEG-TS (WebP EXIF, PNG, GZIP)
# ---------------------------------------------------------------------------
def deobfuscate_segment(data: bytes) -> bytes:
    """
    Desofusca un segmento devuelto por CDN (TikTok, TVNow247 u otros proveedores).
    Soporta:
    1. MPEG-TS puro (comienza con el byte de sincronización 0x47).
    2. Formato WebP actual: Contenedor RIFF / WEBP con payload MPEG-TS en chunk EXIF.
    3. Formato PNG / GZIP directo o con cabecera TIKTIKPX.
    """
    if not data:
        return data

    # 1. Ya es flujo MPEG-TS directo
    if data.startswith(b"\x47"):
        return data

    # 2. Formato WebP (RIFF....WEBP) con payload MPEG-TS en chunk EXIF
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        try:
            exif_idx = data.find(b"EXIF")
            if exif_idx != -1 and len(data) >= exif_idx + 8:
                chunk_size = int.from_bytes(data[exif_idx + 4:exif_idx + 8], "little")
                payload = data[exif_idx + 8:exif_idx + 8 + chunk_size]

                # Si el payload viniese comprimido con gzip
                if payload.startswith(b"\x1f\x8b\x08"):
                    try:
                        return gzip.decompress(payload)
                    except Exception:
                        pass

                # Si empieza directamente con el sync byte 0x47 de MPEG-TS
                if payload.startswith(b"\x47"):
                    return payload

                # Buscar posible offset del sync byte 0x47 dentro de los primeros bytes
                sync_pos = payload.find(b"\x47")
                if 0 <= sync_pos < 64:
                    return payload[sync_pos:]

                return payload
        except Exception as e:
            logger.error(f"Error al desofuscar segmento WEBP/EXIF: {e}")

    # 3. Formato PNG con cabecera TIKTIKPX o gzip
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        try:
            try:
                from PIL import Image
                im = Image.open(io.BytesIO(data))
                raw_pixels = im.tobytes()

                # Comprobar firma TIKTIKPX
                if raw_pixels[:8] == b"TIKTIKPX":
                    content_len = int.from_bytes(raw_pixels[8:12], "big")
                    payload = raw_pixels[12:12 + content_len]
                    return gzip.decompress(payload)

                if raw_pixels[:3] == b"\x1f\x8b\x08":
                    return gzip.decompress(raw_pixels)
            except ImportError:
                # Si Pillow no está instalado, buscar payload gzip directo
                gzip_idx = data.find(b"\x1f\x8b\x08")
                if gzip_idx != -1:
                    try:
                        return gzip.decompress(data[gzip_idx:])
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error al desofuscar segmento PNG: {e}")

    # Fallback: devolver los datos originales si no se pudo transformar
    return data


def decode_token(token_str: str) -> Optional[dict]:
    """Decodifica un token en base64 para extraer su payload."""
    try:
        padded = token_str + "=" * (-len(token_str) % 4)
        decoded = base64.b64decode(padded).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Deduplicación y Clave Canónica de Segmentos
# ---------------------------------------------------------------------------
def get_canonical_segment_key(raw_url: str) -> str:
    """
    Extrae la clave única canónica de un segmento para deduplicación exacta.
    Soporta:
    1. Proxies upstream que encapsulan la URL real en parámetro 'u=' (ej. canales 500-520 con /api/proxy/seg?token=...&u=https%3A%2F%2F...%2Ftwourqvinvybv-1009839510.ts).
    2. CDNs con rutas únicas directas (ej. TikTok CDN /tos-.../hash~tplv-tiktokx-origin.image?...).
    3. URLs genéricas donde la query string define el fragmento descartando tokens efímeros.
    """
    if not raw_url:
        return ""

    # 1. Parámetro 'u=' con URL destino encapsulada (canales 500-520 y proxies upstream)
    u_match = re.search(r'[?&]u=([^&]+)', raw_url)
    if u_match:
        try:
            target_url = unquote(u_match.group(1))
            target_parsed = urlparse(target_url)
            if target_parsed.path and target_parsed.path != "/":
                return target_parsed.path
            return target_url
        except Exception:
            pass

    parsed = urlparse(raw_url)
    path = parsed.path or ""

    # 2. Si el path es genérico (ej. /api/proxy/seg, /segment, /proxy, etc.) o no tiene extensión
    generic_paths = {"/api/proxy/seg", "/segment", "/proxy", "/seg", ""}
    if path in generic_paths or not re.search(r'\.[a-zA-Z0-9]+$', path):
        # Descartar parámetros volátiles de timestamp o firma que cambian en cada refresh
        clean_q = re.sub(r'[?&](?:ts|token|expires|signature|x-signature|x-expires)=\d+', '', parsed.query or '')
        clean_q = re.sub(r'[?&]token=[^&]+', '', clean_q).strip('&')
        return f"{path}?{clean_q}" if clean_q else raw_url

    # 3. CDN directa con path único (ej. TikTok CDN)
    return path


class SegmentItem:
    def __init__(self, seq: int, inf: str, raw_url: str):
        self.seq = seq
        self.inf = inf
        self.raw_url = raw_url
        self.path_key = get_canonical_segment_key(raw_url)


# ---------------------------------------------------------------------------
# Gestor de Ventana Deslizante de Canales HLS (Sliding Window & Buffer Offset)
# ---------------------------------------------------------------------------
class ChannelWindowManager:
    """
    Gestor de Ventana Deslizante (Sliding Window) para streams HLS en vivo.
    Mantiene una ventana contigua de segmentos en memoria RAM, asigna secuencias
    estrictamente crecientes, deduplica segmentos mediante clave canónica y
    permite añadir un colchón de retardo seguro (buffer offset) sin micro-cortes.
    """
    def __init__(self, max_segments: int = 14):
        self.max_segments = max(max_segments, 8)
        self.windows: Dict[str, List[SegmentItem]] = {}
        self.last_seq_assigned: Dict[str, int] = {}
        self.last_updated: Dict[str, float] = {}
        self.last_target_dur: Dict[str, int] = {}
        self.last_rendered_m3u8: Dict[str, str] = {}

    def get_last_rendered(self, channel_id: str) -> Optional[str]:
        return self.last_rendered_m3u8.get(channel_id)

    def process_and_extend(
        self,
        channel_id: str,
        media_seq: int,
        target_dur: int,
        incoming: List[Tuple[str, str]],
        request_base_url: str,
        extra_query: str = "",
        buffer_offset: float = 16.0,
        key_lines: Optional[List[str]] = None
    ) -> str:
        now = time.time()
        last_t = self.last_updated.get(channel_id, 0.0)
        self.last_updated[channel_id] = now
        self.last_target_dur[channel_id] = target_dur

        # Umbral de expiración por inactividad dinámico: escala según target_dur (mínimo 60s o 6x target_dur)
        max_inactivity = max(60.0, float(target_dur) * 6.0)
        if last_t > 0 and (now - last_t) > max_inactivity:
            self.windows.pop(channel_id, None)
            self.last_seq_assigned.pop(channel_id, None)

        existing = self.windows.get(channel_id, [])

        # Comprobar solapamiento real con la ventana existente usando clave canónica
        if existing:
            existing_paths = {item.path_key for item in existing}
            incoming_paths = {get_canonical_segment_key(url) for _, url in incoming}
            overlap = existing_paths.intersection(incoming_paths)
            if not overlap:
                logger.info(f"Salto o discontinuidad detectada en stream {channel_id} (0 solapamiento). Reiniciando ventana manteniendo secuencia creciente.")
                last_seq_base = self.last_seq_assigned.get(channel_id, media_seq - 1)
                self.windows[channel_id] = []
                existing = []
                self.last_seq_assigned[channel_id] = max(last_seq_base, media_seq - 1)

        if not existing:
            # Inicialización de ventana limpia
            base_seq = max(media_seq, self.last_seq_assigned.get(channel_id, 0) + 1)
            new_window = []
            curr_seq = base_seq
            for inf, url in incoming:
                new_window.append(SegmentItem(seq=curr_seq, inf=inf, raw_url=url))
                curr_seq += 1
            self.windows[channel_id] = new_window
            self.last_seq_assigned[channel_id] = curr_seq - 1
        else:
            # Ventana previa contigua: identificar qué segmentos entrantes son realmente nuevos
            existing_paths = {item.path_key for item in existing}
            last_seq = self.last_seq_assigned.get(channel_id, existing[-1].seq)

            new_items = []
            for inf, url in incoming:
                p_key = get_canonical_segment_key(url)
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

        # TARGETDURATION debe ser >= que la duración máxima de cualquier segmento en la ventana
        max_seg_dur = target_dur
        for item in contiguous:
            try:
                val_str = item.inf.replace("#EXTINF:", "").split(",")[0].strip()
                max_seg_dur = max(max_seg_dur, int(float(val_str) + 0.999))
            except Exception:
                pass
        target_dur = max_seg_dur

        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            f"#EXT-X-TARGETDURATION:{target_dur}",
            f"#EXT-X-MEDIA-SEQUENCE:{start_seq}",
            "#EXT-X-INDEPENDENT-SEGMENTS",
        ]

        if key_lines:
            for kl in key_lines:
                lines.append(kl)

        # Tag de offset HLS estándar RFC 8216: inicia la reproducción X segundos por detrás del vivo
        # permitiendo acumular un gran colchón de búfer sin cortes (solo si hay suficientes segmentos en la ventana)
        if buffer_offset is not None and buffer_offset > 0 and len(contiguous) >= 4:
            offset_val = -abs(buffer_offset)
            lines.append(f"#EXT-X-START:TIME-OFFSET={offset_val:.1f},PRECISE=NO")

        for item in contiguous:
            proxy_seg_url = f"{request_base_url}/stream/segment?url={quote(item.raw_url, safe='')}{extra_query}"
            lines.append(item.inf)
            lines.append(proxy_seg_url)

        rendered = "\n".join(lines) + "\n"
        self.last_rendered_m3u8[channel_id] = rendered
        return rendered


window_manager = ChannelWindowManager(max_segments=WINDOW_MAX_SEGMENTS)


# ---------------------------------------------------------------------------
# Caché LRU de Segmentos en RAM
# ---------------------------------------------------------------------------
class SegmentCache:
    """Caché LRU en memoria RAM para acelerar segmentos de video y reducir latencia."""
    def __init__(self, max_items: int = 120):
        self.max_items = max_items
        self.cache: OrderedDict[str, Tuple[bytes, str]] = OrderedDict()  # canonical_key -> (data, content_type)
        self.hits: int = 0
        self.misses: int = 0

    def get(self, key: str) -> Optional[Tuple[bytes, str]]:
        if key in self.cache:
            self.cache.move_to_end(key)
            self.hits += 1
            return self.cache[key]
        self.misses += 1
        return None

    def set(self, key: str, data: bytes, content_type: str):
        self.cache[key] = (data, content_type)
        self.cache.move_to_end(key)
        while len(self.cache) > self.max_items:
            self.cache.popitem(last=False)


segment_cache = SegmentCache(max_items=int(os.environ.get("MAX_CACHE_ITEMS", 120)))


# ---------------------------------------------------------------------------
# Funciones Auxiliares de Parsing
# ---------------------------------------------------------------------------
def parse_stream_url_and_headers(raw_url: str) -> Tuple[str, Dict[str, str]]:
    """
    Separa la URL real de las cabeceras personalizadas pasadas con la sintaxis IPTV |Header=Value&...
    Ej: https://example.com/live.m3u8|User-Agent=CustomUA&Referer=https://ref.com/
    """
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "*/*"
    }
    if not raw_url:
        return "", headers

    if "|" in raw_url:
        parts = raw_url.split("|", 1)
        actual_url = parts[0].strip()
        header_str = parts[1].strip()
        for pair in header_str.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                k_clean = k.strip()
                v_clean = v.strip()
                k_lower = k_clean.lower()
                if k_lower in ("user-agent", "http-user-agent"):
                    headers["User-Agent"] = v_clean
                elif k_lower in ("referer", "referrer", "http-referer"):
                    headers["Referer"] = v_clean
                elif k_lower in ("origin", "http-origin"):
                    headers["Origin"] = v_clean
                else:
                    headers[k_clean] = v_clean
        return actual_url, headers

    return raw_url.strip(), headers


def parse_playlist_body(text: str, base_url: str) -> Tuple[int, int, List[Tuple[str, str]], List[str], bool]:
    """
    Parsea una lista HLS.
    Devuelve (media_seq, target_dur, segments, key_lines, is_master_playlist).
    segments es una lista de tuplas (extinf_header, segment_absolute_url).
    """
    is_master = "#EXT-X-STREAM-INF" in text or ("#EXT-X-MEDIA" in text and "#EXTINF" not in text)
    if is_master:
        return 0, 0, [], [], True

    media_seq = 0
    target_dur = 4
    m_seq = re.search(r'#EXT-X-MEDIA-SEQUENCE:(\d+)', text)
    if m_seq:
        media_seq = int(m_seq.group(1))
    m_dur = re.search(r'#EXT-X-TARGETDURATION:(\d+)', text)
    if m_dur:
        target_dur = int(m_dur.group(1))

    segments = []
    key_lines = []
    curr_inf = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("##"):
            continue
        if line.startswith("#EXT-X-KEY"):
            key_lines.append(line)
        elif line.startswith("#EXTINF:"):
            curr_inf = line
        elif line.startswith("#"):
            continue
        elif curr_inf:
            full_seg_url = urljoin(base_url, line)
            segments.append((curr_inf, full_seg_url))
            curr_inf = None

    return media_seq, target_dur, segments, key_lines, False


# ---------------------------------------------------------------------------
# Proxificación de Manifiestos HLS (Master y Media Playlists con Búfer PRO)
# ---------------------------------------------------------------------------
async def proxy_m3u8_manifest(
    target_url: str,
    request_base_url: str,
    buffer_offset: float = 16.0,
    custom_ref: Optional[str] = None,
    custom_ua: Optional[str] = None,
    stream_id: Optional[str] = None
) -> Response:
    """
    Descarga una lista de reproducción HLS (.m3u8), amplía la ventana deslizante
    en memoria para estabilidad PRO, deduplica segmentos mediante clave canónica,
    reescribe fragmentos y sub-listas y entrega cabeceras CORS permisivas.
    """
    clean_target_url, headers = parse_stream_url_and_headers(target_url)

    # Corrección automática de dominios: chunk.tvnow247.today da timeout en /api/proxy/playlist
    # El dominio operativo para listas upstream es tvnow247.top
    if "chunk.tvnow247.today" in clean_target_url and "/api/proxy/playlist" in clean_target_url:
        clean_target_url = clean_target_url.replace("chunk.tvnow247.today", "tvnow247.top")
        logger.info(f"Redirigiendo playlist de chunk.tvnow247.today a tvnow247.top: {clean_target_url}")

    if custom_ref:
        headers["Referer"] = custom_ref
    if custom_ua:
        headers["User-Agent"] = custom_ua

    # Identificar canal / stream de forma unívoca para el gestor de ventana deslizante
    sid = stream_id
    if not sid:
        token_match = re.search(r'[?&]token=([^&]+)', clean_target_url)
        if token_match:
            tok = decode_token(token_match.group(1))
            if tok and "channelId" in tok:
                sid = f"tvnow_{tok['channelId']}"
    if not sid:
        sid = f"stream_{abs(hash(clean_target_url.split('?')[0])) % 100000}"

    # Parámetros para propagar en sub-manifiestos y segmentos
    extra_query = ""
    if "Referer" in headers:
        extra_query += f"&ref={quote(headers['Referer'], safe='')}"
    if headers.get("User-Agent") and headers["User-Agent"] != DEFAULT_USER_AGENT:
        extra_query += f"&ua={quote(headers['User-Agent'], safe='')}"
    if sid:
        extra_query += f"&stream_id={quote(sid, safe='')}"

    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Cache-Control": "no-cache, no-store, must-revalidate"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            r = await client.get(clean_target_url, headers=headers)
            if r.status_code != 200:
                # Si upstream falla momentáneamente, intentar devolver la última ventana buena en memoria
                cached_m3u = window_manager.get_last_rendered(sid)
                if cached_m3u:
                    logger.warning(f"Upstream devolvió {r.status_code}. Entregando última ventana en memoria para {sid}")
                    return Response(content=cached_m3u, media_type="application/vnd.apple.mpegurl", headers=cors_headers)
                raise HTTPException(status_code=r.status_code, detail=f"Error obteniendo stream remoto: {r.status_code}")

            content = r.text
            final_url = str(r.url)

    except HTTPException:
        raise
    except Exception as e:
        cached_m3u = window_manager.get_last_rendered(sid)
        if cached_m3u:
            logger.warning(f"Error conectando a upstream: {e}. Entregando última ventana en memoria para {sid}")
            return Response(content=cached_m3u, media_type="application/vnd.apple.mpegurl", headers=cors_headers)
        logger.error(f"Error proxificando m3u8 ({target_url}): {e}")
        raise HTTPException(status_code=502, detail=f"No se pudo acceder al stream HLS: {str(e)}")

    # Discernir si es una lista de medios o un manifiesto maestro (multivariante / pistas de audio)
    media_seq, target_dur, incoming_segs, key_lines, is_master = parse_playlist_body(content, final_url)

    # 1. Si es una lista de medios (contiene segmentos de vídeo/audio) -> Procesar con Gestor de Ventana Deslizante
    if not is_master and incoming_segs:
        # Reescribir URIs en etiquetas #EXT-X-KEY si las hubiera
        rewritten_keys = []
        for kl in key_lines:
            def replace_key_uri(m):
                k_uri = m.group(1)
                full_k = urljoin(final_url, k_uri)
                proxy_k = f"{request_base_url}/stream/segment?url={quote(full_k, safe='')}{extra_query}"
                return f'URI="{proxy_k}"'
            rewritten_keys.append(re.sub(r'URI="([^"]+)"', replace_key_uri, kl))

        m3u8_content = window_manager.process_and_extend(
            channel_id=sid,
            media_seq=media_seq,
            target_dur=target_dur,
            incoming=incoming_segs,
            request_base_url=request_base_url,
            extra_query=extra_query,
            buffer_offset=buffer_offset,
            key_lines=rewritten_keys
        )
        return Response(
            content=m3u8_content,
            media_type="application/vnd.apple.mpegurl",
            headers=cors_headers
        )

    # 2. Si es un manifiesto maestro (sub-listas de calidad / audio alternativo) -> Reescribir sub-manifiestos
    lines = content.splitlines()
    rewritten = []

    for line in lines:
        s_line = line.strip()
        if not s_line:
            rewritten.append(line)
            continue

        if s_line.startswith("#"):
            if 'URI="' in s_line:
                def replace_uri(m):
                    sub_uri = m.group(1)
                    full_sub = urljoin(final_url, sub_uri)
                    if ".m3u8" in full_sub or "playlist" in full_sub:
                        proxy_uri = f"{request_base_url}/stream/proxy.m3u8?url={quote(full_sub, safe='')}{extra_query}"
                    else:
                        proxy_uri = f"{request_base_url}/stream/segment?url={quote(full_sub, safe='')}{extra_query}"
                    return f'URI="{proxy_uri}"'
                s_line = re.sub(r'URI="([^"]+)"', replace_uri, s_line)
            rewritten.append(s_line)
        else:
            full_item_url = urljoin(final_url, s_line)
            clean_item = full_item_url.lower().split("?")[0]

            if clean_item.endswith(".m3u8") or ".m3u8" in clean_item or "playlist" in clean_item:
                proxy_line = f"{request_base_url}/stream/proxy.m3u8?url={quote(full_item_url, safe='')}{extra_query}"
            else:
                proxy_line = f"{request_base_url}/stream/segment?url={quote(full_item_url, safe='')}{extra_query}"
            rewritten.append(proxy_line)

    output_manifest = "\n".join(rewritten)
    return Response(
        content=output_manifest,
        media_type="application/vnd.apple.mpegurl",
        headers=cors_headers
    )


# ---------------------------------------------------------------------------
# Proxificación de Segmentos (.ts, .m4s, WebP/EXIF, PNG) con Desofuscación y Caché
# ---------------------------------------------------------------------------
async def proxy_segment_request(target_url: str, request: Request) -> Response:
    """
    Descarga, desofusca (WebP/PNG a MPEG-TS si procede) y sirve un fragmento de vídeo
    con caché en memoria RAM, soporte Range (206 Partial Content) y cabeceras CORS.
    """
    clean_target_url, req_headers = parse_stream_url_and_headers(target_url)

    ref_param = request.query_params.get("ref")
    ua_param = request.query_params.get("ua")
    if ref_param:
        req_headers["Referer"] = ref_param
    if ua_param:
        req_headers["User-Agent"] = ua_param

    canonical_key = get_canonical_segment_key(clean_target_url)

    # Comprobar si está en caché RAM por su clave canónica
    cached = segment_cache.get(canonical_key)
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

    # Trasladar cabecera Range si el reproductor la solicita
    range_header = request.headers.get("Range")
    if range_header:
        req_headers["Range"] = range_header

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r = await client.get(clean_target_url, headers=req_headers)
            if r.status_code not in (200, 206):
                raise HTTPException(status_code=r.status_code, detail="Error descargando fragmento")

            raw_data = r.content

            # Desofuscar segmento si viene empaquetado en WebP EXIF, PNG o GZIP
            data = deobfuscate_segment(raw_data)

            c_type = r.headers.get("content-type", "")
            if (
                data.startswith(b"\x47")
                or not c_type
                or "octet-stream" in c_type
                or "text/html" in c_type
                or "text/plain" in c_type
                or "image/" in c_type
                or ".ts" in clean_target_url.lower()
                or "/ace/c/" in clean_target_url
            ):
                c_type = "video/mp2t"

            # Guardar en caché si fue un 200 completo y no excede 12MB
            if r.status_code == 200 and len(data) <= 12 * 1024 * 1024:
                segment_cache.set(canonical_key, data, c_type)

            resp_headers = {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
                "Cache-Control": "public, max-age=86400, immutable",
                "X-Proxy-Cache": "MISS"
            }
            if "content-range" in r.headers:
                resp_headers["Content-Range"] = r.headers["content-range"]
            if "accept-ranges" in r.headers:
                resp_headers["Accept-Ranges"] = r.headers["accept-ranges"]

            total_len = len(data)
            # Soporte Range si el contenido fue modificado o desofuscado
            if range_header and range_header.startswith("bytes="):
                try:
                    parts = range_header.replace("bytes=", "").split("-")
                    start = int(parts[0]) if parts[0] else 0
                    end = int(parts[1]) if len(parts) > 1 and parts[1] else total_len - 1
                    if start < total_len:
                        end = min(end, total_len - 1)
                        chunk = data[start:end + 1]
                        resp_headers["Content-Range"] = f"bytes {start}-{end}/{total_len}"
                        resp_headers["Content-Length"] = str(len(chunk))
                        return Response(content=chunk, status_code=206, headers=resp_headers, media_type=c_type)
                except Exception:
                    pass

            resp_headers["Content-Length"] = str(total_len)
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
