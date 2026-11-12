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


def find_index_html() -> Optional[str]:
    candidate_paths = [
        INDEX_HTML_PATH,
        os.path.join(os.path.dirname(__file__), "templates", "index.html"),
        "/app/templates/index.html",
        "/opt/templates/index.html",
        os.path.join(os.getcwd(), "templates", "index.html"),
        os.path.join(LOCAL_PLAYLISTS_DIR, "m3u_stream_proxy", "templates", "index.html"),
        os.path.join(os.path.dirname(__file__), "index.html"),
        "/app/index.html",
    ]
    for p in candidate_paths:
        if p and os.path.isfile(p):
            try:
                if os.path.getsize(p) > 100:
                    return p
            except Exception:
                pass
    return None


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
    repo_id: Optional[str] = None


class SaveGithubRepoPayload(BaseModel):
    url: str
    scan_now: bool = True


class UpdateGithubRepoPayload(BaseModel):
    reimport_all: bool = True


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


class HDHomeRunSettingsPayload(BaseModel):
    hdhomerun_enabled: Optional[bool] = None
    hdhomerun_name: Optional[str] = None
    hdhomerun_tuners: Optional[int] = None
    hdhomerun_mode: Optional[str] = None
    hdhomerun_group: Optional[str] = None
    hdhomerun_source_id: Optional[str] = None
    hdhomerun_max_channels: Optional[int] = None


# ---------------------------------------------------------------------------
# Tareas de fondo y Ciclo de vida
# ---------------------------------------------------------------------------
async def background_sync_worker():
    """Sincroniza periódicamente las listas M3U activas y repositorios GitHub."""
    while True:
        try:
            sync_hours = manager.settings.get("auto_sync_hours", 6)
            sleep_seconds = max(3600, sync_hours * 3600)
            await asyncio.sleep(sleep_seconds)
            logger.info("Iniciando auto-sincronización periódica de listas M3U y repositorios...")
            # Sincronizar repositorios GitHub guardados para descubrir nuevos archivos o cambios
            for repo_id in list(manager.github_repos.keys()):
                try:
                    logger.info(f"Auto-sincronizando repositorio GitHub: {repo_id}")
                    await manager.update_github_repo(repo_id, reimport_all=True)
                except Exception as e:
                    logger.warning(f"Error auto-sincronizando repo GitHub {repo_id}: {e}")
            await manager.sync_all_enabled_sources()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error en auto-sincronización en segundo plano: {e}")
            await asyncio.sleep(600)


@app.on_event("startup")
async def startup_event():
    logger.info("Iniciando M3U Stream Hub & AceStream Proxy...")
    
    # 1. Auto-importar repositorios GitHub o URLs de listas M3U configuradas en variables de entorno
    try:
        await manager.auto_import_from_env()
    except Exception as e:
        logger.error(f"Error durante auto_import_from_env: {e}")

    # 2. Si no hay fuentes configuradas, revisar si existen listas locales y añadir 'canales_acestream.m3u' si existe
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
    path = find_index_html()
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
                if content and len(content.strip()) > 100:
                    return HTMLResponse(content=content)
        except Exception as e:
            logger.warning(f"Error al leer index.html desde archivo {path}: {e}")

    # Fallback embebido infalible (evita errores si el volumen del host oculta /app/templates)
    try:
        from default_template import get_embedded_index_html
        return HTMLResponse(content=get_embedded_index_html())
    except Exception as e:
        logger.error(f"Error al cargar plantilla embebida: {e}")
        raise HTTPException(status_code=500, detail="index.html no disponible")


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


@app.get("/api/channels/custom")
async def get_custom_channels():
    """Devuelve la lista de canales personalizados agregados por el usuario."""
    return JSONResponse(manager.get_custom_channels())


