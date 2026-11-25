#!/usr/bin/env python3
"""
ZicoTV Token Extractor & HLS Redirect Service
Extrae en tiempo real el token activo (con duración configurable: 20 min, 1h, 3h máx) de cualquier canal de ZicoTV.
Funciona como servicio web (FastAPI/Podman) y como herramienta de línea de comandos (CLI).
"""

import sys
import os
import re
import json
import time
import base64
import binascii
import secrets
import argparse
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

try:
    import httpx
    from fastapi import FastAPI, HTTPException, Query, Request, Response
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    FastAPI = None
    HTTPException = Exception

# Configuración base
BASE_URL = os.environ.get("ZICOTV_BASE_URL", "https://bp.zicotv.cc")
DEFAULT_DURATION = os.environ.get("DEFAULT_TOKEN_DURATION", "3h")
AUTH_USER = os.environ.get("AUTH_USER", "").strip()
AUTH_PASS = os.environ.get("AUTH_PASS", "")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# Canales fijos 24/7 de ZicoTV
CANALES_FIJOS = {
    1: {"name": "DAZN LaLiga", "group": "Fútbol"},
    2: {"name": "DAZN 1 Spain", "group": "Deportes"},
    3: {"name": "DAZN 2 Spain", "group": "Deportes"},
    4: {"name": "LaLiga TV Bar", "group": "Fútbol"},
    5: {"name": "Movistar Liga de Campeones 3", "group": "Fútbol"},
    6: {"name": "Movistar Liga de Campeones", "group": "Fútbol"},
    7: {"name": "Movistar Liga de Campeones 4", "group": "Fútbol"},
    8: {"name": "Movistar Liga de Campeones 2", "group": "Fútbol"},
    9: {"name": "Movistar #Vamos", "group": "Deportes"},
    10: {"name": "ESPN Deportes", "group": "Deportes"},
    11: {"name": "DAZN F1 Spain", "group": "Motor"},
    12: {"name": "LaLiga TV Hypermotion", "group": "Fútbol"},
    13: {"name": "Sky Sports Main Event", "group": "Deportes"},
    14: {"name": "Movistar Liga de Campeones 5", "group": "Fútbol"},
    15: {"name": "DAZN LaLiga 2", "group": "Fútbol"},
    16: {"name": "Sport TV 2", "group": "Deportes"},
    17: {"name": "Amazon Prime Video Spain", "group": "Deportes"},
    18: {"name": "Movistar Deportes 3", "group": "Deportes"},
    19: {"name": "Movistar Deportes", "group": "Deportes"},
    20: {"name": "Movistar Deportes 6", "group": "Deportes"},
    21: {"name": "UFC", "group": "Combate"},
    22: {"name": "LaLiga TV 2 por Movistar Plus", "group": "Fútbol"},
    23: {"name": "Eurosport 1 Spain", "group": "Deportes"},
    24: {"name": "LaLiga TV Hypermotion 2", "group": "Fútbol"},
    25: {"name": "NBA 1", "group": "Baloncesto"},
    28: {"name": "Movistar Plus+", "group": "General"},
    32: {"name": "Multichampions", "group": "Fútbol"},
    34: {"name": "DAZN 3 Spain", "group": "Deportes"},
    35: {"name": "Movistar Deportes 2", "group": "Deportes"},
    36: {"name": "Eurosport 2 Spain", "group": "Deportes"},
    37: {"name": "DAZN 4 Spain", "group": "Deportes"},
    38: {"name": "LaLiga TV Bar Aux", "group": "Fútbol"},
    39: {"name": "Movistar LaLiga 4", "group": "Fútbol"},
    41: {"name": "Movistar LaLiga 3", "group": "Fútbol"},
    46: {"name": "Movistar Liga de Campeones 8", "group": "Fútbol"},
    47: {"name": "Movistar Liga de Campeones 7", "group": "Fútbol"},
    48: {"name": "Movistar Liga de Campeones 6", "group": "Fútbol"},
}

