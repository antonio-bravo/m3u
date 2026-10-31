import os
import re
import logging
from typing import Dict, List, Optional, Tuple
import httpx

logger = logging.getLogger("m3u_stream_proxy.github")

GITHUB_URL_REGEX = re.compile(r'github\.com/([^/]+)/([^/]+)(?:/(?:tree|blob|raw)/([^/]+)(?:/(.*))?)?', re.IGNORECASE)
RAW_GITHUB_REGEX = re.compile(r'raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.*)', re.IGNORECASE)


def format_bytes(size: int) -> str:
    """Convierte bytes a formato legible (KB, MB, GB)."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"


def normalize_github_url(url: str) -> str:
    """
    Si se introduce una URL de visualización en GitHub como:
    https://github.com/owner/repo/blob/main/path/to/file.m3u
    o https://github.com/owner/repo/raw/main/path/to/file.m3u
    o https://github.com/owner/repo/blob/refs/heads/main/path/to/file.m3u
    La convierte a su enlace crudo directo:
    https://raw.githubusercontent.com/owner/repo/main/path/to/file.m3u
    """
    m = GITHUB_URL_REGEX.search(url)
    if m and any(part in url.lower() for part in ("/blob/", "/raw/")):
        owner = m.group(1)
        repo = m.group(2).removesuffix(".git")
        branch = m.group(3) or "main"
        path = m.group(4) or ""
        if branch.startswith("refs/heads/"):
            branch = branch.removeprefix("refs/heads/")
        elif branch == "refs" and path.startswith("heads/"):
            parts = path.split("/", 2)
            if len(parts) >= 2:
                branch = parts[1]
                path = parts[2] if len(parts) > 2 else ""
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    return url


def parse_repo_identifier(repo_input: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extrae (owner, repo, branch) a partir de:
    - https://github.com/owner/repo
    - https://github.com/owner/repo/tree/master
    - https://raw.githubusercontent.com/owner/repo/main/path...
    - owner/repo
    """
    cleaned = repo_input.strip()

    # 0. Probar URL raw de GitHub
    m_raw = RAW_GITHUB_REGEX.search(cleaned)
    if m_raw:
        owner = m_raw.group(1)
        repo = m_raw.group(2).removesuffix(".git")
        branch = m_raw.group(3)
        return owner, repo, branch

    # 1. Probar URL estándar de GitHub
    m = GITHUB_URL_REGEX.search(cleaned)
    if m:
        owner = m.group(1)
        repo = m.group(2).removesuffix(".git")
        branch = m.group(3) # Puede ser None
        return owner, repo, branch

    # 2. Probar formato simple "owner/repo"
    if "/" in cleaned and not cleaned.startswith("http"):
        parts = cleaned.split("/")
        if len(parts) >= 2:
            owner = parts[0].strip()
            repo = parts[1].strip().removesuffix(".git")
            branch = parts[2].strip() if len(parts) >= 3 else None
            return owner, repo, branch

    return None, None, None


async def scan_github_repository(
    repo_input: str,
    github_token: Optional[str] = None
) -> Dict:
    """
    Escanea un repositorio de GitHub utilizando su API pública para descubrir
    todos los archivos con extensión .m3u o .m3u8.
    """
    owner, repo, specified_branch = parse_repo_identifier(repo_input)
    if not owner or not repo:
        return {
            "success": False,
            "error": "URL o identificador de repositorio no válido. Usa formato 'usuario/repo' o 'https://github.com/usuario/repo'."
        }

    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "M3U-Stream-Hub-Scanner/1.0"
    }
    
    token = github_token or os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"token {token}"

    async with httpx.AsyncClient(timeout=25.0) as client:
        # Paso 1: Obtener la rama por defecto si no fue especificada
        branch = specified_branch
        if not branch:
            repo_info_url = f"https://api.github.com/repos/{owner}/{repo}"
            try:
                r = await client.get(repo_info_url, headers=headers)
                if r.status_code == 404:
                    return {"success": False, "error": f"Repositorio '{owner}/{repo}' no encontrado o es privado."}
                elif r.status_code == 403:
                    return {"success": False, "error": "Límite de peticiones de GitHub API alcanzado. Puedes configurar un GitHub Token en los Ajustes."}
                r.raise_for_status()
                data = r.json()
                branch = data.get("default_branch", "main")
            except Exception as e:
                logger.warning(f"Error consultando repo {owner}/{repo}: {e}")
                branch = "main"

        # Paso 2: Obtener el árbol recursivo de archivos
        tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        try:
            r = await client.get(tree_url, headers=headers)
            if r.status_code == 404 and specified_branch is None and branch == "main":
                # Intentar fallback a master
                branch = "master"
                tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
                r = await client.get(tree_url, headers=headers)

            r.raise_for_status()
            tree_data = r.json()
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"Error de GitHub ({e.response.status_code}): {e.response.text[:200]}"}
        except Exception as e:
            return {"success": False, "error": f"No se pudo consultar el repositorio: {str(e)}"}

        tree = tree_data.get("tree", [])
        m3u_files = []

        for item in tree:
            if item.get("type") == "blob":
                path = item.get("path", "")
                path_lower = path.lower()
                if path_lower.endswith((".m3u", ".m3u8")):
                    size = item.get("size", 0)
                    filename = os.path.basename(path)
                    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
                    github_url = f"https://github.com/{owner}/{repo}/blob/{branch}/{path}"

                    m3u_files.append({
                        "filename": filename,
                        "path": path,
                        "size": size,
                        "size_formatted": format_bytes(size),
                        "raw_url": raw_url,
                        "github_url": github_url,
                        "branch": branch
                    })

        # Ordenar alfabéticamente por ruta
        m3u_files.sort(key=lambda x: x["path"].lower())

        return {
            "success": True,
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "files_count": len(m3u_files),
            "files": m3u_files
        }
