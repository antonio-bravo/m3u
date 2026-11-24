import os
import re
import json
import time
import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, quote

import httpx
from fastapi import Request, HTTPException, Response

from cors_hls_proxy import proxy_m3u8_manifest, window_manager

logger = logging.getLogger("m3u_stream_proxy.zicotv")

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15",
    "Mozilla/5.0 (iPad; CPU OS 18_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:134.0) Gecko/20100101 Firefox/134.0",
]
DEFAULT_USER_AGENT = USER_AGENTS[0]

# ---------------------------------------------------------------------------
# Canales fijos 24/7 conocidos de la plataforma ZicoTV
# ---------------------------------------------------------------------------
CANALES_FIJOS: Dict[int, Dict[str, str]] = {
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

LOGOS_DESTACADOS: Dict[str, str] = {
    "dazn motogp": "https://raw.githubusercontent.com/Icastresana/logos/main/dazn_motogp.png",
    "dazn f1": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1a/DAZN_F1_logo.png/512px-DAZN_F1_logo.png",
    "dazn f1 spain": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1a/DAZN_F1_logo.png/512px-DAZN_F1_logo.png",
    "dazn laliga": "https://upload.wikimedia.org/wikipedia/commons/9/98/DAZN_LaLiga_2023_Logo.svg",
    "dazn laliga 2": "https://upload.wikimedia.org/wikipedia/commons/9/98/DAZN_LaLiga_2023_Logo.svg",
    "dazn 1": "https://upload.wikimedia.org/wikipedia/commons/a/a2/DAZN_1_Logo.svg",
    "dazn 1 spain": "https://upload.wikimedia.org/wikipedia/commons/a/a2/DAZN_1_Logo.svg",
    "dazn 2": "https://upload.wikimedia.org/wikipedia/commons/a/a2/DAZN_1_Logo.svg",
    "dazn 2 spain": "https://upload.wikimedia.org/wikipedia/commons/a/a2/DAZN_1_Logo.svg",
    "dazn 3 spain": "https://upload.wikimedia.org/wikipedia/commons/a/a2/DAZN_1_Logo.svg",
    "dazn 4 spain": "https://upload.wikimedia.org/wikipedia/commons/a/a2/DAZN_1_Logo.svg",
    "movistar #vamos": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/Vamos_logo_2021.svg/512px-Vamos_logo_2021.svg.png",
    "movistar plus+": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/07/Movistar_Plus%2B_2023_logo.svg/512px-Movistar_Plus%2B_2023_logo.svg.png",
    "eurosport 1": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d0/Eurosport_1_Logo_2015.svg/512px-Eurosport_1_Logo_2015.svg.png",
    "eurosport 1 spain": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d0/Eurosport_1_Logo_2015.svg/512px-Eurosport_1_Logo_2015.svg.png",
    "eurosport 2": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4c/Eurosport_2_Logo_2015.svg/512px-Eurosport_2_Logo_2015.svg.png",
    "eurosport 2 spain": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4c/Eurosport_2_Logo_2015.svg/512px-Eurosport_2_Logo_2015.svg.png",
    "espn deportes": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2f/ESPN_wordmark.svg/512px-ESPN_wordmark.svg.png",
    "sky sports main event": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/36/Sky_Sports_Main_Event_logo_2020.svg/512px-Sky_Sports_Main_Event_logo_2020.svg.png",
}

DEPORTE_TRADUCCIONES: Dict[str, str] = {
    "soccer": "Fútbol",
    "football": "Fútbol",
    "basketball": "Baloncesto",
    "motorsport": "Motor",
    "fighting": "Combate",
    "tennis": "Tenis",
    "ice hockey": "Hockey Hielo",
    "volleyball": "Voleibol",
    "handball": "Balonmano",
    "american football": "Fútbol Americano",
    "padel": "Pádel",
    "darts": "Dardos",
    "futsal": "Fútbol Sala",
    "cricket": "Cricket",
    "other": "Eventos en Directo",
}


class ZicoTVManager:
    """
    Gestor del microproxy de ZicoTV.
    - Mantiene tokens Flussonic activos de 3 horas (solicitados con modo nativo HLS).
    - Renueva automáticamente los tokens en segundo plano antes de expirar.
    - Sincroniza dinámicamente los eventos deportivos en vivo y canales fijos.
    - Proxifica el manifiesto HLS a través de cors_hls_proxy con búfer PRO.
    """

    def __init__(self):
        self.base_urls = ["https://bp.zicotv.cc", "https://zicotv.cc"]
        self.active_base_url = "https://bp.zicotv.cc"
        self.cached_streams: Dict[int, Dict] = {}  # channel_id -> stream_data
        self.cached_catalog: List[Dict] = []
        self.catalog_last_updated: float = 0.0
        self.catalog_ttl: float = 300.0  # 5 minutos para catálogo de canales/eventos
        self.logos_dict: Dict[str, str] = dict(LOGOS_DESTACADOS)
        self.channel_locks: Dict[int, asyncio.Lock] = {}
        self.global_lock = asyncio.Lock()
        self._client: Optional[httpx.AsyncClient] = None

        self._load_logos_xml()

    def _load_logos_xml(self):
        """Carga logos desde LOGOS-CANALES-TV.xml si está disponible en disco."""
        candidate_paths = [
            os.path.join(os.path.dirname(__file__), "..", "LOGOS-CANALES-TV.xml"),
            os.path.join(os.path.dirname(__file__), "LOGOS-CANALES-TV.xml"),
            os.path.join(os.getcwd(), "LOGOS-CANALES-TV.xml"),
            "/app/LOGOS-CANALES-TV.xml",
            "/app/local_playlists/LOGOS-CANALES-TV.xml",
            "/app/data/LOGOS-CANALES-TV.xml",
        ]
        xml_file = None
        for p in candidate_paths:
            if p and os.path.isfile(p):
                xml_file = p
                break

        if not xml_file:
            return

        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            for ch in root.findall(".//channel"):
                name = ch.get("name")
                logo = ch.find("logo_url")
                if name and logo is not None and logo.text:
                    clean_name = name.lower().strip()
                    if clean_name not in self.logos_dict:
                        self.logos_dict[clean_name] = logo.text.strip()
            logger.info(f"Logos de ZicoTV cargados ({len(self.logos_dict)} referencias desde {xml_file})")
        except Exception as e:
            logger.warning(f"No se pudo cargar logos desde {xml_file}: {e}")

    def find_logo(self, channel_name: str) -> str:
        """Busca el mejor logo disponible para un canal."""
        if not channel_name:
            return ""
        name_clean = channel_name.lower().strip()
        if name_clean in self.logos_dict:
            return self.logos_dict[name_clean]

        for k, v in self.logos_dict.items():
            if k in name_clean or name_clean in k:
                return v
        return ""

    async def get_client(self) -> httpx.AsyncClient:
        """Obtiene o inicializa el cliente HTTP asíncrono con keep-alive."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Accept": "*/*",
                    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
                },
                timeout=12.0,
                follow_redirects=True,
            )
        return self._client

    async def get_active_base_url(self) -> str:
        """Comprueba y devuelve el dominio base de ZicoTV operativo."""
        client = await self.get_client()
        for base in self.base_urls:
            try:
                r = await client.get(f"{base}/api/v1", timeout=4.0)
                if r.status_code == 200:
                    self.active_base_url = base
                    return base
            except Exception:
                pass
        return self.active_base_url

    def _get_channel_lock(self, channel_id: int) -> asyncio.Lock:
        if channel_id not in self.channel_locks:
            self.channel_locks[channel_id] = asyncio.Lock()
        return self.channel_locks[channel_id]

    async def get_channel_catalog(self, force: bool = False) -> List[Dict]:
        """
        Obtiene el catálogo de canales unificado:
        1. Canales fijos 24/7 (37 canales).
        2. Canales de eventos en directo actuales (desde /api/v1/events).
        """
        now = time.time()
        if not force and self.cached_catalog and (now - self.catalog_last_updated) < self.catalog_ttl:
            return self.cached_catalog

        async with self.global_lock:
            if not force and self.cached_catalog and (now - self.catalog_last_updated) < self.catalog_ttl:
                return self.cached_catalog

            base = await self.get_active_base_url()
            client = await self.get_client()

            canales_dict: Dict[int, Dict] = {}

            # 1. Canales fijos
            for cid, info in CANALES_FIJOS.items():
                canales_dict[cid] = {
                    "id": cid,
                    "name": info["name"],
                    "group": info["group"],
                    "logo": self.find_logo(info["name"]),
                    "type": "fixed",
                }

            # 2. Eventos en vivo
            try:
                events_url = f"{base}/api/v1/events"
                r = await client.get(events_url, timeout=6.0)
                if r.status_code == 200:
                    events_data = r.json().get("data", [])
                    for ev in events_data:
                        raw_sport = (ev.get("sport_category") or "General").lower().strip()
                        sport = DEPORTE_TRADUCCIONES.get(raw_sport, raw_sport.capitalize())
                        event_title = (ev.get("title") or "").strip()

                        for src in ev.get("sources", []):
                            if src.get("source") == "own":
                                cid = src.get("channel_id")
                                cname = (src.get("name") or f"Canal {cid}").strip()
                                if cid and cid not in canales_dict:
                                    if event_title and event_title != cname:
                                        full_name = f"{cname} - {event_title}"
                                    else:
                                        full_name = cname

                                    canales_dict[cid] = {
                                        "id": cid,
                                        "name": full_name,
                                        "group": sport,
                                        "logo": self.find_logo(cname) or self.find_logo(full_name),
                                        "type": "event",
                                    }
            except Exception as e:
                logger.warning(f"Advertencia al consultar eventos dinámicos de ZicoTV: {e}")

            catalog = sorted(list(canales_dict.values()), key=lambda x: (x["group"], x["name"]))
            self.cached_catalog = catalog
            self.catalog_last_updated = now
            return catalog

    async def resolve_stream(self, channel_id: int, force_renew: bool = False) -> Dict:
        """
        Resuelve y renueva el stream HLS de un canal de ZicoTV.
        - Si el token está en caché y quedan más de 10 minutos de validez, se reutiliza.
        - Si no está o está por expirar, realiza el POST /renew con {"native": true}
          obteniendo un token de 3 horas (ttl: 10800).
        - Obtiene la URL del manifiesto de medios (ej. tracks-v1a1/mono.m3u8).
        """
        now = time.time()
        cached = self.cached_streams.get(channel_id)
        # Margen de seguridad de 10 minutos (600s)
        if not force_renew and cached and (cached["expires_at"] - now) > 600:
            return cached

        lock = self._get_channel_lock(channel_id)
        async with lock:
            # Doble comprobación tras adquirir el cerrojo
            now = time.time()
            cached = self.cached_streams.get(channel_id)
            if not force_renew and cached and (cached["expires_at"] - now) > 600:
                return cached

            base = await self.get_active_base_url()
            client = await self.get_client()

            player_url = f"{base}/api/player/{channel_id}"
            logger.info(f"Resolviendo stream para canal ZicoTV ID {channel_id}...")

            r_player = await client.get(player_url, timeout=8.0)
            if r_player.status_code != 200:
                raise HTTPException(
                    status_code=r_player.status_code,
                    detail=f"ZicoTV devolvió código {r_player.status_code} al consultar reproductor del canal {channel_id}"
                )

            html = r_player.text
            m_title = re.search(r"<title>Reproductor\s*-\s*([^<]+)</title>", html)
            channel_name = m_title.group(1).strip() if m_title else f"Canal {channel_id}"

            m_renew = re.search(r"var\s+ZT_RENEW\s*=\s*(\{.*?\});", html)
            if not m_renew:
                raise HTTPException(
                    status_code=502,
                    detail=f"No se encontró información de renovación (ZT_RENEW) para el canal {channel_id}"
                )

            try:
                renew_info = json.loads(m_renew.group(1))
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Error parseando ZT_RENEW: {e}")

            renew_rel_path = renew_info.get("url")
            if not renew_rel_path:
                raise HTTPException(status_code=502, detail="Ruta de renovación no encontrada en ZT_RENEW")

            renew_full_url = urljoin(base, renew_rel_path)

            # Solicitar token de 3 horas en modo nativo HLS (native: true) con rotación de User-Agent ante 403
            r_renew = None
            for ua in USER_AGENTS:
                post_headers = {
                    "User-Agent": ua,
                    "Referer": player_url,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                r_renew = await client.post(
                    renew_full_url,
                    json={"native": True},
                    headers=post_headers,
                    timeout=8.0
                )
                if r_renew.status_code == 200:
                    break

            if not r_renew or r_renew.status_code != 200:
                raise HTTPException(
                    status_code=r_renew.status_code if r_renew else 502,
                    detail=f"Error en endpoint de renovación ZicoTV: {r_renew.status_code if r_renew else 'Timeout'}"
                )

            renew_data = r_renew.json()
            sources = renew_data.get("sources", [])
            valid_sources = [s for s in sources if s.get("url") and not s.get("locked")]
            if not valid_sources:
                raise HTTPException(
                    status_code=503,
                    detail=f"El canal {channel_id} ({channel_name}) no tiene fuentes de emisión disponibles actualmente"
                )

            master_url = valid_sources[0]["url"]
            slug = renew_data.get("slug", "")
            ttl = int(renew_data.get("ttl", 10800))  # 3 horas por defecto
            expires_at = now + ttl

            # Obtener el manifiesto maestro para resolver la sub-lista directa de medios (tracks-v1a1/mono.m3u8)
            media_url = master_url
            try:
                r_m3u8 = await client.get(master_url, headers={"Referer": player_url}, timeout=6.0)
                if r_m3u8.status_code == 200 and "#EXT-X-STREAM-INF" in r_m3u8.text:
                    lines = [l.strip() for l in r_m3u8.text.splitlines() if l.strip() and not l.startswith("#")]
                    if lines:
                        media_url = urljoin(master_url, lines[0])
            except Exception as e:
                logger.warning(f"Aviso al resolver variante de medios para {channel_id}: {e}")

            stream_data = {
                "channel_id": channel_id,
                "name": channel_name,
                "master_url": master_url,
                "media_url": media_url,
                "slug": slug,
                "ttl": ttl,
                "expires_at": expires_at,
                "player_url": player_url,
                "renew_url": renew_full_url,
                "cdn_label": valid_sources[0].get("label", "CDN"),
                "updated_at": now,
            }

            self.cached_streams[channel_id] = stream_data
            logger.info(
                f"Stream ZicoTV renovado para canal {channel_id} ({channel_name}). TTL: {ttl}s ({ttl // 3600}h). CDN: {stream_data['cdn_label']}"
            )
            return stream_data

    async def proxy_channel_hls(
        self,
        channel_id: int,
        request: Request,
        buffer_offset: Optional[float] = None
    ) -> Response:
        """
        Descarga el manifiesto del canal, lo reescribe con CORS y búfer PRO,
        y asegura que los segmentos lleven siempre el token válido.
        Si el token expira o es rechazado con 401/403, renueva automáticamente
        de forma transparente para que la reproducción nunca se corte.
        """
        stream_info = await self.resolve_stream(channel_id)
        base_url = str(request.base_url).rstrip("/")
        offset = buffer_offset if buffer_offset is not None else 16.0

        # Seguimiento en tiempo real de clientes y canales en reproducción activa
        client_ip = request.client.host if request.client else "desconocido"
        now = time.time()
        last_req = stream_info.get("last_requested_at", 0)
        if now - last_req > 60:
            logger.info(
                f"▶ [ZicoTV] Canal activo: [{channel_id}] {stream_info['name']} | "
                f"Dispositivo: {client_ip} | CDN: {stream_info.get('cdn_label', 'CDN')}"
            )
        stream_info["last_requested_at"] = now
        stream_info["last_client_ip"] = client_ip
        stream_info["request_count"] = stream_info.get("request_count", 0) + 1

        try:
            return await proxy_m3u8_manifest(
                target_url=stream_info["media_url"],
                request_base_url=base_url,
                buffer_offset=offset,
                custom_ref=stream_info["player_url"],
                stream_id=f"zicotv_{channel_id}"
            )
        except HTTPException as e:
            if e.status_code in (401, 403):
                logger.warning(
                    f"Token upstream caducado o denegado ({e.status_code}) para canal {channel_id}. Renovando forzosamente..."
                )
                stream_info = await self.resolve_stream(channel_id, force_renew=True)
                return await proxy_m3u8_manifest(
                    target_url=stream_info["media_url"],
                    request_base_url=base_url,
                    buffer_offset=offset,
                    custom_ref=stream_info["player_url"],
                    stream_id=f"zicotv_{channel_id}"
                )
            raise

    async def generate_m3u_playlist(self, request_base_url: str) -> str:
        """Genera una lista de reproducción M3U con todos los canales apuntando a este microproxy."""
        catalog = await self.get_channel_catalog()
        lines = [
            "#EXTM3U",
            "# ZicoTV Microproxy PRO Playlist",
            f"# Canales disponibles: {len(catalog)}",
            "",
        ]

        curr_group = None
        for ch in catalog:
            cid = ch["id"]
            cname = ch["name"]
            cgroup = ch["group"]
            clogo = ch.get("logo", "")

            if cgroup != curr_group:
                curr_group = cgroup
                lines.append(f"\n# -------- {curr_group.upper()} --------")

            logo_attr = f' tvg-logo="{clogo}"' if clogo else ""
            lines.append(f'#EXTINF:-1 tvg-id="zicotv-{cid}" tvg-name="{cname}"{logo_attr} group-title="{cgroup}",{cname}')
            lines.append(f"{request_base_url}/zico/live/{cid}.m3u8")

        return "\n".join(lines) + "\n"

    def get_status(self) -> Dict:
        """Devuelve el estado del proxy ZicoTV, canales cacheados y métricas de tokens."""
        now = time.time()
        streams_status = []
        for cid, s in self.cached_streams.items():
            remaining = max(0, int(s["expires_at"] - now))
            last_req = s.get("last_requested_at", s.get("updated_at", now))
            diff_req = max(0, int(now - last_req))
            is_active = diff_req < 60  # Si hubo petición en los últimos 60 segundos
            streams_status.append({
                "channel_id": cid,
                "name": s["name"],
                "is_playing_now": is_active,
                "status": "REPRODUCIENDO_EN_VIVO" if is_active else "INACTIVO",
                "seconds_since_last_request": diff_req,
                "client_ip": s.get("last_client_ip", "desconocido"),
                "requests_served": s.get("request_count", 0),
                "remaining_seconds": remaining,
                "remaining_minutes": round(remaining / 60, 1),
                "cdn": s.get("cdn_label", ""),
                "slug": s.get("slug", ""),
            })

        # Ordenar primero los que se están viendo ahora, luego por tiempo desde última petición
        streams_status.sort(key=lambda x: (not x["is_playing_now"], x["seconds_since_last_request"]))
        active_now_count = sum(1 for s in streams_status if s["is_playing_now"])

        return {
            "active_base_url": self.active_base_url,
            "catalog_count": len(self.cached_catalog),
            "catalog_last_updated": self.catalog_last_updated,
            "playing_now_count": active_now_count,
            "cached_tokens_count": len(self.cached_streams),
            "active_streams": streams_status,
        }

    def render_status_dashboard(self, request_base_url: str) -> str:
        """Genera un panel HTML interactivo con auto-refresco que muestra en vivo los canales en reproducción."""
        status = self.get_status()
        active_count = status["playing_now_count"]
        cached_count = status["cached_tokens_count"]
        catalog_count = status["catalog_count"]
        active_streams = status["active_streams"]

        rows_html = ""
        if not active_streams:
            rows_html = """
            <tr>
                <td colspan="6" style="text-align: center; padding: 36px; color: #94a3b8;">
                    No hay ningún canal en reproducción en este momento.<br>
                    <small style="color: #64748b;">Abre cualquier canal en VLC, TiviMate o Smart TV para verlo reflejado aquí en tiempo real.</small>
                </td>
            </tr>
            """
        else:
            for s in active_streams:
                is_active = s["is_playing_now"]
                status_badge = (
                    '<span class="badge badge-active">🟢 EN REPRODUCCIÓN</span>'
                    if is_active
                    else '<span class="badge badge-idle">⚪ INACTIVO</span>'
                )
                last_seen = f"{s['seconds_since_last_request']}s" if s['seconds_since_last_request'] < 60 else f"{s['seconds_since_last_request'] // 60}m"
                cid = s['channel_id']
                cname = s['name']
                cdn = s['cdn']
                client_ip = s['client_ip']
                rem_min = s['remaining_minutes']

                rows_html += f"""
                <tr class="{'row-active' if is_active else 'row-idle'}">
                    <td><strong>[{cid}]</strong> {cname}</td>
                    <td>{status_badge}</td>
                    <td><code class="ip-code">{client_ip}</code></td>
                    <td>hace {last_seen}</td>
                    <td><span class="token-ttl">{rem_min} min</span> (Auto-renovable)</td>
                    <td><span class="cdn-tag">{cdn}</span></td>
                </tr>
                """

        return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="4">
    <title>ZicoTV - Canales en Reproducción Activa</title>
    <style>
        :root {{
            --bg-dark: #0f172a;
            --card-bg: #1e293b;
            --border-color: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #38bdf8;
            --success: #22c55e;
            --warning: #f59e0b;
        }}
        body {{
            background: var(--bg-dark);
            color: var(--text-main);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 24px;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
        }}
        .header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border-color);
        }}
        h1 {{
            margin: 0;
            font-size: 1.5rem;
            color: var(--accent);
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .stat-card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 16px;
            text-align: center;
        }}
        .stat-val {{
            font-size: 2rem;
            font-weight: 700;
            color: var(--accent);
        }}
        .stat-val.active {{
            color: var(--success);
        }}
        .stat-label {{
            color: var(--text-muted);
            font-size: 0.85rem;
            margin-top: 4px;
        }}
        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            overflow: hidden;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.95rem;
        }}
        th, td {{
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }}
        th {{
            background: rgba(0,0,0,0.25);
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.05em;
        }}
        .row-active {{
            background: rgba(34, 197, 94, 0.06);
        }}
        .badge {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
        }}
        .badge-active {{
            background: rgba(34, 197, 94, 0.2);
            color: var(--success);
            border: 1px solid rgba(34, 197, 94, 0.4);
        }}
        .badge-idle {{
            background: rgba(148, 163, 184, 0.1);
            color: var(--text-muted);
        }}
        .ip-code {{
            background: rgba(0,0,0,0.3);
            padding: 2px 6px;
            border-radius: 4px;
            font-family: monospace;
            color: var(--accent);
        }}
        .token-ttl {{
            color: var(--warning);
            font-weight: 600;
        }}
        .cdn-tag {{
            color: var(--text-muted);
            font-size: 0.85rem;
        }}
        .footer {{
            margin-top: 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            color: var(--text-muted);
            font-size: 0.85rem;
        }}
        a {{
            color: var(--accent);
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📡 Canales ZicoTV en Reproducción Activa</h1>
            <div>
                <a href="{request_base_url}/zico/playlist.m3u" target="_blank" style="padding: 6px 12px; background: rgba(56,189,248,0.15); border: 1px solid var(--accent); border-radius: 6px;">📥 Lista M3U</a>
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-val active">{active_count}</div>
                <div class="stat-label">🟢 Canales viéndose ahora</div>
            </div>
            <div class="stat-card">
                <div class="stat-val">{cached_count}</div>
                <div class="stat-label">🔑 Tokens activos en RAM (3h)</div>
            </div>
            <div class="stat-card">
                <div class="stat-val">{catalog_count}</div>
                <div class="stat-label">📺 Canales en catálogo</div>
            </div>
        </div>

        <div class="card">
            <table>
                <thead>
                    <tr>
                        <th>Canal</th>
                        <th>Estado</th>
                        <th>Dispositivo / IP</th>
                        <th>Última Petición</th>
                        <th>Token Restante</th>
                        <th>CDN</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
        </div>

        <div class="footer">
            <div>Auto-refresco cada 4 segundos • <a href="{request_base_url}/zico/api/status" target="_blank">Ver JSON de estado</a></div>
            <div>Microproxy ZicoTV PRO</div>
        </div>
    </div>
</body>
</html>
"""

    async def close(self):
        """Cierra el cliente HTTP al apagar el servidor."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# Instancia singleton del gestor ZicoTV
zico_manager = ZicoTVManager()
