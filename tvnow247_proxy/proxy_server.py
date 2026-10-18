import os
import re
import io
import time
import json
import base64
import logging
import asyncio
from typing import Optional, List, Dict
from urllib.parse import urlparse, quote, unquote

import httpx
from fastapi import FastAPI, Request, Query, HTTPException, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from PIL import Image

from deobfuscator import deobfuscate_segment, decode_token, encode_token

# Configuración de logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("tvnow247_proxy")

app = FastAPI(
    title="TVNow247 IPTV Deobfuscator Proxy",
    description="Proxy HLS y desofuscador de video para canales de TVNow247 camuflados como imágenes PNG.",
    version="1.0.0"
)

# Constantes y configuración
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}
BASE_SITES = ["https://tvnow247.today", "https://tvnow247.top"]

# Caché en memoria para segmentos desofuscados (ajustado a 15 para consumir < 30MB en capas gratuitas)
SEGMENT_CACHE: Dict[str, bytes] = {}
MAX_SEGMENT_CACHE_ITEMS = int(os.environ.get("MAX_CACHE_ITEMS", 15))


# Estado global
class ProxyState:
    active_domain: str = "tvnow247.top"
    domain_last_checked: float = 0
    channels: List[dict] = []
    channels_last_loaded: float = 0

state = ProxyState()

# Cliente HTTP global reutilizable
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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Página de inicio con enlaces y documentación de uso."""
    base_url = str(request.base_url).rstrip("/")
    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>TVNow247 Proxy & Deobfuscator</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem; margin: 0; line-height: 1.6; }}
            .container {{ max-width: 900px; margin: 0 auto; background: #1e293b; border-radius: 12px; padding: 2rem; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
            h1 {{ color: #38bdf8; margin-top: 0; }}
            .badge {{ display: inline-block; padding: 0.25rem 0.75rem; border-radius: 9999px; font-size: 0.875rem; font-weight: 600; background: #0284c7; color: white; margin-bottom: 1rem; }}
            code {{ background: #0f172a; padding: 0.2rem 0.5rem; border-radius: 4px; color: #f43f5e; font-size: 0.95em; word-break: break-all; }}
            pre {{ background: #0f172a; padding: 1rem; border-radius: 8px; overflow-x: auto; color: #e2e8f0; border: 1px solid #334155; }}
            .card {{ background: #334155; border-radius: 8px; padding: 1.25rem; margin-top: 1.5rem; }}
            a {{ color: #38bdf8; text-decoration: none; font-weight: 500; }}
            a:hover {{ text-decoration: underline; }}
            ul {{ padding-left: 1.2rem; }}
            li {{ margin-bottom: 0.5rem; }}
        </style>
    </head>
    <body>
        <div class="container">
            <span class="badge">Proxy Activo • {state.active_domain}</span>
            <h1>TVNow247 IPTV Proxy & Deobfuscator</h1>
            <p>Este proxy intercepta las listas HLS de TVNow247, descarga los segmentos camuflados como imágenes PNG desde TikTok CDN, extrae la cabecera <code>TIKTIKPX</code>, descomprime la señal con GZIP y entrega un flujo limpio <strong>MPEG-TS</strong> compatible con cualquier reproductor IPTV.</p>

            <div class="card">
                <h3>📺 Reproducción Rápida de Canales</h3>
                <ul>
                    <li><strong>Lista M3U Completa:</strong> <a href="{base_url}/channels.m3u" target="_blank">{base_url}/channels.m3u</a> (Importa esta URL en TiviMate, VLC, OTT Navigator o Kodi)</li>
                    <li><strong>Canal Específico (ej. 652):</strong> <code>{base_url}/live/652.m3u8</code></li>
                    <li><strong>Probar URL Directa:</strong> <code>{base_url}/proxy?url=TU_URL_DE_PLAYLIST</code></li>
                </ul>
            </div>

            <div class="card">
                <h3>🛠️ Ejemplos de uso con reproductores</h3>
                <p><strong>En VLC Media Player:</strong></p>
                <pre>vlc "{base_url}/live/652.m3u8"</pre>
                <p><strong>En FFplay / MPV:</strong></p>
                <pre>mpv "{base_url}/live/652.m3u8"</pre>
            </div>

            <div class="card">
                <h3>ℹ️ Estado del Sistema</h3>
                <p>Canales indexados en memoria: <strong>{len(state.channels)}</strong></p>
                <p>Dominio upstream activo: <strong>{state.active_domain}</strong></p>
                <p>Verificación de salud: <a href="{base_url}/health">{base_url}/health</a></p>
            </div>
        </div>
    </body>
    </html>
    """