def parse_duration_seconds(duration_str: Optional[str]) -> Tuple[int, bool]:
    """
    Parsea un string de duración (ej: '3h', '180m', '20m', '1h', '3600') y devuelve:
    (duracion_en_segundos, usar_native_upstream)
    - 3 horas (10800s, maximo ZicoTV): native = True
    - 20 minutos (1200s, estandar web): native = False
    - Intervalos intermedios o personalizados: native según supere los 20 min.
    """
    if not duration_str:
        duration_str = DEFAULT_DURATION

    d = str(duration_str).strip().lower()

    if d in ("max", "native", "3h", "180m", "10800"):
        return 10800, True

    if d in ("std", "standard", "web", "20m", "1200"):
        return 1200, False

    m = re.match(r"^(\d+)\s*(h|m|s|min|mins|horas?|minutos?)?$", d)
    if m:
        val = int(m.group(1))
        unit = m.group(2) or "s"
        if unit.startswith("h"):
            secs = val * 3600
        elif unit.startswith("m"):
            secs = val * 60
        else:
            secs = val
    else:
        secs = 10800

    # Limitar entre 300s (5 min) y 10800s (3 horas maximo)
    secs = max(300, min(10800, secs))
    native = secs > 1200
    return secs, native

class TokenExtractor:
    """Gestor y extractor de tokens HLS en tiempo real con TTL configurable."""

    def __init__(self):
        self.cached_tokens: Dict[Tuple[int, bool], Dict] = {}
        self.channel_catalog: Dict[int, Dict] = {}
        self.catalog_last_updated: float = 0
        self._client: Optional[httpx.AsyncClient] = None

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0, follow_redirects=True)
        return self._client

    async def fetch_channels(self, force: bool = False) -> Dict[int, Dict]:
        """Obtiene el catálogo combinado de canales fijos y eventos en vivo."""
        now = time.time()
        if not force and self.channel_catalog and (now - self.catalog_last_updated < 300):
            return self.channel_catalog

        channels = {}
        for cid, info in CANALES_FIJOS.items():
            channels[cid] = {"id": cid, "name": info["name"], "group": info["group"]}

        client = await self.get_client()
        try:
            r = await client.get(f"{BASE_URL}/api/v1/events", headers=HEADERS)
            if r.status_code == 200:
                events = r.json().get("data", [])
                for ev in events:
                    sport = ev.get("sport_category", "Eventos").capitalize()
                    title = ev.get("title", "")
                    for src in ev.get("sources", []):
                        if src.get("source") == "own":
                            cid = src.get("channel_id")
                            cname = src.get("name") or f"Canal {cid}"
                            if cid and cid not in channels:
                                full_name = f"{cname} - {title}" if title and title != cname else cname
                                channels[cid] = {"id": cid, "name": full_name, "group": sport}
        except Exception as e:
            print(f"[Aviso] No se pudieron actualizar eventos: {e}")

        self.channel_catalog = channels
        self.catalog_last_updated = now
        return channels

    async def get_token_for_channel(self, channel_id: int, duration: Optional[str] = None, force: bool = False) -> Dict:
        """
        Obtiene el token activo para el canal solicitado con la duración especificada (ej: 20m, 1h, 3h).
        Si ya existe en caché y restan más de 120 segundos de validez, se retorna inmediatamente.
        """
        secs, native = parse_duration_seconds(duration)
        cache_key = (channel_id, native)
        now = time.time()

        if not force and cache_key in self.cached_tokens:
            info = self.cached_tokens[cache_key]
            remaining = int(info["expires_at"] - now)
            if remaining > 120:
                res = dict(info)
                res["remaining_seconds"] = remaining
                res["remaining_minutes"] = round(remaining / 60, 1)
                return res

        client = await self.get_client()
        player_url = f"{BASE_URL}/api/player/{channel_id}"

        r_player = await client.get(player_url, headers=HEADERS)
        if r_player.status_code != 200:
            raise HTTPException(status_code=404, detail=f"Canal {channel_id} no disponible en ZicoTV (HTTP {r_player.status_code})")

        html = r_player.text
        m_title = re.search(r'<title>Reproductor\s*-\s*([^<]+)</title>', html)
        channel_name = m_title.group(1).strip() if m_title else CANALES_FIJOS.get(channel_id, {}).get("name", f"Canal {channel_id}")

        m_renew = re.search(r'var\s+ZT_RENEW\s*=\s*(\{.*?\});', html)
        if not m_renew:
            raise HTTPException(status_code=502, detail=f"No se pudo extraer la firma ZT_RENEW para el canal {channel_id}")

        renew_info = json.loads(m_renew.group(1))
        renew_path = renew_info.get("url")
        if not renew_path:
            raise HTTPException(status_code=502, detail="Ruta de renovación inválida")

        renew_url = f"{BASE_URL}{renew_path}"
        post_headers = {
            "User-Agent": USER_AGENT,
            "Referer": player_url,
            "Origin": BASE_URL,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Payload según duración: native=True da 3 horas (10800s); native=False o {} da 20 min (1200s)
        payload = {"native": True} if native else {}
        r_renew = await client.post(renew_url, json=payload, headers=post_headers)
        if r_renew.status_code != 200:
            raise HTTPException(status_code=r_renew.status_code, detail=f"Fallo al renovar token de canal {channel_id}: {r_renew.text[:100]}")

        data = r_renew.json()
        sources = data.get("sources", [])
        valid_sources = [s for s in sources if s.get("url") and not s.get("locked")]
        if not valid_sources:
            raise HTTPException(status_code=503, detail=f"Canal {channel_id} sin fuentes activas disponibles")

        chosen_source = valid_sources[0]
        stream_url = chosen_source["url"]
        upstream_ttl = int(data.get("ttl", 10800 if native else 1200))
        effective_ttl = min(upstream_ttl, secs)
        expires_at = now + effective_ttl

        # Extraer token crudo de la URL
        parsed = urlparse(stream_url)
        params = parse_qs(parsed.query)
        token_str = params.get("token", [""])[0]

        result = {
            "channel_id": channel_id,
            "name": channel_name,
            "token": token_str,
            "stream_url": stream_url,
            "cdn": chosen_source.get("label", "CDN"),
            "ttl": effective_ttl,
            "duration_requested": f"{effective_ttl // 60}m" if effective_ttl < 3600 else f"{effective_ttl / 3600:.1f}h",
            "is_native_3h": native,
            "expires_at": expires_at,
            "expires_at_iso": datetime.fromtimestamp(expires_at, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
            "remaining_seconds": effective_ttl,
            "remaining_minutes": round(effective_ttl / 60, 1),
            "sources_count": len(valid_sources),
            "all_sources": valid_sources
        }

        self.cached_tokens[cache_key] = result
        return result

    def resolve_sync(self, channel_id: int, duration: Optional[str] = None) -> Dict:
        """Versión síncrona para invocación CLI rápida usando requests/urllib."""
        import urllib.request
        secs, native = parse_duration_seconds(duration)
        player_url = f"{BASE_URL}/api/player/{channel_id}"

        req = urllib.request.Request(player_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8")

        m_title = re.search(r'<title>Reproductor\s*-\s*([^<]+)</title>', html)
        channel_name = m_title.group(1).strip() if m_title else CANALES_FIJOS.get(channel_id, {}).get("name", f"Canal {channel_id}")

        m_renew = re.search(r'var\s+ZT_RENEW\s*=\s*(\{.*?\});', html)
        if not m_renew:
            raise RuntimeError(f"No se encontró firma ZT_RENEW para canal {channel_id}")

        renew_info = json.loads(m_renew.group(1))
        renew_url = BASE_URL + renew_info["url"]

        payload = {"native": True} if native else {}
        post_data = json.dumps(payload).encode("utf-8")
        post_req = urllib.request.Request(
            renew_url,
            data=post_data,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": player_url,
                "Origin": BASE_URL,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        with urllib.request.urlopen(post_req, timeout=10) as post_resp:
            data = json.loads(post_resp.read().decode("utf-8"))

        valid_sources = [s for s in data.get("sources", []) if s.get("url") and not s.get("locked")]
        if not valid_sources:
            raise RuntimeError("No hay fuentes disponibles")

        chosen = valid_sources[0]
        stream_url = chosen["url"]
        parsed = urlparse(stream_url)
        params = parse_qs(parsed.query)
        token_str = params.get("token", [""])[0]
        upstream_ttl = data.get("ttl", 10800 if native else 1200)
        effective_ttl = min(upstream_ttl, secs)

        return {
            "channel_id": channel_id,
            "name": channel_name,
            "token": token_str,
            "stream_url": stream_url,
            "cdn": chosen.get("label", "CDN"),
            "ttl": effective_ttl,
            "duration": f"{effective_ttl // 60}m" if effective_ttl < 3600 else f"{effective_ttl // 3600}h",
            "sources": len(valid_sources),
        }

extractor = TokenExtractor()

if HAS_FASTAPI:
    app = FastAPI(title="ZicoTV Token Extractor API", version="1.1.0")

    if bool(AUTH_USER) != bool(AUTH_PASS):
        print("[Aviso] AUTH_USER y AUTH_PASS deben definirse juntos para activar la autenticación; acceso abierto activo.")

    @app.middleware("http")
    async def optional_basic_auth_middleware(request: Request, call_next):
        """Aplica HTTP Basic Auth solo si AUTH_USER y AUTH_PASS están configurados."""
        if not (AUTH_USER and AUTH_PASS) or request.url.path == "/health":
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            encoded = auth_header[6:].strip()
            try:
                decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
                username, sep, password = decoded.partition(":")
                if sep and (
                    secrets.compare_digest(username.encode("utf-8"), AUTH_USER.encode("utf-8"))
                    and secrets.compare_digest(password.encode("utf-8"), AUTH_PASS.encode("utf-8"))
                ):
                    return await call_next(request)
            except (binascii.Error, UnicodeDecodeError, ValueError):
                pass

        return Response(
            content="Autenticación requerida",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="ZicoTV Token Extractor"'},
        )

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "zicotv_token_extractor", "timestamp": time.time()}

    @app.get("/token/{channel_id}")
    async def get_token_endpoint(
        channel_id: int,
        duration: Optional[str] = Query(None, description="Duración deseada del token: '3h' (máximo), '20m' (estándar), '1h', etc."),
        force: bool = False
    ):
        """Devuelve el token HLS activo y la URL del canal en formato JSON con la duración elegida."""
        return await extractor.get_token_for_channel(channel_id, duration=duration, force=force)

    @app.get("/stream/{channel_id}")
    async def stream_redirect_endpoint(
        channel_id: int,
        duration: Optional[str] = Query(None, description="Duración deseada del token ('3h', '20m', etc.)")
    ):
        """
        Redirige (HTTP 302) directamente a la URL de emisión activa con token renovado.
        Ideal para abrir en reproductores IPTV, VLC o Kodi con una URL fija:
        http://localhost:8085/stream/1?duration=3h
        """
        token_info = await extractor.get_token_for_channel(channel_id, duration=duration)
        return RedirectResponse(url=token_info["stream_url"], status_code=302)

    @app.get("/channels")
    async def list_channels_endpoint():
        """Devuelve la lista de todos los canales conocidos y eventos deportivos en vivo."""
        return await extractor.fetch_channels()

    @app.get("/playlist.m3u")
    async def playlist_endpoint(
        mode: str = Query("direct", description="'direct' para URLs con tokens de CDN, 'redirect' para URLs fijas del contenedor"),
        duration: Optional[str] = Query(None, description="Duración de los tokens ('3h', '20m', etc.)")
    ):
        """
        Genera la lista M3U.
        mode=direct -> Contiene las URLs directas a CDN con token (ej: 3h o 20m).
        mode=redirect -> Contiene URLs http://host:8085/stream/{cid} que se auto-renuevan y nunca caducan.
        """
        channels = await extractor.fetch_channels()
        dur_label = duration or DEFAULT_DURATION
        lines = [
            "#EXTM3U",
            f"# ZicoTV Extractor Playlist (Duración: {dur_label}) - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            ""
        ]

        for cid, meta in sorted(channels.items(), key=lambda x: (x[1]["group"], x[1]["name"])):
            lines.append(f'#EXTINF:-1 tvg-id="zicotv-{cid}" tvg-name="{meta["name"]}" group-title="{meta["group"]}",{meta["name"]}')
            if mode == "redirect":
                dur_param = f"?duration={duration}" if duration else ""
                lines.append(f"http://localhost:8085/stream/{cid}{dur_param}")
            else:
                try:
                    t = await extractor.get_token_for_channel(cid, duration=duration)
                    lines.append(t["stream_url"])
                except Exception:
                    lines.append(f"http://localhost:8085/stream/{cid}")

        return PlainTextResponse(content="\n".join(lines) + "\n", media_type="application/vnd.apple.mpegurl")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        """Panel de control interactivo con selector de duración, buscador y copia de tokens."""
        channels = await extractor.fetch_channels()
        channels_json = json.dumps([{"id": c["id"], "name": c["name"], "group": c["group"]} for c in channels.values()])

        return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ZicoTV Token Extractor</title>
    <style>
        :root {{
            --bg: #0b0f19;
            --card-bg: #151d30;
            --border: #23304e;
            --accent: #38bdf8;
            --text: #f1f5f9;
            --muted: #94a3b8;
            --green: #22c55e;
            --amber: #f59e0b;
        }}
        body {{
            background: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 24px;
        }}
        .container {{ max-width: 1100px; margin: 0 auto; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 20px; }}
        h1 {{ margin: 0; font-size: 1.5rem; display: flex; align-items: center; gap: 10px; }}
        .badge {{ background: rgba(56, 189, 248, 0.15); color: var(--accent); border: 1px solid var(--accent); padding: 4px 10px; border-radius: 6px; font-size: 0.8rem; font-weight: 600; }}
        .toolbar {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px; margin-bottom: 20px; }}
        .search-box {{ flex: 1; min-width: 280px; padding: 12px 16px; background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; color: var(--text); font-size: 1rem; box-sizing: border-box; }}
        .duration-bar {{ display: flex; align-items: center; gap: 8px; background: var(--card-bg); padding: 6px 12px; border-radius: 8px; border: 1px solid var(--border); }}
        .dur-btn {{ background: transparent; border: 1px solid transparent; color: var(--muted); padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 0.85rem; font-weight: 600; transition: all 0.2s; }}
        .dur-btn:hover {{ color: var(--text); }}
        .dur-btn.active {{ background: rgba(56, 189, 248, 0.2); border-color: var(--accent); color: var(--accent); }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }}
        .card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 16px; display: flex; flex-direction: column; justify-content: space-between; }}
        .card-title {{ font-weight: 700; font-size: 1rem; margin-bottom: 6px; display: flex; justify-content: space-between; }}
        .card-group {{ color: var(--muted); font-size: 0.8rem; margin-bottom: 14px; text-transform: uppercase; letter-spacing: 0.05em; }}
        .btn-group {{ display: flex; gap: 8px; flex-wrap: wrap; }}
        button, a.btn {{ background: #1e293b; color: var(--text); border: 1px solid var(--border); padding: 7px 12px; border-radius: 6px; cursor: pointer; font-size: 0.8rem; text-decoration: none; display: inline-flex; align-items: center; gap: 5px; font-weight: 500; transition: all 0.2s; }}
        button:hover, a.btn:hover {{ background: #334155; border-color: var(--accent); color: var(--accent); }}
        button.btn-primary {{ background: rgba(56, 189, 248, 0.2); border-color: var(--accent); color: var(--accent); }}
        .modal {{ display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); backdrop-filter: blur(4px); align-items: center; justify-content: center; z-index: 1000; }}
        .modal-content {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; max-width: 650px; width: 90%; padding: 24px; position: relative; }}
        .close-btn {{ position: absolute; right: 16px; top: 16px; cursor: pointer; color: var(--muted); font-size: 1.2rem; }}
        pre {{ background: #0b0f19; padding: 12px; border-radius: 6px; border: 1px solid var(--border); overflow-x: auto; font-family: monospace; font-size: 0.85rem; color: #38bdf8; word-break: break-all; white-space: pre-wrap; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>🔑 ZicoTV Token Extractor <span class="badge">GHCR / Podman</span></h1>
                <p style="margin: 6px 0 0; color: var(--muted); font-size: 0.9rem;">Extractor de tokens HLS con duración seleccionable (20 min a 3 horas)</p>
            </div>
            <div>
                <a href="/playlist.m3u?mode=redirect" target="_blank" class="btn btn-primary">📥 Descargar M3U (Redirect)</a>
                <a id="m3uDirectBtn" href="/playlist.m3u?mode=direct&duration=3h" target="_blank" class="btn">📥 M3U Directa</a>
            </div>
        </div>

        <div class="toolbar">
            <input type="text" id="searchInput" class="search-box" placeholder="🔍 Buscar canal (ej: DAZN, Movistar, F1, Champions)..." oninput="filterChannels()">
            
            <div class="duration-bar">
                <span style="font-size: 0.85rem; color: var(--muted);">⏱️ Duración:</span>
                <button type="button" class="dur-btn active" onclick="setDuration('3h', this)">3 Horas (Máx)</button>
                <button type="button" class="dur-btn" onclick="setDuration('1h', this)">1 Hora</button>
                <button type="button" class="dur-btn" onclick="setDuration('20m', this)">20 Minutos</button>
            </div>
        </div>

        <div id="channelsGrid" class="grid"></div>
    </div>

    <!-- Modal Token Info -->
    <div id="tokenModal" class="modal">
        <div class="modal-content">
            <span class="close-btn" onclick="closeModal()">&times;</span>
            <h2 id="modalTitle" style="margin-top: 0;"></h2>
            <p id="modalStatus" style="color: var(--muted); font-size: 0.85rem;"></p>
            
            <label style="font-size: 0.8rem; font-weight: 600; color: var(--muted);">TOKEN ACTIVO:</label>
            <pre id="modalToken">Cargando...</pre>

            <label style="font-size: 0.8rem; font-weight: 600; color: var(--muted);">URL STREAM DIRECTA HLS:</label>
            <pre id="modalUrl">Cargando...</pre>

            <div style="margin-top: 16px; display: flex; gap: 10px; flex-wrap: wrap;">
                <button onclick="copyToClipboard('modalToken', '¡Token copiado!')" class="btn-primary">📋 Copiar Token</button>
                <button onclick="copyToClipboard('modalUrl', '¡URL HLS copiada!')">📋 Copiar URL Stream</button>
                <a id="modalVlcLink" href="#" class="btn" target="_blank">▶ Abrir Stream</a>
            </div>
        </div>
    </div>

    <script>
        const channels = {channels_json};
        const grid = document.getElementById('channelsGrid');
        let selectedDuration = '3h';

        function setDuration(dur, btn) {{
            selectedDuration = dur;
            document.querySelectorAll('.dur-btn').forEach(b => b.classList.remove('active'));
            if (btn) btn.classList.add('active');
            document.getElementById('m3uDirectBtn').href = `/playlist.m3u?mode=direct&duration=${{dur}}`;
        }}

        function render(list) {{
            grid.innerHTML = list.map(c => `
                <div class="card">
                    <div>
                        <div class="card-title">
                            <span>${{c.name}}</span>
                            <span style="color: var(--muted); font-size: 0.8rem;">#${{c.id}}</span>
                        </div>
                        <div class="card-group">${{c.group}}</div>
                    </div>
                    <div class="btn-group">
                        <button onclick="fetchToken(${{c.id}}, '${{c.name.replace(/'/g, "\\\\'")}}')" class="btn-primary">🔑 Sacar Token</button>
                        <a href="/stream/${{c.id}}?duration=${{selectedDuration}}" target="_blank" class="btn">▶ Stream 302</a>
                        <a href="/token/${{c.id}}?duration=${{selectedDuration}}" target="_blank" class="btn">JSON</a>
                    </div>
                </div>
            `).join('');
        }}

        function filterChannels() {{
            const q = document.getElementById('searchInput').value.toLowerCase();
            const filtered = channels.filter(c => c.name.toLowerCase().includes(q) || c.group.toLowerCase().includes(q));
            render(filtered);
        }}

        async function fetchToken(cid, name) {{
            const modal = document.getElementById('tokenModal');
            document.getElementById('modalTitle').innerText = `[${{cid}}] ${{name}}`;
            document.getElementById('modalToken').innerText = `Extrayendo token (${{selectedDuration}}) desde ZicoTV...`;
            document.getElementById('modalUrl').innerText = 'Conectando con upstream...';
            document.getElementById('modalStatus').innerText = '';
            modal.style.display = 'flex';

            try {{
                const r = await fetch(`/token/${{cid}}?duration=${{selectedDuration}}`);
                const data = await r.json();
                if (data.token) {{
                    document.getElementById('modalToken').innerText = data.token;
                    document.getElementById('modalUrl').innerText = data.stream_url;
                    document.getElementById('modalStatus').innerText = `CDN: ${{data.cdn}} • TTL: ${{data.ttl}}s (~${{data.remaining_minutes}} min) • Duración solicitada: ${{data.duration_requested}} • Expira: ${{data.expires_at_iso}}`;
                    document.getElementById('modalVlcLink').href = data.stream_url;
                }} else {{
                    document.getElementById('modalToken').innerText = 'Error al extraer token';
                    document.getElementById('modalUrl').innerText = JSON.stringify(data, null, 2);
                }}
            }} catch(e) {{
                document.getElementById('modalToken').innerText = 'Error de red: ' + e;
            }}
        }}

        function closeModal() {{
            document.getElementById('tokenModal').style.display = 'none';
        }}

        function copyToClipboard(elementId, msg) {{
            const text = document.getElementById(elementId).innerText;
            navigator.clipboard.writeText(text);
            alert(msg);
        }}

        render(channels);
    </script>
</body>
</html>
"""
else:
    app = None

def cli_main():
    """Punto de entrada de línea de comandos."""
    parser = argparse.ArgumentParser(description="ZicoTV Token Extractor CLI & Server")
    parser.add_argument("channel_id", nargs="?", type=int, help="ID del canal a resolver (ej: 1 para DAZN LaLiga)")
    parser.add_argument("-d", "--duration", default=DEFAULT_DURATION, help="Duración del token (ej: 20m, 1h, 3h máx). Por defecto 3h.")
    parser.add_argument("--m3u", metavar="OUTPUT", nargs="?", const="lista_zicotv.m3u", help="Generar archivo M3U con tokens")
    parser.add_argument("--server", action="store_true", help="Iniciar servidor web FastAPI")
    parser.add_argument("--port", type=int, default=8085, help="Puerto del servidor (por defecto 8085)")
    parser.add_argument("--host", default="0.0.0.0", help="Host del servidor (por defecto 0.0.0.0)")

    args = parser.parse_args()

    # Si se pide levantar servidor
    if args.server:
        import uvicorn
        print(f"Iniciando ZicoTV Token Extractor en http://{args.host}:{args.port} (Duración por defecto: {args.duration})...")
        uvicorn.run("extractor:app", host=args.host, port=args.port, reload=False)
        return

    # Si se pide canal específico
    if args.channel_id:
        cid = args.channel_id
        dur = args.duration
        print("=" * 60)
        print(f"Extrayendo Token para Canal {cid} (Duración: {dur})...")
        print("=" * 60)
        try:
            info = extractor.resolve_sync(cid, duration=dur)
            print(f"Canal:      [{info['channel_id']}] {info['name']}")
            print(f"CDN:        {info['cdn']}")
            print(f"Duración:   {info['duration']} ({info['ttl']}s)")
            print(f"Fuentes:    {info['sources']} CDNs disponibles\n")
            print("TOKEN ACTIVO:")
            print(info['token'])
            print("\nURL HLS COMPLETA:")
            print(info['stream_url'])
            print("=" * 60)
        except Exception as e:
            print(f"Error al extraer token para canal {cid}: {e}")
            sys.exit(1)
        return

    # Si se pide generar M3U
    if args.m3u:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        output_file = args.m3u
        dur = args.duration
        print(f"Generando lista M3U con tokens de {dur} en '{output_file}'...")
        channels = CANALES_FIJOS
        resolved = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(extractor.resolve_sync, cid, dur): cid for cid in channels}
            for f in as_completed(futs):
                cid = futs[f]
                try:
                    res = f.result()
                    if res and res.get("stream_url"):
                        print(f"  ✓ [{res['channel_id']}] {res['name']} ({res['cdn']} - {res['duration']})")
                        resolved.append(res)
                except Exception as e:
                    print(f"  ✗ [{cid}]: {e}")

        with open(output_file, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            f.write(f"# ZicoTV Local Extractor List (Tokens: {dur}) - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            for r in sorted(resolved, key=lambda x: x["name"]):
                f.write(f'#EXTINF:-1 tvg-id="zicotv-{r["channel_id"]}" tvg-name="{r["name"]}",{r["name"]}\n')
                f.write(f"{r['stream_url']}\n")
        print(f"\n✓ Lista guardada en '{output_file}' con {len(resolved)} canales activos.")
        return

    # Si se ejecuta sin argumentos en contenedor, arrancar servidor
    if os.environ.get("CONTAINER") or os.path.exists("/.dockerenv"):
        import uvicorn
        uvicorn.run("extractor:app", host=args.host, port=args.port, reload=False)
    else:
        parser.print_help()

if __name__ == "__main__":
    cli_main()
