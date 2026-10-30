import os
import re
import time
import asyncio
import logging
from typing import Optional, List, Dict
from urllib.parse import quote, unquote

import httpx
from fastapi import FastAPI, Request, Query, HTTPException, Response, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse, RedirectResponse
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
ace_proxy = AceStreamProxy(lambda: manager.settings)

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


class AceServerPayload(BaseModel):
    id: Optional[str] = None
    name: str
    url: str
    is_default: bool = False


class SetActiveServerPayload(BaseModel):
    server_id: str


class DirectPlayPayload(BaseModel):
    identifier: str
    name: Optional[str] = ""
    save_favorite: bool = False
    server_id: Optional[str] = None


class SettingsPayload(BaseModel):
    acestream_engine_url: str
    buffer_offset_seconds: float = 16.0
    auto_sync_hours: int = 6
    github_token: Optional[str] = ""
    auto_failover_acestream: Optional[bool] = True
    active_acestream_server_id: Optional[str] = None


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
        try:
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
        except Exception as e:
            logger.error(f"Error cargando lista local inicial por defecto: {e}")

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
async def get_channel_by_id(channel_id: str, request: Request):
    ch = manager.get_channel_by_id(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Canal no encontrado")
    base_url = str(request.base_url).rstrip("/")
    res = dict(ch)
    res["live_url"] = f"{base_url}/live/{channel_id}.m3u8"
    if ch.get("acestream_id"):
        res["ts_url"] = f"{base_url}/ace/stream/{ch['acestream_id']}"
        res["ace_uri"] = f"acestream://{ch['acestream_id']}"
    return JSONResponse(res)


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
    try:
        new_src = await manager.add_source(
            name=payload.name,
            source_type=payload.type,
            url_or_path=payload.url_or_path,
            enabled=payload.enabled
        )
        return JSONResponse(new_src)
    except Exception as e:
        logger.error(f"Error en add_source: {e}")
        raise HTTPException(status_code=400, detail=f"No se pudo añadir la fuente: {str(e)}")


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
    errors = []
    for f in payload.files:
        name = f.get("filename", "GitHub Playlist")
        raw_url = f.get("raw_url", "")
        if raw_url:
            try:
                src = await manager.add_source(
                    name=f"{name}",
                    source_type="github",
                    url_or_path=raw_url,
                    enabled=True
                )
                imported.append(src)
            except Exception as e:
                logger.error(f"Error importando {name}: {e}")
                errors.append(f"{name}: {str(e)}")
    return JSONResponse({
        "status": "imported",
        "imported_count": len(imported),
        "errors": errors
    })


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
    if payload.auto_failover_acestream is not None:
        manager.settings["auto_failover_acestream"] = payload.auto_failover_acestream
    if payload.active_acestream_server_id:
        manager.settings["active_acestream_server_id"] = payload.active_acestream_server_id
    manager.save_settings()
    return JSONResponse(manager.settings)


@app.get("/api/acestream/check")
async def check_acestream_status(server_id: Optional[str] = Query(None)):
    result = await ace_proxy.check_engine_health(server_id=server_id)
    return JSONResponse(result)


@app.get("/api/acestream/servers")
async def get_acestream_servers():
    """Devuelve todos los servidores AceStream configurados con su estado en vivo."""
    servers = await ace_proxy.check_all_servers()
    return JSONResponse({
        "servers": servers,
        "active_server_id": manager.settings.get("active_acestream_server_id", "direct"),
        "auto_failover": manager.settings.get("auto_failover_acestream", True)
    })


@app.post("/api/acestream/servers")
async def add_or_update_acestream_server(payload: AceServerPayload):
    """Añade o actualiza un servidor de AceStream (ej: con o sin VPN)."""
    servers = manager.settings.get("acestream_servers", [])
    srv_id = payload.id or f"srv_{int(time.time())}"
    clean_url = payload.url.strip().rstrip("/")
    if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
        clean_url = f"http://{clean_url}"

    existing = next((s for s in servers if s.get("id") == srv_id), None)
    if existing:
        existing["name"] = payload.name.strip()
        existing["url"] = clean_url
        if payload.is_default:
            for s in servers:
                s["is_default"] = (s.get("id") == srv_id)
            manager.settings["active_acestream_server_id"] = srv_id
            manager.settings["acestream_engine_url"] = clean_url
    else:
        if payload.is_default:
            for s in servers:
                s["is_default"] = False
            manager.settings["active_acestream_server_id"] = srv_id
            manager.settings["acestream_engine_url"] = clean_url
        servers.append({
            "id": srv_id,
            "name": payload.name.strip(),
            "url": clean_url,
            "is_default": payload.is_default or len(servers) == 0
        })

    manager.settings["acestream_servers"] = servers
    manager.save_settings()
    return JSONResponse({
        "status": "ok",
        "servers": await ace_proxy.check_all_servers(),
        "active_server_id": manager.settings.get("active_acestream_server_id", "direct")
    })


@app.delete("/api/acestream/servers/{server_id}")
async def delete_acestream_server(server_id: str):
    """Elimina un servidor de AceStream si hay más de uno."""
    servers = manager.settings.get("acestream_servers", [])
    if len(servers) <= 1:
        raise HTTPException(status_code=400, detail="No puedes eliminar el único servidor configurado")

    manager.settings["acestream_servers"] = [s for s in servers if s.get("id") != server_id]
    if manager.settings.get("active_acestream_server_id") == server_id:
        new_active = manager.settings["acestream_servers"][0]
        manager.settings["active_acestream_server_id"] = new_active["id"]
        manager.settings["acestream_engine_url"] = new_active["url"]

    manager.save_settings()
    return JSONResponse({
        "status": "ok",
        "servers": await ace_proxy.check_all_servers(),
        "active_server_id": manager.settings.get("active_acestream_server_id")
    })


@app.post("/api/acestream/active")
async def set_active_acestream_server(payload: SetActiveServerPayload):
    """Cambia en caliente el servidor AceStream activo."""
    servers = manager.settings.get("acestream_servers", [])
    target = next((s for s in servers if s.get("id") == payload.server_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Servidor AceStream no encontrado")

    manager.settings["active_acestream_server_id"] = payload.server_id
    manager.settings["acestream_engine_url"] = target["url"]
    manager.save_settings()
    return JSONResponse({
        "status": "ok",
        "active_server_id": payload.server_id,
        "server": target
    })


@app.post("/api/channels/direct-play")
async def direct_play_stream(payload: DirectPlayPayload, request: Request):
    """
    Permite pegar directamente un AceStream ID, URI o URL HLS y empezar a reproducirlo.
    """
    try:
        ch = manager.add_direct_channel(
            identifier=payload.identifier,
            name=payload.name,
            save_favorite=payload.save_favorite,
            server_id=payload.server_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    base_url = str(request.base_url).rstrip("/")
    res = dict(ch)
    res["live_url"] = f"{base_url}/live/{ch['id']}.m3u8"
    if ch.get("acestream_id"):
        res["ts_url"] = f"{base_url}/ace/stream/{ch['acestream_id']}"
        res["ace_uri"] = f"acestream://{ch['acestream_id']}"
    return JSONResponse(res)


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
async def stream_ace_manifest(request: Request, acestream_id: str, server_id: Optional[str] = Query(None)):
    """Proxy de manifiesto HLS generado por el motor AceStream."""
    base_url = str(request.base_url).rstrip("/")
    return await ace_proxy.get_hls_manifest(acestream_id, request_base_url=base_url, server_id=server_id)


@app.get("/ace/segment")
async def stream_ace_segment(request: Request, url: str = Query(...), server_id: Optional[str] = Query(None)):
    """Proxy de fragmento AceStream con soporte CORS."""
    target_url = unquote(url)
    return await proxy_segment_request(target_url, request)


@app.get("/ace/stream/{acestream_id}")
async def stream_ace_ts(acestream_id: str, server_id: Optional[str] = Query(None)):
    """Proxy directo de flujo MPEG-TS desde /ace/getstream."""
    return await ace_proxy.stream_direct_ts(acestream_id, server_id=server_id)


# ---------------------------------------------------------------------------
# Endpoints de Streaming Directo por Canal (/live/{channel_id})
# ---------------------------------------------------------------------------
@app.get("/live/{channel_id}.m3u8")
@app.head("/live/{channel_id}.m3u8")
async def get_channel_live_m3u8(
    channel_id: str,
    request: Request,
    offset: Optional[float] = Query(None),
    server_id: Optional[str] = Query(None)
):
    """
    Ruta directa para reproducir cualquier canal en reproductores externos
    (VLC, Kodi, TiviMate, Smart TV) o en la web.
    """
    ch = manager.channels.get(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Canal no encontrado")

    base_url = str(request.base_url).rstrip("/")
    if ch.get("acestream_id"):
        target_server = server_id or ch.get("acestream_server_id")
        return await ace_proxy.get_hls_manifest(ch["acestream_id"], request_base_url=base_url, server_id=target_server)

    target_url = ch.get("url")
    if not target_url:
        raise HTTPException(status_code=400, detail="El canal no tiene URL válida")

    buf_offset = offset if offset is not None else manager.settings.get("buffer_offset_seconds", 16.0)
    return await proxy_m3u8_manifest(target_url, request_base_url=base_url, buffer_offset=buf_offset)


@app.get("/live/{channel_id}.ts")
@app.get("/live/{channel_id}")
async def get_channel_live_ts(channel_id: str, request: Request, server_id: Optional[str] = Query(None)):
    """
    Ruta directa para flujo MPEG-TS directo (óptimo para VLC, Kodi o decodificadores).
    """
    ch = manager.channels.get(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Canal no encontrado")

    if ch.get("acestream_id"):
        target_server = server_id or ch.get("acestream_server_id")
        return await ace_proxy.stream_direct_ts(ch["acestream_id"], server_id=target_server)

    target_url = ch.get("url")
    if not target_url:
        raise HTTPException(status_code=400, detail="El canal no tiene URL válida")

    return RedirectResponse(url=target_url)

    return RedirectResponse(url=target_url)


# ---------------------------------------------------------------------------
# Exportador de Lista M3U Unificada
# ---------------------------------------------------------------------------
@app.get("/playlist.m3u", response_class=PlainTextResponse)
@app.get("/channels.m3u", response_class=PlainTextResponse)
async def export_m3u_playlist(
    request: Request,
    source_id: Optional[str] = Query(None),
    stream_type: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    group: Optional[str] = Query(None)
):
    """
    Genera un archivo M3U unificado con todos los canales (o filtrados)
    con URLs proxificadas listas para TiviMate, OTT Navigator, Kodi, VLC, etc.
    """
    filter_type = stream_type or type
    base_url = str(request.base_url).rstrip("/")
    lines = ["#EXTM3U"]

    for ch in manager.channels.values():
        if source_id and ch.get("source_id") != source_id:
            continue
        if filter_type:
            ft = filter_type.lower()
            if ft in ("ace", "acestream") and not ch.get("acestream_id"):
                continue
            elif ft == "hls" and (ch.get("stream_type") != "hls" or ch.get("acestream_id")):
                continue
            elif ft not in ("ace", "acestream", "hls") and ch.get("stream_type") != filter_type:
                continue
        if group and ch.get("group") != group:
            continue

        logo = ch.get("tvg_logo", "")
        tvg_id = ch.get("tvg_id", "")
        grp = ch.get("group", "General")
        name = ch.get("name", "Canal")

        extinf = f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{name}" tvg-logo="{logo}" group-title="{grp}", {name}'
        lines.append(extinf)

        play_url = f"{base_url}/live/{ch['id']}.m3u8"
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
