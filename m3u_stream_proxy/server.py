import os
import re
import time
import asyncio
import logging
from typing import Optional, List, Dict
from urllib.parse import quote, unquote

import httpx
from fastapi import FastAPI, Request, Query, HTTPException, Response, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from playlist_manager import PlaylistManager
from github_scanner import scan_github_repository, normalize_github_url
from acestream_proxy import AceStreamProxy
from cors_hls_proxy import proxy_m3u8_manifest, proxy_segment_request

# Configuración de logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("m3u_stream_proxy")

# Rutas de datos y listas locales
DATA_DIR = os.environ.get("DATA_DIR", "data")
LOCAL_PLAYLISTS_DIR = os.environ.get("LOCAL_PLAYLISTS_DIR", "local_playlists")

app = FastAPI(
    title="M3U Stream Hub & AceStream Proxy PRO",
    description="Centro multimedia y proxy HLS / AceStream con soporte multi-GitHub y reproductor web.",
    version="1.0.0"
)

# Permitir CORS globalmente
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Instancias principales
manager = PlaylistManager(data_dir=DATA_DIR, local_playlists_dir=LOCAL_PLAYLISTS_DIR)
ace_proxy = AceStreamProxy(lambda: manager.settings.get("acestream_engine_url", "http://host.docker.internal:6878"))

# Ruta al archivo de plantilla HTML
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
INDEX_HTML_PATH = os.path.join(TEMPLATES_DIR, "index.html")


# ---------------------------------------------------------------------------
# Modelos Pydantic para peticiones
# ---------------------------------------------------------------------------
class AddSourcePayload(BaseModel):
    name: str
    type: str  # 'github', 'url', 'local'
    url_or_path: str
    enabled: bool = True


class GithubScanPayload(BaseModel):
    repo: str


class GithubImportPayload(BaseModel):
    files: List[Dict]


class SettingsPayload(BaseModel):
    acestream_engine_url: str
    buffer_offset_seconds: float = 16.0
    auto_sync_hours: int = 6
    github_token: Optional[str] = ""


# ---------------------------------------------------------------------------
# Tareas de fondo y Ciclo de vida
# ---------------------------------------------------------------------------
async def background_sync_worker():
    """Sincroniza periódicamente las listas M3U activas."""
    while True:
        try:
            sync_hours = manager.settings.get("auto_sync_hours", 6)
            sleep_seconds = max(3600, sync_hours * 3600)
            await asyncio.sleep(sleep_seconds)
            logger.info("Iniciando auto-sincronización periódica de listas M3U...")
            await manager.sync_all_enabled_sources()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error en auto-sincronización en segundo plano: {e}")
            await asyncio.sleep(600)


@app.on_event("startup")
async def startup_event():
    logger.info("Iniciando M3U Stream Hub & AceStream Proxy...")
    
    # Si no hay fuentes configuradas, revisar si existen listas locales y añadir 'canales_acestream.m3u' si existe
    if not manager.sources:
        local_files = manager.scan_local_playlists()
        for f in local_files:
            if "acestream" in f["name"].lower():
                logger.info(f"Detectada lista local inicial: {f['name']}. Añadiéndola como fuente por defecto...")
                await manager.add_source(
                    name=f["name"],
                    source_type="local",
                    url_or_path=f["path"],
                    enabled=True
                )
                break

    # Sincronizar fuentes activas al inicio
    asyncio.create_task(manager.sync_all_enabled_sources())
    # Iniciar worker periódico
    asyncio.create_task(background_sync_worker())


# ---------------------------------------------------------------------------
# Rutas de la Interfaz Web
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    if not os.path.exists(INDEX_HTML_PATH):
        raise HTTPException(status_code=500, detail="index.html no encontrado")
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "channels_loaded": len(manager.channels),
        "sources_count": len(manager.sources)
    }


