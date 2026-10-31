import os
import re
import json
import time
import glob
import logging
import asyncio
from typing import Dict, List, Optional, Set, Any
import httpx

from m3u_parser import parse_m3u_file, parse_m3u_text, detect_acestream_id, detect_stream_type
from github_scanner import normalize_github_url, format_bytes, parse_repo_identifier, scan_github_repository, RAW_GITHUB_REGEX

logger = logging.getLogger("m3u_stream_proxy.manager")


class PlaylistManager:
    def __init__(self, data_dir: str = "data", local_playlists_dir: str = "local_playlists"):
        self.data_dir = data_dir
        self.local_playlists_dir = local_playlists_dir
        
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.local_playlists_dir, exist_ok=True)

        self.sources_file = os.path.join(self.data_dir, "sources.json")
        self.favorites_file = os.path.join(self.data_dir, "favorites.json")
        self.settings_file = os.path.join(self.data_dir, "settings.json")
        self.custom_channels_file = os.path.join(self.data_dir, "custom_channels.json")
        self.github_repos_file = os.path.join(self.data_dir, "github_repos.json")
        self.github_cache_dir = os.path.join(self.data_dir, "github_cache")
        os.makedirs(self.github_cache_dir, exist_ok=True)

        self.sources: Dict[str, Dict] = {}
        self.favorites: Set[str] = set()
        self.custom_channels: Dict[str, Dict] = {}
        self.github_repos: Dict[str, Dict] = {}

        default_engine = os.environ.get("ACESTREAM_ENGINE_URL", "http://host.docker.internal:6878")
        vpn_engine = os.environ.get("ACESTREAM_VPN_ENGINE_URL", "")

        default_servers = [
            {
                "id": "direct",
                "name": "⚡ Sin VPN",
                "url": default_engine,
                "is_default": True
            }
        ]
        if vpn_engine:
            default_servers.append({
                "id": "vpn",
                "name": "🛡️ Con VPN",
                "url": vpn_engine,
                "is_default": False
            })

        self.settings: Dict[str, Any] = {
            "acestream_engine_url": default_engine,
            "acestream_servers": default_servers,
            "active_acestream_server_id": "direct",
            "auto_failover_acestream": True,
            "buffer_offset_seconds": float(os.environ.get("BUFFER_OFFSET_SECONDS", 16.0)),
            "auto_sync_hours": int(os.environ.get("AUTO_SYNC_HOURS", 6)),
            "github_token": os.environ.get("GITHUB_TOKEN", ""),
            "hdhomerun_enabled": True,
            "hdhomerun_name": "M3U Stream Hub",
            "hdhomerun_tuners": 4,
            "hdhomerun_mode": "favorites",
            "hdhomerun_group": "",
            "hdhomerun_source_id": "",
            "hdhomerun_max_channels": 300
        }

        # Almacén de canales en memoria indexado
        # channel_id -> channel_data
        self.channels: Dict[str, Dict] = {}
        # source_id -> list of channel_ids
        self.source_channel_map: Dict[str, List[str]] = {}
        # Mapeo de número de guía virtual para HDHomeRun (GuideNumber -> channel_id)
        self.hdhomerun_channel_map: Dict[str, str] = {}

        self._lock = asyncio.Lock()

        # Cargar configuración y fuentes previas
        self.load_settings()
        self.load_favorites()
        self.load_sources()
        self.load_custom_channels()
        self.load_github_repos()

    # -------------------------------------------------------------------------
    # Persistencia de Configuración, Favoritos y Fuentes
    # -------------------------------------------------------------------------
    def load_settings(self):
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    self.settings.update(saved)
            except Exception as e:
                logger.error(f"Error cargando settings.json: {e}")

        # Garantizar que acestream_servers exista y sea consistente
        if "acestream_servers" not in self.settings or not self.settings["acestream_servers"]:
            self.settings["acestream_servers"] = [
                {
                    "id": "direct",
                    "name": "⚡ Sin VPN",
                    "url": self.settings.get("acestream_engine_url", "http://host.docker.internal:6878"),
                    "is_default": True
                }
            ]
        if "active_acestream_server_id" not in self.settings:
            self.settings["active_acestream_server_id"] = self.settings["acestream_servers"][0]["id"]
        if "auto_failover_acestream" not in self.settings:
            self.settings["auto_failover_acestream"] = True
        if "hdhomerun_enabled" not in self.settings:
            self.settings["hdhomerun_enabled"] = True
        if "hdhomerun_name" not in self.settings:
            self.settings["hdhomerun_name"] = "M3U Stream Hub"
        if "hdhomerun_tuners" not in self.settings:
            self.settings["hdhomerun_tuners"] = 4
        if "hdhomerun_mode" not in self.settings:
            self.settings["hdhomerun_mode"] = "favorites"
        if "hdhomerun_max_channels" not in self.settings:
            self.settings["hdhomerun_max_channels"] = 300

    def save_settings(self):
        try:
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error guardando settings.json: {e}")

    def load_custom_channels(self):
        if os.path.exists(self.custom_channels_file):
            try:
                with open(self.custom_channels_file, "r", encoding="utf-8") as f:
                    self.custom_channels = json.load(f)
                    for cid, ch in self.custom_channels.items():
                        ch["is_favorite"] = cid in self.favorites
                        ch["is_custom"] = True
                        ch["source_id"] = "direct_play"
                        ch["source_name"] = "Direct Play"
                        self.channels[cid] = ch
            except Exception as e:
                logger.error(f"Error cargando custom_channels.json: {e}")

    def save_custom_channels(self):
        try:
            with open(self.custom_channels_file, "w", encoding="utf-8") as f:
                json.dump(self.custom_channels, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error guardando custom_channels.json: {e}")

    def delete_custom_channel(self, channel_id: str) -> bool:
        """Elimina un canal personalizado/directo agregado por el usuario."""
        deleted = False
        if channel_id in self.custom_channels:
            del self.custom_channels[channel_id]
            self.save_custom_channels()
            deleted = True

        if channel_id in self.channels:
            del self.channels[channel_id]
            deleted = True

        if channel_id in self.favorites:
            self.favorites.remove(channel_id)
            self.save_favorites()

        if "direct_play" in self.source_channel_map and channel_id in self.source_channel_map["direct_play"]:
            self.source_channel_map["direct_play"].remove(channel_id)

        return deleted

    def get_custom_channels(self) -> List[Dict]:
        """Devuelve los canales personalizados guardados."""
        result = []
        for cid, ch in self.custom_channels.items():
            item = dict(ch)
            item["is_favorite"] = cid in self.favorites
            item["is_custom"] = True
            result.append(item)
        return result

    def add_direct_channel(self, identifier: str, name: Optional[str] = None, save_favorite: bool = False, server_id: Optional[str] = None) -> Dict:
        import hashlib
        raw = identifier.strip().strip('"').strip("'")
        if not raw:
            raise ValueError("El identificador o enlace no puede estar vacío")

        stream_type = "direct"
        acestream_id = None
        play_url = raw

        # Detección inteligente de AceStream
        hex_pattern = r'^[a-fA-F0-9]{40,64}$'
        if re.match(hex_pattern, raw):
            stream_type = "acestream"
            acestream_id = raw.lower()
            play_url = f"http://127.0.0.1:6878/ace/getstream?id={acestream_id}"
        elif raw.startswith("acestream://"):
            clean = raw.replace("acestream://", "").split("/")[0].split("?")[0].strip()
            stream_type = "acestream"
            acestream_id = clean.lower()
            play_url = f"http://127.0.0.1:6878/ace/getstream?id={acestream_id}"
        elif "ace/getstream" in raw or "ace/manifest" in raw or "id=" in raw:
            match = re.search(r'[?&]id=([a-fA-F0-9]{40,64})', raw)
            if match:
                stream_type = "acestream"
                acestream_id = match.group(1).lower()
                play_url = f"http://127.0.0.1:6878/ace/getstream?id={acestream_id}"
        elif ".m3u8" in raw.lower():
            stream_type = "hls"

        # Generar un ID único determinista
        if acestream_id:
            ch_id = f"ace_{acestream_id[:16]}"
            default_name = f"⚡ AceStream: {acestream_id[:8]}...{acestream_id[-4:]}"
        else:
            hashed = hashlib.md5(raw.encode()).hexdigest()[:16]
            ch_id = f"custom_{hashed}"
            default_name = f"📺 Stream: {raw.split('/')[-1].split('?')[0] or raw[:20]}"

        channel_name = name.strip() if name and name.strip() else default_name

        channel = {
            "id": ch_id,
            "name": channel_name,
            "tvg_id": f"direct_{ch_id}",
            "tvg_name": channel_name,
            "tvg_logo": "",
            "group": "AceStream Directo" if stream_type == "acestream" else "Stream Directo",
            "url": play_url,
            "acestream_id": acestream_id,
            "stream_type": stream_type,
            "acestream_server_id": server_id or self.settings.get("active_acestream_server_id", "direct"),
            "source_id": "direct_play",
            "source_name": "Direct Play",
            "is_favorite": save_favorite,
            "is_custom": True
        }

        self.channels[ch_id] = channel
        self.custom_channels[ch_id] = channel
        self.save_custom_channels()

        if save_favorite:
            self.favorites.add(ch_id)
            self.save_favorites()

        return channel

    def load_favorites(self):
        if os.path.exists(self.favorites_file):
            try:
                with open(self.favorites_file, "r", encoding="utf-8") as f:
                    fav_list = json.load(f)
                    self.favorites = set(fav_list)
            except Exception as e:
                logger.error(f"Error cargando favorites.json: {e}")

    def save_favorites(self):
        try:
            with open(self.favorites_file, "w", encoding="utf-8") as f:
                json.dump(list(self.favorites), f, indent=2)
        except Exception as e:
            logger.error(f"Error guardando favorites.json: {e}")

    def toggle_favorite(self, channel_id: str) -> bool:
        if channel_id in self.favorites:
            self.favorites.remove(channel_id)
            is_fav = False
        else:
            self.favorites.add(channel_id)
            is_fav = True
        self.save_favorites()
        return is_fav

    def load_sources(self):
        if os.path.exists(self.sources_file):
            try:
                with open(self.sources_file, "r", encoding="utf-8") as f:
                    self.sources = json.load(f)
            except Exception as e:
                logger.error(f"Error cargando sources.json: {e}")
                self.sources = {}

    def save_sources(self):
        try:
            with open(self.sources_file, "w", encoding="utf-8") as f:
                json.dump(self.sources, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error guardando sources.json: {e}")

    # -------------------------------------------------------------------------
    # Persistencia y Gestión de Repositorios GitHub y Caché Local
    # -------------------------------------------------------------------------
    def load_github_repos(self):
        if os.path.exists(self.github_repos_file):
            try:
                with open(self.github_repos_file, "r", encoding="utf-8") as f:
                    self.github_repos = json.load(f)
            except Exception as e:
                logger.error(f"Error cargando github_repos.json: {e}")
                self.github_repos = {}
        else:
            self.github_repos = {}

        # Auto-descubrir repositorios desde fuentes existentes si está vacío
        if not self.github_repos and self.sources:
            for sid, s in self.sources.items():
                url = s.get("url_or_path", "")
                m = RAW_GITHUB_REGEX.search(url)
                if m:
                    owner, repo, branch, path = m.group(1), m.group(2), m.group(3), m.group(4)
                    repo_id = f"{owner.lower()}_{repo.lower()}"
                    if repo_id not in self.github_repos:
                        self.github_repos[repo_id] = {
                            "id": repo_id,
                            "url": f"https://github.com/{owner}/{repo}",
                            "owner": owner,
                            "repo": repo,
                            "branch": branch,
                            "name": f"{owner}/{repo}",
                            "added_at": int(time.time()),
                            "last_scanned": None,
                            "files_count": 0,
                            "files": [],
                            "imported_sources": []
                        }
                    if sid not in self.github_repos[repo_id]["imported_sources"]:
                        self.github_repos[repo_id]["imported_sources"].append(sid)
                    s["github_repo_id"] = repo_id
                    s["github_path"] = path
            if self.github_repos:
                self.save_github_repos()

    def save_github_repos(self):
        try:
            with open(self.github_repos_file, "w", encoding="utf-8") as f:
                json.dump(self.github_repos, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error guardando github_repos.json: {e}")

    def get_github_cache_filepath(self, repo_id: str, path: str) -> str:
        clean_path = path.replace("..", "").lstrip("/\\")
        full_path = os.path.join(self.github_cache_dir, repo_id, clean_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        return full_path

    def save_file_to_github_cache(self, repo_id: str, path: str, content: str) -> str:
        filepath = self.get_github_cache_filepath(repo_id, path)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            logger.error(f"Error guardando archivo en caché local {filepath}: {e}")
        return filepath

    def get_file_from_github_cache(self, repo_id: str, path: str) -> Optional[str]:
        filepath = self.get_github_cache_filepath(repo_id, path)
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
            except Exception as e:
                logger.error(f"Error leyendo archivo de caché local {filepath}: {e}")
        return None

    def get_github_repos(self) -> List[Dict]:
        """Devuelve la lista de repositorios GitHub guardados con estadísticas."""
        repos = []
        for rid, r in self.github_repos.items():
            imported_count = 0
            for sid, s in self.sources.items():
                if s.get("github_repo_id") == rid or (r.get("owner") and f"/{r['owner'].lower()}/{r['repo'].lower()}/" in s.get("url_or_path", "").lower()):
                    imported_count += 1

            repo_item = dict(r)
            repo_item["imported_count"] = imported_count
            repos.append(repo_item)

        repos.sort(key=lambda x: x.get("added_at") or 0, reverse=True)
        return repos

    async def add_or_update_github_repo(self, repo_input: str, scan_now: bool = True) -> Dict:
        """Añade o actualiza un repositorio GitHub guardado."""
        owner, repo, branch = parse_repo_identifier(repo_input)
        if not owner or not repo:
            raise ValueError("URL o identificador de repositorio no válido. Usa formato 'usuario/repo' o 'https://github.com/usuario/repo'.")

        repo_id = f"{owner.lower()}_{repo.lower()}"
        now = int(time.time())

        if repo_id not in self.github_repos:
            self.github_repos[repo_id] = {
                "id": repo_id,
                "url": f"https://github.com/{owner}/{repo}",
                "owner": owner,
                "repo": repo,
                "branch": branch or "main",
                "name": f"{owner}/{repo}",
                "added_at": now,
                "last_scanned": None,
                "files_count": 0,
                "files": [],
                "imported_sources": []
            }

        repo_obj = self.github_repos[repo_id]

        if scan_now:
            token = self.settings.get("github_token") or os.environ.get("GITHUB_TOKEN", "")
            scan_res = await scan_github_repository(f"{owner}/{repo}", github_token=token)
            if scan_res.get("success"):
                repo_obj["branch"] = scan_res.get("branch", repo_obj.get("branch", "main"))
                repo_obj["files_count"] = scan_res.get("files_count", 0)
                repo_obj["files"] = scan_res.get("files", [])
                repo_obj["last_scanned"] = now

        self.save_github_repos()
        return repo_obj

    async def delete_github_repo(self, repo_id: str, delete_sources: bool = False) -> bool:
        """Elimina un repositorio GitHub guardado y opcionalmente sus fuentes asociadas."""
        async with self._lock:
            if repo_id not in self.github_repos:
                return False

            repo = self.github_repos[repo_id]
            owner = repo.get("owner", "").lower()
            repo_name = repo.get("repo", "").lower()

            if delete_sources:
                sources_to_remove = []
                for sid, s in self.sources.items():
                    if s.get("github_repo_id") == repo_id:
                        sources_to_remove.append(sid)
                    elif f"/{owner}/{repo_name}/" in s.get("url_or_path", "").lower():
                        sources_to_remove.append(sid)

                for sid in sources_to_remove:
                    del self.sources[sid]
                    self._unload_source_channels(sid)
                self.save_sources()

            import shutil
            cache_folder = os.path.join(self.github_cache_dir, repo_id)
            if os.path.exists(cache_folder):
                shutil.rmtree(cache_folder, ignore_errors=True)

            del self.github_repos[repo_id]
            self.save_github_repos()
            return True

    async def update_github_repo(self, repo_id: str, reimport_all: bool = True) -> Dict:
        """
        Escanea el repositorio, descarga copias en caché local y sincroniza/reimporta canales.
        """
        if repo_id not in self.github_repos:
            raise ValueError(f"Repositorio {repo_id} no encontrado")

        repo = self.github_repos[repo_id]
        owner = repo["owner"]
        repo_name = repo["repo"]
        token = self.settings.get("github_token") or os.environ.get("GITHUB_TOKEN", "")

        # 1. Escanear
        scan_res = await scan_github_repository(f"{owner}/{repo_name}", github_token=token)
        if not scan_res.get("success"):
            raise RuntimeError(scan_res.get("error", "Error al escanear repositorio de GitHub"))

        repo["branch"] = scan_res.get("branch", repo.get("branch", "main"))
        repo["files_count"] = scan_res.get("files_count", 0)
        repo["files"] = scan_res.get("files", [])
        repo["last_scanned"] = int(time.time())
        self.save_github_repos()

        files_to_sync = repo["files"]
        if not reimport_all and repo.get("imported_sources"):
            imported_paths = set()
            for sid in repo["imported_sources"]:
                s = self.sources.get(sid)
                if s and s.get("github_path"):
                    imported_paths.add(s["github_path"].lower())
            if imported_paths:
                files_to_sync = [f for f in files_to_sync if f["path"].lower() in imported_paths]

        # 2. Descargar, almacenar en caché local y sincronizar fuentes
        updated_sources = 0
        total_channels = 0
        errors = []

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*"
        }
        if token:
            headers["Authorization"] = f"token {token}"

        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            for f in files_to_sync:
                fname = f.get("filename", "")
                fpath = f.get("path", "")
                raw_url = f.get("raw_url", "")
                if not raw_url:
                    continue

                content = None
                try:
                    r = await client.get(raw_url, headers=headers)
                    if r.status_code == 200:
                        content = r.text
                        self.save_file_to_github_cache(repo_id, fpath, content)
                except Exception as e:
                    logger.warning(f"Error descargando {raw_url}: {e}")
                    content = self.get_file_from_github_cache(repo_id, fpath)

                if content is None:
                    errors.append(f"No se pudo descargar {fname}")
                    continue

                try:
                    src = await self.add_source(
                        name=fname,
                        source_type="github",
                        url_or_path=raw_url,
                        enabled=True,
                        github_repo_id=repo_id,
                        github_path=fpath
                    )
                    sid = src["id"]
                    if sid not in repo["imported_sources"]:
                        repo["imported_sources"].append(sid)
                    updated_sources += 1
                    total_channels += src.get("channel_count", 0)
                except Exception as e:
                    errors.append(f"Error procesando {fname}: {str(e)}")

        self.save_github_repos()
        return {
            "success": True,
            "repo_id": repo_id,
            "repo_name": repo["name"],
            "files_count": repo["files_count"],
            "updated_sources": updated_sources,
            "total_channels": total_channels,
            "errors": errors
        }

    # -------------------------------------------------------------------------
    # Detección de Listas M3U Locales
    # -------------------------------------------------------------------------
    def scan_local_playlists(self) -> List[Dict]:
        """
        Escanea tanto el directorio local_playlists como rutas padre habituales
        para encontrar archivos M3U existentes (como canales_acestream.m3u, etc.).
        """
        discovered = []
        search_dirs = [
            self.local_playlists_dir,
            "/app/local_playlists",
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
            os.path.abspath(".")
        ]

        seen_paths = set()
        seen_filenames = set()
        for d in search_dirs:
            if not os.path.exists(d):
                continue
            for pattern in ["*.m3u", "*.m3u8"]:
                for filepath in glob.glob(os.path.join(d, pattern)):
                    norm_path = os.path.realpath(filepath)
                    filename = os.path.basename(norm_path)
                    if norm_path in seen_paths or filename in seen_filenames:
                        continue
                    seen_paths.add(norm_path)
                    seen_filenames.add(filename)

                    # Omitir archivos temporales o de backup
                    if filename.startswith(".") or "test" in filename.lower():
                        continue

                    try:
                        size = os.path.getsize(norm_path)
                    except OSError:
                        size = 0

                    # Comprobar si ya está dada de alta en sources
                    matched_sid = None
                    is_enabled = False
                    for sid, s in self.sources.items():
                        if s.get("type") == "local":
                            s_path = s.get("url_or_path", "")
                            if s_path == norm_path or os.path.basename(s_path) == filename:
                                matched_sid = sid
                                is_enabled = s.get("enabled", True)
                                break

                    clean_filename = re.sub(r'[^a-zA-Z0-9_]', '_', filename.lower())
                    source_id = matched_sid or f"local_{clean_filename}"
                    is_added = matched_sid is not None

                    discovered.append({
                        "id": source_id,
                        "name": filename,
                        "path": norm_path,
                        "size": size,
                        "size_formatted": format_bytes(size),
                        "is_added": is_added,
                        "enabled": is_enabled
                    })

        discovered.sort(key=lambda x: x["name"].lower())
        return discovered

    # -------------------------------------------------------------------------
    # Añadir / Gestionar Fuentes
    # -------------------------------------------------------------------------
    async def add_source(
        self,
        name: str,
        source_type: str,
        url_or_path: str,
        enabled: bool = True,
        auto_sync: bool = True,
        github_repo_id: Optional[str] = None,
        github_path: Optional[str] = None
    ) -> Dict:
        """Añade una nueva fuente M3U (GitHub, URL remota o archivo local) y sincroniza sus canales."""
        async with self._lock:
            # Normalizar URL de GitHub si es necesario
            if source_type in ("github", "url"):
                url_or_path = normalize_github_url(url_or_path.strip())

            # Comprobar si ya existe una fuente con la misma ruta o URL para evitar duplicados
            existing_id = None
            for sid, s in self.sources.items():
                if s.get("url_or_path") == url_or_path:
                    existing_id = sid
                    break
                if source_type == "local" and os.path.basename(s.get("url_or_path", "")) == os.path.basename(url_or_path):
                    existing_id = sid
                    break

            if existing_id:
                source_id = existing_id
                source_entry = self.sources[source_id]
                source_entry["name"] = name
                source_entry["type"] = source_type
                source_entry["enabled"] = enabled
                source_entry["url_or_path"] = url_or_path
                if github_repo_id:
                    source_entry["github_repo_id"] = github_repo_id
                if github_path:
                    source_entry["github_path"] = github_path
            else:
                if source_type == "local":
                    clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', os.path.basename(url_or_path).lower())
                    source_id = f"local_{clean_name}"
                else:
                    clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', name.lower())
                    source_id = f"src_{clean_name}_{int(time.time())}"

                source_entry = {
                    "id": source_id,
                    "name": name,
                    "type": source_type, # 'github', 'url', 'local'
                    "url_or_path": url_or_path,
                    "enabled": enabled,
                    "auto_sync": auto_sync,
                    "channel_count": 0,
                    "last_synced": None,
                    "status": "pending",
                    "error_message": "",
                    "github_repo_id": github_repo_id or "",
                    "github_path": github_path or ""
                }
                if not source_entry["github_repo_id"] and ("github" in source_type or "raw.githubusercontent.com" in url_or_path):
                    m_raw = RAW_GITHUB_REGEX.search(url_or_path)
                    if m_raw:
                        source_entry["github_repo_id"] = f"{m_raw.group(1).lower()}_{m_raw.group(2).lower()}"
                        source_entry["github_path"] = m_raw.group(4)

                self.sources[source_id] = source_entry

            self.save_sources()

        if enabled:
            await self.sync_source(source_id)

        return self.sources[source_id]

    async def toggle_source(self, source_id: str, enabled: Optional[bool] = None) -> bool:
        """Activa o desactiva una fuente."""
        async with self._lock:
            if source_id not in self.sources:
                raise ValueError("Fuente no encontrada")

            if enabled is None:
                new_state = not self.sources[source_id].get("enabled", True)
            else:
                new_state = enabled

            self.sources[source_id]["enabled"] = new_state
            self.save_sources()

        if new_state:
            # Si no tiene canales cargados, sincronizarla
            if source_id not in self.source_channel_map or not self.source_channel_map[source_id]:
                await self.sync_source(source_id)
        else:
            # Desactivar canales de la memoria
            async with self._lock:
                self._unload_source_channels(source_id)

        return new_state

    async def remove_source(self, source_id: str):
        """Elimina una fuente y sus canales."""
        async with self._lock:
            target_sid = None
            if source_id in self.sources:
                target_sid = source_id
            else:
                # Búsqueda de coincidencia alternativa para listas locales
                for sid, s in self.sources.items():
                    if s.get("type") == "local":
                        s_fname = os.path.basename(s.get("url_or_path", ""))
                        clean_fname = f"local_{re.sub(r'[^a-zA-Z0-9_]', '_', s_fname.lower())}"
                        if sid == clean_fname or clean_fname == source_id:
                            target_sid = sid
                            break

            if target_sid and target_sid in self.sources:
                del self.sources[target_sid]
                self.save_sources()
                self._unload_source_channels(target_sid)

    def _unload_source_channels(self, source_id: str):
        """Descarga de la memoria los canales de una fuente específica."""
        channel_ids = self.source_channel_map.pop(source_id, [])
        for ch_id in channel_ids:
            self.channels.pop(ch_id, None)

    # -------------------------------------------------------------------------
    # Sincronización de Fuentes M3U
    # -------------------------------------------------------------------------
    async def sync_source(self, source_id: str):
        """Descarga/lee y parsea los canales de una fuente."""
        if source_id not in self.sources:
            return

        source = self.sources[source_id]
        source_type = source.get("type", "url")
        url_or_path = source.get("url_or_path", "").strip()

        # Detección automática si es archivo local o URL remota
        is_local = source_type == "local" or url_or_path.startswith("/") or not url_or_path.startswith("http")

        source["status"] = "syncing"
        logger.info(f"Sincronizando fuente '{source['name']}' ({source_type}: {url_or_path})...")

        try:
            channels = []
            if is_local:
                # Leer archivo local directamente
                resolved_path = url_or_path
                if not os.path.exists(resolved_path):
                    filename = os.path.basename(url_or_path)
                    candidates = [
                        os.path.join(self.local_playlists_dir, filename),
                        os.path.join("/app/local_playlists", filename),
                        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", filename)),
                        os.path.abspath(filename),
                    ]
                    for cand in candidates:
                        if os.path.exists(cand):
                            resolved_path = cand
                            break

                if not os.path.exists(resolved_path):
                    raise FileNotFoundError(f"Archivo local no existe: {url_or_path}")

                channels = parse_m3u_file(resolved_path, source_id=source_id)
            else:
                # Descargar por HTTP
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Accept": "*/*"
                }
                token = self.settings.get("github_token") or os.environ.get("GITHUB_TOKEN", "")
                if token and ("github.com" in url_or_path or "raw.githubusercontent.com" in url_or_path):
                    headers["Authorization"] = f"token {token}"

                repo_id_for_cache = source.get("github_repo_id")
                cached_filename = source.get("github_path") or os.path.basename(url_or_path.split("?")[0])
                if not repo_id_for_cache and "raw.githubusercontent.com" in url_or_path:
                    m_raw = RAW_GITHUB_REGEX.search(url_or_path)
                    if m_raw:
                        repo_id_for_cache = f"{m_raw.group(1).lower()}_{m_raw.group(2).lower()}"
                        cached_filename = m_raw.group(4) or cached_filename
                        source["github_repo_id"] = repo_id_for_cache
                        source["github_path"] = cached_filename

                content = None
                try:
                    async with httpx.AsyncClient(timeout=35.0, follow_redirects=True) as client:
                        r = await client.get(url_or_path, headers=headers)
                        r.raise_for_status()
                        content = r.text
                        if repo_id_for_cache and content:
                            self.save_file_to_github_cache(repo_id_for_cache, cached_filename, content)
                except Exception as dl_err:
                    logger.warning(f"Error descargando {url_or_path}: {dl_err}. Buscando en copia local en caché...")
                    if repo_id_for_cache:
                        content = self.get_file_from_github_cache(repo_id_for_cache, cached_filename)
                    if not content:
                        cand = os.path.join(self.local_playlists_dir, os.path.basename(cached_filename))
                        if os.path.exists(cand):
                            with open(cand, "r", encoding="utf-8", errors="replace") as f:
                                content = f.read()
                    if not content:
                        raise dl_err
                    logger.info(f"Fuente '{source['name']}' cargada exitosamente desde caché local persistente.")

                channels = parse_m3u_text(content, source_id=source_id)

            # Cargar los canales procesados en memoria protegidos por Lock
            async with self._lock:
                self._unload_source_channels(source_id)
                ch_ids = []
                for ch in channels:
                    ch_id = ch["id"]
                    ch_ids.append(ch_id)
                    self.channels[ch_id] = ch

                self.source_channel_map[source_id] = ch_ids
                source["channel_count"] = len(ch_ids)
                source["last_synced"] = int(time.time())
                source["status"] = "ok"
                source["error_message"] = ""
                self.save_sources()

            logger.info(f"Fuente '{source['name']}' sincronizada con éxito: {len(channels)} canales.")

        except Exception as e:
            logger.error(f"Error sincronizando fuente '{source['name']}': {e}")
            source["status"] = "error"
            source["error_message"] = str(e)
            self.save_sources()

    async def sync_all_enabled_sources(self):
        """Sincroniza todas las fuentes activas."""
        sources_to_sync = [
            sid for sid, s in self.sources.items()
            if s.get("enabled", True)
        ]
        for sid in sources_to_sync:
            try:
                await self.sync_source(sid)
            except Exception as e:
                logger.error(f"Fallo sincronizando fuente {sid}: {e}")

    # -------------------------------------------------------------------------
    # Consulta y Filtrado de Canales en Memoria
    # -------------------------------------------------------------------------
    def query_channels(
        self,
        search: Optional[str] = None,
        group: Optional[str] = None,
        source_id: Optional[str] = None,
        stream_type: Optional[str] = None,
        favorite_only: bool = False,
        page: int = 1,
        limit: int = 60
    ) -> Dict:
        """
        Búsqueda de alta velocidad y paginación en memoria para soportar
        listas gigantes (ej. 50,000 canales) sin congelar la interfaz web.
        """
        matched = []
        search_terms = search.lower().strip().split() if search else []

        for ch in self.channels.values():
            # Filtro por fuente
            if source_id and ch.get("source_id") != source_id:
                continue

            # Filtro por tipo de stream (acestream, hls, video, direct)
            if stream_type and stream_type != "all":
                if ch.get("stream_type") != stream_type:
                    continue

            # Filtro por grupo/categoría
            if group and group != "all":
                if ch.get("group") != group:
                    continue

            # Filtro por favoritos
            is_fav = ch["id"] in self.favorites
            if favorite_only and not is_fav:
                continue

            # Búsqueda por texto (nombre, grupo, tvg_id, acestream_id)
            if search_terms:
                haystack = f"{ch.get('name', '')} {ch.get('group', '')} {ch.get('tvg_id', '')} {ch.get('acestream_id', '')}".lower()
                if not all(term in haystack for term in search_terms):
                    continue

            # Clonar canal y agregar metadatos de estado para la vista
            item = dict(ch)
            item["is_favorite"] = is_fav
            item["is_custom"] = bool(ch.get("is_custom", False))
            source_info = self.sources.get(ch.get("source_id", ""), {})
            item["source_name"] = "Direct Play" if item["is_custom"] else source_info.get("name", "Fuente Desconocida")
            matched.append(item)

        total_count = len(matched)
        
        # Paginación
        page = max(1, page)
        limit = max(1, min(limit, 500))
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_channels = matched[start_idx:end_idx]

        return {
            "total": total_count,
            "page": page,
            "limit": limit,
            "total_pages": (total_count + limit - 1) // limit if total_count > 0 else 1,
            "channels": paginated_channels
        }

    def get_channel_by_id(self, channel_id: str) -> Optional[Dict]:
        """Recupera un canal por su ID único."""
        ch = self.channels.get(channel_id)
        if ch:
            item = dict(ch)
            item["is_favorite"] = channel_id in self.favorites
            source_info = self.sources.get(ch.get("source_id", ""), {})
            item["source_name"] = source_info.get("name", "Fuente")
            return item
        return None

    def get_groups(self, source_id: Optional[str] = None) -> List[Dict]:
        """Obtiene la lista única de grupos/categorías con conteo de canales."""
        counts: Dict[str, int] = {}
        for ch in self.channels.values():
            if source_id and ch.get("source_id") != source_id:
                continue
            grp = ch.get("group") or "General"
            counts[grp] = counts.get(grp, 0) + 1

        sorted_groups = sorted(
            [{"name": k, "count": v} for k, v in counts.items()],
            key=lambda x: (-x["count"], x["name"].lower())
        )
        return sorted_groups

    def get_sources_summary(self) -> List[Dict]:
        """Resumen de todas las fuentes configuradas."""
        summary = []
        for sid, s in self.sources.items():
            loaded_count = len(self.source_channel_map.get(sid, []))
            summary.append({
                "id": sid,
                "name": s.get("name"),
                "type": s.get("type"),
                "url_or_path": s.get("url_or_path"),
                "enabled": s.get("enabled", True),
                "channel_count": loaded_count,
                "last_synced": s.get("last_synced"),
                "status": s.get("status", "ok"),
                "error_message": s.get("error_message", "")
            })
        summary.sort(key=lambda x: (x.get("name") or "").lower())
        return summary

    # -------------------------------------------------------------------------
    # Emulador HDHomeRun (Plex, Jellyfin, Emby)
    # -------------------------------------------------------------------------
    def get_hdhomerun_lineup(
        self,
        base_url: str,
        favorites_only: Optional[bool] = None,
        group: Optional[str] = None,
        source_id: Optional[str] = None,
        stream_type: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict]:
        """
        Genera la parrilla de canales adaptada para el sintonizador HDHomeRun
        (Plex, Jellyfin, Emby), mapeando números de guía y manteniendo un índice en memoria.
        """
        mode = self.settings.get("hdhomerun_mode", "favorites")
        max_ch = limit or int(self.settings.get("hdhomerun_max_channels", 300))
        grp_filter = group or self.settings.get("hdhomerun_group") or None
        src_filter = source_id or self.settings.get("hdhomerun_source_id") or None

        use_favorites = favorites_only if favorites_only is not None else (mode == "favorites")
        selected_channels = []

        if use_favorites:
            for cid in self.favorites:
                if cid in self.channels:
                    selected_channels.append(self.channels[cid])

            # Si aún no hay favoritos guardados, tomar los primeros canales de fuentes habilitadas
            if not selected_channels:
                for cid, ch in self.channels.items():
                    if len(selected_channels) >= min(max_ch, 50):
                        break
                    s = self.sources.get(ch.get("source_id", ""))
                    if s and not s.get("enabled", True):
                        continue
                    selected_channels.append(ch)
        else:
            for cid, ch in self.channels.items():
                s = self.sources.get(ch.get("source_id", ""))
                if s and not s.get("enabled", True):
                    continue

                if mode == "acestream" or stream_type == "acestream":
                    if not ch.get("acestream_id"):
                        continue

                if grp_filter and grp_filter != "all" and ch.get("group") != grp_filter:
                    continue

                if src_filter and src_filter != "all" and ch.get("source_id") != src_filter:
                    continue

                selected_channels.append(ch)
                if len(selected_channels) >= max_ch:
                    break

        selected_channels = selected_channels[:max_ch]

        lineup = []
        new_channel_map = {}

        for idx, ch in enumerate(selected_channels, start=1):
            guide_num = str(idx)
            cid = ch["id"]
            new_channel_map[guide_num] = cid

            name = ch.get("name") or f"Canal {guide_num}"
            stream_url = f"{base_url}/auto/v{guide_num}"

            lineup.append({
                "GuideNumber": guide_num,
                "GuideName": name,
                "URL": stream_url,
                "ChannelID": cid,
                "Station": ch.get("tvg_id") or name,
                "StreamType": ch.get("stream_type", "direct"),
                "Group": ch.get("group", "General")
            })

        self.hdhomerun_channel_map = new_channel_map
        return lineup