@app.get("/health")
async def health():
    """Endpoint de salud para comprobaciones de Docker."""
    return {
        "status": "healthy",
        "active_domain": state.active_domain,
        "channels_count": len(state.channels),
        "cached_segments": len(SEGMENT_CACHE)
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


@app.get("/live/{channel_id}.m3u8")
async def get_live_playlist(channel_id: str, request: Request):
    """Obtiene y reescribe la lista M3U8 para un canal específico."""
    global http_client
    if not http_client:
        raise HTTPException(status_code=500, detail="HTTP client no inicializado")

    base_url = str(request.base_url).rstrip("/")
    token = encode_token(channel_id)
    
    # Probar dominios proxy candidatos en orden de prioridad
    candidate_domains = [state.active_domain, "chunk.tvnow247.today", "tvnow247.top", "tvnow247.today"]
    seen_domains = set()
    candidate_domains = [d for d in candidate_domains if d and not (d in seen_domains or seen_domains.add(d))]

    r = None
    last_res = None
    for d in candidate_domains:
        upstream_url = f"https://{d}/api/proxy/playlist?token={token}"
        try:
            res = await http_client.get(upstream_url, headers={**HEADERS, "Referer": f"https://{d}/"}, timeout=5.0)
            last_res = res
            if res.status_code == 200 and "#EXTM3U" in res.text:
                r = res
                if d != state.active_domain:
                    logger.info(f"Cambiando dominio activo a {d}")
                    state.active_domain = d
                break
            # Si el backend upstream ya respondió que la fuente está caída, no hace falta reintentar dominios espejo
            if res.status_code in (403, 502) and "upstream error" in res.text.lower():
                break
        except Exception as ex:
            logger.warning(f"Error consultando upstream {d}: {ex}")

    # Si ninguno de los dominios directos funcionó, intentar fallback a resolver API
    if not r or r.status_code != 200 or "#EXTM3U" not in r.text:
        try:
            fallback_res = await http_client.get(f"https://chat.cfbu247.sbs/api/resolve-dlstream/{channel_id}", timeout=6.0)
            if fallback_res.status_code == 200:
                data = fallback_res.json()
                alt_url = data.get("proxyPlaylistUrl") or data.get("m3u8")
                ref = data.get("referer") or f"https://{state.active_domain}/"
                if alt_url:
                    fb_r = await http_client.get(alt_url, headers={**HEADERS, "Referer": ref}, timeout=6.0)
                    last_res = fb_r
                    if fb_r.status_code == 200 and "#EXTM3U" in fb_r.text:
                        r = fb_r
        except Exception as ex:
            logger.warning(f"Error consultando API resolve fallback para canal {channel_id}: {ex}")

    if not r or r.status_code != 200 or "#EXTM3U" not in r.text:
        status_code = last_res.status_code if last_res else (r.status_code if r else 502)
        resp_text = (last_res.text if last_res else (r.text if r else "")).strip().lower()
        err_detail = f"Upstream no devolvió una lista válida (estado {status_code})"
        if "upstream error" in resp_text or "unavailable" in resp_text or status_code in (403, 502):
            err_detail += f": el canal {channel_id} está temporalmente inactivo o caído en los servidores de TVNow247"
        raise HTTPException(status_code=502, detail=err_detail)

    # Reescribir las URLs de los segmentos para que pasen por /segment de forma segura con base64
    rewritten_lines = []
    for line in r.text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Descartar comentarios no oficiales
        if line.startswith("## by HACKERMAN"):
            continue
        if line.startswith("#"):
            rewritten_lines.append(line)
        else:
            # Codificar la URL del segmento en base64 url-safe para evitar problemas de escape de caracteres (%2F, +, =)
            b64_seg = base64.urlsafe_b64encode(line.encode("utf-8")).decode("utf-8")
            local_seg_url = f"{base_url}/segment?b64={b64_seg}"
            rewritten_lines.append(local_seg_url)

    m3u8_content = "\n".join(rewritten_lines) + "\n"
    return Response(
        content=m3u8_content,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Access-Control-Allow-Origin": "*"
        }
    )