# ---------------------------------------------------------------------------
# API REST: Canales, Grupos y Favoritos
# ---------------------------------------------------------------------------
@app.get("/api/channels")
async def get_channels(
    search: Optional[str] = Query(None),
    source_id: Optional[str] = Query(None),
    group: Optional[str] = Query(None),
    stream_type: Optional[str] = Query(None),
    favorite_only: bool = Query(False),
    page: int = Query(1, ge=1),
    limit: int = Query(60, ge=1, le=500)
):
    result = manager.query_channels(
        search=search,
        source_id=source_id,
        group=group,
        stream_type=stream_type,
        favorite_only=favorite_only,
        page=page,
        limit=limit
    )
    return JSONResponse(result)


@app.get("/api/channels/{channel_id}")
async def get_channel_by_id(channel_id: str):
    ch = manager.get_channel_by_id(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Canal no encontrado")
    return JSONResponse(ch)


@app.get("/api/groups")
async def get_groups(source_id: Optional[str] = Query(None)):
    groups = manager.get_groups(source_id=source_id)
    return JSONResponse(groups)


@app.get("/api/favorites")
async def get_favorites():
    return JSONResponse(list(manager.favorites))


@app.post("/api/favorites/{channel_id}")
async def toggle_favorite(channel_id: str):
    is_fav = manager.toggle_favorite(channel_id)
    return JSONResponse({"channel_id": channel_id, "is_favorite": is_fav})


# ---------------------------------------------------------------------------
# API REST: Fuentes M3U y Listas Locales
# ---------------------------------------------------------------------------
@app.get("/api/sources")
async def get_sources():
    return JSONResponse(manager.get_sources_summary())


@app.post("/api/sources")
async def add_source(payload: AddSourcePayload):
    new_src = await manager.add_source(
        name=payload.name,
        source_type=payload.type,
        url_or_path=payload.url_or_path,
        enabled=payload.enabled
    )
    return JSONResponse(new_src)


@app.post("/api/sources/{source_id}/toggle")
async def toggle_source(source_id: str):
    try:
        new_state = await manager.toggle_source(source_id)
        return JSONResponse({"source_id": source_id, "enabled": new_state})
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/sources/{source_id}/sync")
async def sync_source(source_id: str, background_tasks: BackgroundTasks):
    if source_id not in manager.sources:
        raise HTTPException(status_code=404, detail="Fuente no encontrada")
    background_tasks.add_task(manager.sync_source, source_id)
    return JSONResponse({"status": "sync_started", "source_id": source_id})


@app.delete("/api/sources/{source_id}")
async def delete_source(source_id: str):
    await manager.remove_source(source_id)
    return JSONResponse({"status": "deleted", "source_id": source_id})


@app.get("/api/local/scan")
async def scan_local_playlists():
    files = manager.scan_local_playlists()
    return JSONResponse(files)


# ---------------------------------------------------------------------------
# API REST: Escaneo e Importación de GitHub
# ---------------------------------------------------------------------------
@app.post("/api/github/scan")
async def scan_github(payload: GithubScanPayload):
    result = await scan_github_repository(
        repo_input=payload.repo,
        github_token=manager.settings.get("github_token")
    )
    return JSONResponse(result)


@app.post("/api/github/import")
async def import_github_files(payload: GithubImportPayload):
    imported = []
    for f in payload.files:
        name = f.get("filename", "GitHub Playlist")
        raw_url = f.get("raw_url", "")
        if raw_url:
            src = await manager.add_source(
                name=f"{name}",
                source_type="github",
                url_or_path=raw_url,
                enabled=True
            )
            imported.append(src)
    return JSONResponse({"status": "imported", "imported_count": len(imported)})


# ---------------------------------------------------------------------------
# API REST: Ajustes y Estado de AceStream Engine
# ---------------------------------------------------------------------------
@app.get("/api/settings")
async def get_settings():
    return JSONResponse(manager.settings)


@app.post("/api/settings")
async def update_settings(payload: SettingsPayload):
    manager.settings["acestream_engine_url"] = payload.acestream_engine_url.strip()
    manager.settings["buffer_offset_seconds"] = payload.buffer_offset_seconds
    manager.settings["auto_sync_hours"] = payload.auto_sync_hours
    manager.settings["github_token"] = payload.github_token.strip() if payload.github_token else ""
    manager.save_settings()
    return JSONResponse(manager.settings)


@app.get("/api/acestream/check")
async def check_acestream_status():
    result = await ace_proxy.check_engine_health()
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Proxy de Streaming HLS (CORS & Búfer PRO)
# ---------------------------------------------------------------------------
@app.get("/stream/proxy.m3u8")
async def stream_proxy_m3u8(request: Request, url: str = Query(...)):
    """CORS Proxy para manifiestos HLS remotos."""
    target_url = unquote(url)
    base_url = str(request.base_url).rstrip("/")
    offset = manager.settings.get("buffer_offset_seconds", 16.0)
    return await proxy_m3u8_manifest(target_url, request_base_url=base_url, buffer_offset=offset)


@app.get("/stream/segment")
async def stream_proxy_segment(request: Request, url: str = Query(...)):
    """Proxy para fragmentos de video (.ts, .m4s) con caché en RAM."""
    target_url = unquote(url)
    return await proxy_segment_request(target_url, request)


# ---------------------------------------------------------------------------
# Proxy de AceStream (HLS & Direct TS)
# ---------------------------------------------------------------------------
@app.get("/ace/manifest/{acestream_id}.m3u8")
async def stream_ace_manifest(request: Request, acestream_id: str):
    """Proxy de manifiesto HLS generado por el motor AceStream."""
    base_url = str(request.base_url).rstrip("/")
    return await ace_proxy.get_hls_manifest(acestream_id, request_base_url=base_url)


@app.get("/ace/segment")
async def stream_ace_segment(request: Request, url: str = Query(...)):
    """Proxy de fragmento AceStream con soporte CORS."""
    target_url = unquote(url)
    return await proxy_segment_request(target_url, request)


@app.get("/ace/stream/{acestream_id}")
async def stream_ace_ts(acestream_id: str):
    """Proxy directo de flujo MPEG-TS desde /ace/getstream."""
    return await ace_proxy.stream_direct_ts(acestream_id)


# ---------------------------------------------------------------------------
# Exportador de Lista M3U Unificada
# ---------------------------------------------------------------------------
@app.get("/playlist.m3u", response_class=PlainTextResponse)
async def export_m3u_playlist(
    request: Request,
    source_id: Optional[str] = Query(None),
    stream_type: Optional[str] = Query(None),
    group: Optional[str] = Query(None)
):
    """
    Genera un archivo M3U unificado con todos los canales (o filtrados)
    con URLs proxificadas listas para TiviMate, OTT Navigator, Kodi, VLC, etc.
    """
    base_url = str(request.base_url).rstrip("/")
    lines = ["#EXTM3U"]

    for ch in manager.channels.values():
        if source_id and ch.get("source_id") != source_id:
            continue
        if stream_type and ch.get("stream_type") != stream_type:
            continue
        if group and ch.get("group") != group:
            continue

        logo = ch.get("tvg_logo", "")
        tvg_id = ch.get("tvg_id", "")
        grp = ch.get("group", "General")
        name = ch.get("name", "Canal")

        extinf = f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{name}" tvg-logo="{logo}" group-title="{grp}", {name}'
        lines.append(extinf)

        if ch.get("acestream_id"):
            # Generar URL HLS proxificada del canal AceStream
            play_url = f"{base_url}/ace/manifest/{ch['acestream_id']}.m3u8"
        elif ch.get("stream_type") == "hls":
            play_url = f"{base_url}/stream/proxy.m3u8?url={quote(ch['url'], safe='')}"
        else:
            play_url = ch.get("url", "")

        lines.append(play_url)

    m3u_text = "\n".join(lines)
    return PlainTextResponse(
        content=m3u_text,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Content-Disposition": 'inline; filename="m3u_hub_playlist.m3u"'
        }
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8899))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)