@app.get("/api/channels/{channel_id}")
async def get_channel_by_id(channel_id: str, request: Request):
    ch = manager.get_channel_by_id(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Canal no encontrado")
    base_url = str(request.base_url).rstrip("/")
    res = dict(ch)
    res["live_url"] = f"{base_url}/live/{channel_id}.mpd" if ch.get("stream_type") == "dash" else f"{base_url}/live/{channel_id}.m3u8"
    if ch.get("acestream_id"):
        res["ts_url"] = f"{base_url}/ace/stream/{ch['acestream_id']}"
        res["ace_uri"] = f"acestream://{ch['acestream_id']}"
        res["engine_urls"] = [
            {
                "id": s["id"],
                "name": s.get("name", s["id"]),
                "url": f"{s['url'].rstrip('/')}/ace/getstream?id={ch['acestream_id']}",
                "manifest_url": f"{s['url'].rstrip('/')}/ace/manifest.m3u8?id={ch['acestream_id']}"
            }
            for s in ace_proxy.get_server_list()
        ]
    return JSONResponse(res)


@app.get("/api/channels/{channel_id}/sources")
async def get_channel_alternative_sources(channel_id: str):
    """Devuelve transmisiones y fuentes alternativas disponibles para el mismo canal en otras listas."""
    target = manager.channels.get(channel_id)
    if not target:
        raise HTTPException(status_code=404, detail="Canal no encontrado")

    import re
    def normalize_name(n: str) -> str:
        n = (n or "").lower()
        n = re.sub(r'\(.*?\)|\[.*?\]', '', n)
        n = re.sub(r'-->.*$', '', n)
        n = re.sub(r'\b(fhd|hd|sd|4k|uhd|1080p|720p|hevc|raw|link\s*\d+|opcion\s*\d+|stream\s*\d+)\b', '', n)
        return re.sub(r'[^a-z0-9]', '', n).strip()

    target_norm = normalize_name(target.get("name", ""))
    target_tvg = (target.get("tvg_id") or "").lower().strip()

    alternatives = []
    for cid, ch in manager.channels.items():
        if cid == channel_id:
            continue

        ch_tvg = (ch.get("tvg_id") or "").lower().strip()
        ch_norm = normalize_name(ch.get("name", ""))

        matches = False
        if target_tvg and ch_tvg and target_tvg == ch_tvg:
            matches = True
        elif target_norm and len(target_norm) >= 3 and target_norm == ch_norm:
            matches = True

        if matches:
            alternatives.append({
                "id": ch["id"],
                "name": ch.get("name", "Canal"),
                "source_name": ch.get("source_name", "M3U"),
                "source_id": ch.get("source_id", ""),
                "stream_type": ch.get("stream_type", "hls"),
                "is_acestream": bool(ch.get("acestream_id")),
                "group": ch.get("group", "General"),
                "tvg_logo": ch.get("tvg_logo", "")
            })
            if len(alternatives) >= 25:
                break

    return JSONResponse({
        "channel_id": channel_id,
        "current_name": target.get("name"),
        "total_sources": len(alternatives) + 1,
        "alternatives": alternatives
    })


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
# API REST: Gestión, Escaneo e Importación de Repositorios GitHub
# ---------------------------------------------------------------------------
@app.get("/api/github/repos")
async def get_saved_github_repos():
    repos = manager.get_github_repos()
    return JSONResponse({"success": True, "repos": repos})


@app.post("/api/github/repos")
async def add_saved_github_repo(payload: SaveGithubRepoPayload):
    try:
        repo = await manager.add_or_update_github_repo(payload.url, scan_now=payload.scan_now)
        return JSONResponse({"success": True, "repo": repo})
    except Exception as e:
        logger.error(f"Error guardando repositorio GitHub {payload.url}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.delete("/api/github/repos/{repo_id}")
async def delete_saved_github_repo(repo_id: str, delete_sources: bool = Query(False)):
    deleted = await manager.delete_github_repo(repo_id, delete_sources=delete_sources)
    if not deleted:
        raise HTTPException(status_code=404, detail="Repositorio no encontrado")
    return JSONResponse({"success": True, "repo_id": repo_id})


@app.post("/api/github/repos/{repo_id}/update")
async def update_saved_github_repo(repo_id: str, payload: Optional[UpdateGithubRepoPayload] = None):
    reimport_all = payload.reimport_all if payload else True
    try:
        res = await manager.update_github_repo(repo_id, reimport_all=reimport_all)
        return JSONResponse(res)
    except Exception as e:
        logger.error(f"Error actualizando repositorio {repo_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.post("/api/github/scan")
async def scan_github(payload: GithubScanPayload):
    token = manager.settings.get("github_token")
    result = await scan_github_repository(
        repo_input=payload.repo,
        github_token=token
    )
    if result.get("success"):
        try:
            # Auto-guardar o actualizar el repositorio en github_repos.json
            repo = await manager.add_or_update_github_repo(payload.repo, scan_now=False)
            repo["branch"] = result.get("branch", repo.get("branch", "main"))
            repo["files_count"] = result.get("files_count", 0)
            repo["files"] = result.get("files", [])
            repo["last_scanned"] = int(time.time())
            manager.save_github_repos()
            result["repo_id"] = repo["id"]
        except Exception as e:
            logger.warning(f"No se pudo auto-guardar repositorio en lista: {e}")
    return JSONResponse(result)


@app.post("/api/github/import")
async def import_github_files(payload: GithubImportPayload):
    imported = []
    errors = []
    repo = manager.github_repos.get(payload.repo_id) if payload.repo_id else None

    for f in payload.files:
        name = f.get("filename", "GitHub Playlist")
        raw_url = f.get("raw_url", "")
        fpath = f.get("path", name)
        if raw_url:
            try:
                src = await manager.add_source(
                    name=f"{name}",
                    source_type="github",
                    url_or_path=raw_url,
                    enabled=True,
                    github_repo_id=payload.repo_id,
                    github_path=fpath
                )
                imported.append(src)
                if repo and src["id"] not in repo.get("imported_sources", []):
                    if "imported_sources" not in repo:
                        repo["imported_sources"] = []
                    repo["imported_sources"].append(src["id"])
            except Exception as e:
                logger.error(f"Error importando {name}: {e}")
                errors.append(f"{name}: {str(e)}")

    if repo:
        manager.save_github_repos()

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


@app.get("/api/acestream/stats")
async def get_acestream_stream_stats(
    id: str = Query(..., description="Hash ID de AceStream"),
    server_id: Optional[str] = Query(None, description="Servidor específico a consultar")
):
    """Devuelve telemetría P2P en vivo (peers conectados, velocidad descarga/subida, búfer)."""
    stats = await ace_proxy.get_stream_stats(acestream_id=id, server_id=server_id)
    return JSONResponse(stats)


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
        res["engine_urls"] = [
            {
                "id": s["id"],
                "name": s.get("name", s["id"]),
                "url": f"{s['url'].rstrip('/')}/ace/getstream?id={ch['acestream_id']}",
                "manifest_url": f"{s['url'].rstrip('/')}/ace/manifest.m3u8?id={ch['acestream_id']}"
            }
            for s in ace_proxy.get_server_list()
        ]
    return JSONResponse(res)


@app.delete("/api/channels/{channel_id}")
async def delete_channel(channel_id: str):
    """Elimina un canal personalizado (AceStream ID o URL directa)."""
    success = manager.delete_custom_channel(channel_id)
    if not success:
        raise HTTPException(status_code=404, detail="Canal personalizado no encontrado o no es eliminable")
    return JSONResponse({"status": "deleted", "id": channel_id})


# ---------------------------------------------------------------------------
# Proxy de Streaming HLS (CORS & Búfer PRO)
# ---------------------------------------------------------------------------
@app.get("/stream/proxy.m3u8")
async def stream_proxy_m3u8(request: Request, url: str = Query(...), stream_id: Optional[str] = Query(None)):
    """CORS Proxy para manifiestos HLS remotos con soporte de ventana deslizante PRO."""
    target_url = unquote(url)
    ref = request.query_params.get("ref")
    ua = request.query_params.get("ua")
    sid = stream_id or request.query_params.get("stream_id")
    base_url = str(request.base_url).rstrip("/")
    offset = manager.settings.get("buffer_offset_seconds", 16.0)
    return await proxy_m3u8_manifest(target_url, request_base_url=base_url, buffer_offset=offset, custom_ref=ref, custom_ua=ua, stream_id=sid)


@app.get("/stream/segment")
async def stream_proxy_segment(request: Request, url: str = Query(...)):
    """Proxy para fragmentos de video (.ts, .m4s) con caché en RAM."""
    target_url = unquote(url)
    return await proxy_segment_request(target_url, request)


# ---------------------------------------------------------------------------
# Proxy de AceStream (HLS & Direct TS)
# ---------------------------------------------------------------------------
@app.get("/ace/manifest/{acestream_id}.m3u8")
async def stream_ace_manifest(
    request: Request,
    acestream_id: str,
    server_id: Optional[str] = Query(None),
    preferred_audio_language: Optional[str] = Query(None)
):
    """Proxy de manifiesto HLS generado por el motor AceStream con soporte de idioma de audio."""
    base_url = str(request.base_url).rstrip("/")
    return await ace_proxy.get_hls_manifest(
        acestream_id,
        request_base_url=base_url,
        server_id=server_id,
        preferred_audio_language=preferred_audio_language
    )


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
@app.get("/live/{channel_id}.mpd")
@app.head("/live/{channel_id}.mpd")
async def get_channel_live_mpd(channel_id: str, request: Request):
    """
    Ruta directa para flujo MPEG-DASH (.mpd), óptimo para Shaka Player, Kodi o TiviMate con ClearKey.
    """
    ch = manager.channels.get(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Canal no encontrado")
    target_url = ch.get("url", "").split("|")[0].strip()
    if not target_url:
        raise HTTPException(status_code=400, detail="El canal no tiene URL válida")
    return RedirectResponse(url=target_url)


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

    if ch.get("stream_type") == "dash":
        clean_target = target_url.split("|")[0].strip()
        return RedirectResponse(url=clean_target)

    buf_offset = offset if offset is not None else manager.settings.get("buffer_offset_seconds", 16.0)
    return await proxy_m3u8_manifest(target_url, request_base_url=base_url, buffer_offset=buf_offset, stream_id=channel_id)


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


# ---------------------------------------------------------------------------
# Emulador de Sintonizador Digital HDHomeRun (Plex, Jellyfin, Emby)
# ---------------------------------------------------------------------------
@app.get("/discover.json")
@app.head("/discover.json")
async def hdhomerun_discover(request: Request):
    """
    Protocolo de descubrimiento oficial de sintonizador SiliconDust HDHomeRun.
    Permite que Plex Live TV y Jellyfin reconozcan este contenedor como un sintonizador físico.
    """
    base_url = str(request.base_url).rstrip("/")
    tuner_count = int(manager.settings.get("hdhomerun_tuners", 4))
    friendly_name = manager.settings.get("hdhomerun_name", "M3U Stream Hub")
    device_id = "104E2026"

    query_str = str(request.url.query)
    lineup_url = f"{base_url}/lineup.json"
    if query_str:
        lineup_url = f"{lineup_url}?{query_str}"

    return JSONResponse({
        "FriendlyName": friendly_name,
        "Manufacturer": "SiliconDust",
        "ModelNumber": "HDTC-2US",
        "FirmwareName": "hdhomerun_tc_atsc",
        "FirmwareVersion": "20230505",
        "DeviceID": device_id,
        "DeviceAuth": "m3ustreamproxy2026",
        "TunerCount": tuner_count,
        "BaseURL": base_url,
        "LineupURL": lineup_url
    })


@app.get("/lineup_status.json")
@app.head("/lineup_status.json")
async def hdhomerun_lineup_status():
    """Estado de escaneo del sintonizador HDHomeRun para Plex y Jellyfin."""
    return JSONResponse({
        "ScanInProgress": 0,
        "ScanPossible": 1,
        "Source": "Cable",
        "SourceList": ["Cable", "Antenna"]
    })


@app.post("/lineup.post")
@app.get("/lineup.post")
async def hdhomerun_lineup_post():
    """Respuesta a comandos de escaneo enviados por Plex o Jellyfin."""
    return JSONResponse({"status": "ok"})


@app.get("/device.xml", response_class=Response)
@app.get("/upnp/device.xml", response_class=Response)
async def hdhomerun_device_xml(request: Request):
    """Descriptor XML UPnP / DLNA para auto-descubrimiento en red local."""
    base_url = str(request.base_url).rstrip("/")
    friendly_name = manager.settings.get("hdhomerun_name", "M3U Stream Hub")
    xml_content = f"""<?xml version="1.0" encoding="utf-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion>
    <major>1</major>
    <minor>0</minor>
  </specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>{friendly_name}</friendlyName>
    <manufacturer>SiliconDust</manufacturer>
    <modelName>HDTC-2US</modelName>
    <modelNumber>HDTC-2US</modelNumber>
    <serialNumber>104E2026</serialNumber>
    <UDN>uuid:20261031-104e-4f2b-8899-m3ustreamhub</UDN>
    <presentationURL>{base_url}</presentationURL>
  </device>
</root>"""
    return Response(content=xml_content, media_type="application/xml")


@app.get("/lineup.json")
@app.head("/lineup.json")
async def hdhomerun_lineup(
    request: Request,
    favorites: Optional[bool] = Query(None),
    group: Optional[str] = Query(None),
    source_id: Optional[str] = Query(None),
    limit: Optional[int] = Query(None)
):
    """Parrilla de canales oficial HDHomeRun para Plex y Jellyfin."""
    base_url = str(request.base_url).rstrip("/")
    lineup = manager.get_hdhomerun_lineup(
        base_url=base_url,
        favorites_only=favorites,
        group=group,
        source_id=source_id,
        limit=limit
    )
    result = [
        {
            "GuideNumber": item["GuideNumber"],
            "GuideName": item["GuideName"],
            "URL": item["URL"]
        }
        for item in lineup
    ]
    return JSONResponse(result)


async def _serve_hdhomerun_stream(
    identifier: str,
    request: Request,
    server_id: Optional[str] = None
):
    """Lógica central de sintonía HDHomeRun para Plex, Jellyfin y Emby."""
    base_url = str(request.base_url).rstrip("/")
    if not manager.hdhomerun_channel_map:
        manager.get_hdhomerun_lineup(base_url=base_url)

    target_id = None
    clean_num = identifier.lstrip("v").strip()

    # 1. Buscar por número de guía (ej. "1", "2")
    if clean_num in manager.hdhomerun_channel_map:
        target_id = manager.hdhomerun_channel_map[clean_num]
    elif identifier in manager.hdhomerun_channel_map:
        target_id = manager.hdhomerun_channel_map[identifier]
    # 2. Buscar por ID directo de canal
    elif identifier in manager.channels:
        target_id = identifier
    elif clean_num in manager.channels:
        target_id = clean_num

    if not target_id or target_id not in manager.channels:
        raise HTTPException(status_code=404, detail="Canal no encontrado en el sintonizador")

    ch = manager.channels[target_id]

    # Para peticiones HEAD (sondeos rápidos de Plex / Jellyfin para comprobar estado de sintonía)
    if request.method == "HEAD":
        media_type = "video/mp2t" if ch.get("acestream_id") else "application/vnd.apple.mpegurl"
        return Response(
            status_code=200,
            media_type=media_type,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-cache",
                "Connection": "close"
            }
        )

    # 1. Si es AceStream -> Flujo MPEG-TS continuo directo
    if ch.get("acestream_id"):
        target_server = server_id or ch.get("acestream_server_id")
        return await ace_proxy.stream_direct_ts(ch["acestream_id"], server_id=target_server)

    # 2. Si es HLS -> Servir manifiesto proxificado con offset
    target_url = ch.get("url", "")
    if ".m3u8" in target_url.lower() or ch.get("stream_type") == "hls":
        buf_offset = manager.settings.get("buffer_offset_seconds", 16.0)
        return await proxy_m3u8_manifest(target_url, request_base_url=base_url, buffer_offset=buf_offset, stream_id=target_id)

    return RedirectResponse(url=target_url)


@app.get("/auto/v{guide_num}")
@app.head("/auto/v{guide_num}")
async def hdhomerun_stream_vguide(guide_num: str, request: Request, server_id: Optional[str] = Query(None)):
    """Punto de sintonía HDHomeRun estándar con prefijo v (ej. /auto/v1)."""
    return await _serve_hdhomerun_stream(guide_num, request, server_id)


@app.get("/auto/{channel_id}")
@app.head("/auto/{channel_id}")
async def hdhomerun_stream_any(channel_id: str, request: Request, server_id: Optional[str] = Query(None)):
    """Punto de sintonía HDHomeRun por número o ID de canal (ej. /auto/1 o /auto/ch_123)."""
    return await _serve_hdhomerun_stream(channel_id, request, server_id)


@app.get("/api/hdhomerun/status")
async def get_hdhomerun_status(request: Request):
    """Devuelve el estado completo del sintonizador HDHomeRun y su parrilla para el panel web."""
    base_url = str(request.base_url).rstrip("/")
    lineup = manager.get_hdhomerun_lineup(base_url=base_url)
    return JSONResponse({
        "enabled": manager.settings.get("hdhomerun_enabled", True),
        "friendly_name": manager.settings.get("hdhomerun_name", "M3U Stream Hub"),
        "tuner_count": manager.settings.get("hdhomerun_tuners", 4),
        "mode": manager.settings.get("hdhomerun_mode", "favorites"),
        "max_channels": manager.settings.get("hdhomerun_max_channels", 300),
        "group_filter": manager.settings.get("hdhomerun_group", ""),
        "source_filter": manager.settings.get("hdhomerun_source_id", ""),
        "total_channels": len(lineup),
        "discover_url": f"{base_url}/discover.json",
        "lineup_url": f"{base_url}/lineup.json",
        "channels_sample": lineup[:10]
    })


@app.post("/api/hdhomerun/settings")
async def update_hdhomerun_settings(payload: HDHomeRunSettingsPayload):
    """Actualiza la configuración del sintonizador HDHomeRun."""
    if payload.hdhomerun_enabled is not None:
        manager.settings["hdhomerun_enabled"] = payload.hdhomerun_enabled
    if payload.hdhomerun_name is not None:
        manager.settings["hdhomerun_name"] = payload.hdhomerun_name.strip()
    if payload.hdhomerun_tuners is not None:
        manager.settings["hdhomerun_tuners"] = max(1, min(32, payload.hdhomerun_tuners))
    if payload.hdhomerun_mode is not None:
        manager.settings["hdhomerun_mode"] = payload.hdhomerun_mode
    if payload.hdhomerun_group is not None:
        manager.settings["hdhomerun_group"] = payload.hdhomerun_group
    if payload.hdhomerun_source_id is not None:
        manager.settings["hdhomerun_source_id"] = payload.hdhomerun_source_id
    if payload.hdhomerun_max_channels is not None:
        manager.settings["hdhomerun_max_channels"] = max(10, min(2000, payload.hdhomerun_max_channels))

    manager.save_settings()
    return JSONResponse({"success": True, "settings": manager.settings})


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
    group: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    favorites: Optional[bool] = Query(None),
    fav: Optional[bool] = Query(None),
    format: Optional[str] = Query(None),
    server_id: Optional[str] = Query(None),
    limit: Optional[int] = Query(None)
):
    """
    Genera un archivo M3U unificado con todos los canales (o filtrados)
    con URLs proxificadas listas para TiviMate, OTT Navigator, Kodi, VLC, etc.
    o enlaces directos al motor AceStream si format=getstream.
    Permite filtrar por:
    - ?fav=1 o ?favorites=true (Solo canales favoritos)
    - ?type=dash / acestream / hls
    - ?search=palabra (Buscar por nombre o grupo)
    - ?group=nombre_grupo
    - ?limit=100
    """
    filter_type = stream_type or type
    only_fav = bool(favorites or fav)
    base_url = str(request.base_url).rstrip("/")
    lines = ["#EXTM3U"]

    # Si se pide formato getstream o manifest directo, obtener la base del servidor solicitado
    srv_clean_url = None
    if format in ("getstream", "manifest"):
        url, _, _ = ace_proxy.get_server_info(server_id)
        srv_clean_url = url.rstrip("/")
        # Si la URL es localhost, 127.0.0.1 o host.docker.internal, traducirla a la IP/host del cliente para dispositivos externos
        try:
            parsed_srv = urlparse(srv_clean_url)
            if parsed_srv.hostname in ("127.0.0.1", "localhost", "host.docker.internal"):
                client_host = request.headers.get("host", "").split(":")[0] or request.base_url.hostname
                port_part = f":{parsed_srv.port}" if parsed_srv.port else ":6878"
                srv_clean_url = f"{parsed_srv.scheme or 'http'}://{client_host}{port_part}"
        except Exception:
            pass

    exported_count = 0
    for ch in manager.channels.values():
        if only_fav and ch.get("id") not in manager.favorites:
            continue
        if search:
            s_low = search.lower()
            ch_name = (ch.get("name") or "").lower()
            ch_grp = (ch.get("group") or "").lower()
            if s_low not in ch_name and s_low not in ch_grp:
                continue
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

        is_dash = (ch.get("stream_type") == "dash") or (ch.get("url") and ".mpd" in ch.get("url"))
        has_drm = bool(ch.get("license_key") or ch.get("license_type"))

        if is_dash or has_drm:
            lines.append("#KODIPROP:inputstream=inputstream.adaptive")
            if is_dash:
                lines.append("#KODIPROP:inputstream.adaptive.manifest_type=mpd")

            lic_key = (ch.get("license_key") or "").strip()
            lic_type = (ch.get("license_type") or "clearkey").strip()

            if "clearkey" in lic_type.lower() and lic_key:
                clean_pair = lic_key.replace("-", "").lower()
                lines.append(f"#KODIPROP:inputstream.adaptive.drm_legacy=org.w3.clearkey|{clean_pair}")
                if ":" in clean_pair:
                    kid, key = clean_pair.split(":", 1)
                    lines.append(f'#KODIPROP:inputstream.adaptive.drm={{"org.w3.clearkey":{{"keyids":{{"{kid.strip()}":"{key.strip()}"}}}}}}')
            else:
                if lic_type:
                    lines.append(f"#KODIPROP:inputstream.adaptive.license_type={lic_type}")
                if lic_key:
                    lines.append(f"#KODIPROP:inputstream.adaptive.license_key={lic_key}")

        if ch.get("acestream_id") and format == "getstream" and srv_clean_url:
            play_url = f"{srv_clean_url}/ace/getstream?id={ch['acestream_id']}"
        elif ch.get("acestream_id") and format == "manifest" and srv_clean_url:
            play_url = f"{srv_clean_url}/ace/manifest.m3u8?id={ch['acestream_id']}"
        elif ch.get("acestream_id") and format == "ts":
            play_url = f"{base_url}/ace/stream/{ch['acestream_id']}"
        elif ch.get("stream_type") == "dash":
            play_url = ch.get("url") or f"{base_url}/live/{ch['id']}.mpd"
        else:
            play_url = f"{base_url}/live/{ch['id']}.m3u8"

        lines.append(play_url)
        exported_count += 1
        if limit and exported_count >= limit:
            break

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