@app.get("/proxy")
async def proxy_direct_url(url: str = Query(..., description="URL de playlist upstream"), request: Request = None):
    """
    Permite pasar directamente una URL como https://chunk.tvnow247.today/api/proxy/playlist?token=...
    Detecta automáticamente el canal y regenera el token si es necesario.
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
            # Redirigir o delegar directamente en /live/{cid}.m3u8
            return await get_live_playlist(cid, request)

    # Si no tiene token pero es una URL M3U8 genérica
    global http_client
    r = await http_client.get(url)
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="Error al consultar URL")

    rewritten_lines = []
    for line in r.text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            rewritten_lines.append(line)
        else:
            b64_seg = base64.urlsafe_b64encode(line.encode("utf-8")).decode("utf-8")
            rewritten_lines.append(f"{base_url}/segment?b64={b64_seg}")

    return Response(
        content="\n".join(rewritten_lines) + "\n",
        media_type="application/vnd.apple.mpegurl",
        headers={"Access-Control-Allow-Origin": "*"}
    )


@app.get("/segment")
async def get_segment(
    b64: Optional[str] = Query(None, description="URL en Base64 seguro para URL"),
    url: Optional[str] = Query(None, description="URL directa sin doble unquote")
):
    """Descarga el fragmento, desofusca el formato PNG/TIKTIKPX y devuelve video MPEG-TS."""
    global http_client, SEGMENT_CACHE
    if not http_client:
        raise HTTPException(status_code=500, detail="HTTP client no inicializado")

    if b64:
        try:
            raw_url = base64.urlsafe_b64decode(b64.encode("utf-8")).decode("utf-8")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Parámetro b64 inválido: {e}")
    elif url:
        raw_url = url
    else:
        raise HTTPException(status_code=400, detail="Falta el parámetro de segmento (b64 o url)")

    # Revisar si ya está en caché
    if raw_url in SEGMENT_CACHE:
        return Response(
            content=SEGMENT_CACHE[raw_url],
            media_type="video/mp2t",
            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=3600"}
        )

    # Intentar descargar con reintentos para mitigar fallos transitorios de CDN
    raw_bytes = None
    last_err = None
    for attempt in range(3):
        try:
            r = await http_client.get(raw_url, headers=HEADERS, timeout=8.0)
            if r.status_code == 200:
                raw_bytes = r.content
                break
            last_err = f"HTTP {r.status_code}"
            logger.warning(f"Intento {attempt+1}/3: Falló descarga de fragmento ({last_err})")
        except Exception as e:
            last_err = str(e)
            logger.warning(f"Intento {attempt+1}/3: Error descargando fragmento: {e}")
        if attempt < 2:
            await asyncio.sleep(0.3)

    if raw_bytes is None:
        logger.error(f"Error definitivo procesando segmento ({last_err}): {raw_url}")
        raise HTTPException(status_code=502, detail=f"Error al descargar fragmento de CDN: {last_err}")

    try:
        # Desofuscar si es un PNG
        ts_bytes = deobfuscate_segment(raw_bytes)

        # Almacenar en caché LRU básica
        if len(SEGMENT_CACHE) >= MAX_SEGMENT_CACHE_ITEMS:
            first_key = next(iter(SEGMENT_CACHE))
            del SEGMENT_CACHE[first_key]

        SEGMENT_CACHE[raw_url] = ts_bytes

        return Response(
            content=ts_bytes,
            media_type="video/mp2t",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=3600"
            }
        )

    except Exception as e:
        logger.error(f"Error desofuscando segmento: {e}")
        raise HTTPException(status_code=500, detail=f"Error al desofuscar segmento: {e}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("proxy_server:app", host="0.0.0.0", port=port, reload=False)
