import re
import hashlib
from typing import Dict, List, Optional, Generator, Tuple

# Regex para extraer atributos de #EXTINF
ATTR_REGEX = re.compile(r'([a-zA-Z0-9_\-]+)="([^"]*)"')

# Regex para detectar IDs de AceStream (hash SHA-1 de 40 caracteres hexadecimales)
ACESTREAM_HASH_REGEX = re.compile(r'\b([a-fA-F0-9]{40})\b')
ACESTREAM_SCHEME_REGEX = re.compile(r'acestream://([a-fA-F0-9]{40})', re.IGNORECASE)
ACESTREAM_HTTP_REGEX = re.compile(r'/ace/(?:getstream|manifest\.m3u8)\?id=([a-fA-F0-9]{40})', re.IGNORECASE)
ACESTREAM_QUERY_REGEX = re.compile(r'[?&]id=([a-fA-F0-9]{40})', re.IGNORECASE)


def detect_acestream_id(url: str) -> Optional[str]:
    """Detecta si una URL contiene un enlace o ID de AceStream y devuelve el hash de 40 caracteres."""
    if not url:
        return None
    
    # 1. acestream://<hash>
    m = ACESTREAM_SCHEME_REGEX.search(url)
    if m:
        return m.group(1).lower()

    # 2. http://127.0.0.1:6878/ace/getstream?id=<hash> o manifest.m3u8?id=<hash>
    m = ACESTREAM_HTTP_REGEX.search(url)
    if m:
        return m.group(1).lower()

    # 3. Query param id=<hash>
    m = ACESTREAM_QUERY_REGEX.search(url)
    if m:
        return m.group(1).lower()

    # 4. Comprobar si la URL contiene "ace" o "acestream" y un hash de 40 caracteres
    url_lower = url.lower()
    if "ace" in url_lower or "torrent" in url_lower:
        m = ACESTREAM_HASH_REGEX.search(url)
        if m:
            return m.group(1).lower()

    return None


def detect_stream_type(url: str, acestream_id: Optional[str] = None) -> str:
    """Clasifica el tipo de stream: acestream, hls, video, o direct."""
    if acestream_id:
        return "acestream"
    
    clean = url.lower().split("?")[0]
    if clean.endswith(".m3u8") or ".m3u8" in clean or "/hls/" in clean:
        return "hls"
    elif clean.endswith((".mp4", ".mkv", ".avi", ".ts", ".webm")):
        return "video"
    else:
        # Por defecto consideramos que puede ser HLS o stream directo HTTP
        if "live" in clean or "stream" in clean or "play" in clean:
            return "hls"
        return "direct"


def parse_m3u_lines(lines: Generator[str, None, None], source_id: str = "") -> Generator[Dict, None, None]:
    """
    Parsea líneas de un archivo M3U/M3U8 de forma eficiente y en streaming.
    Retorna un generador de diccionarios con la información de cada canal.
    """
    current_channel = None
    channel_index = 0

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#EXTINF:"):
            # Nuevo canal
            current_channel = {
                "source_id": source_id,
                "name": "Canal Desconocido",
                "tvg_id": "",
                "tvg_name": "",
                "tvg_logo": "",
                "group": "General",
                "url": "",
                "acestream_id": None,
                "stream_type": "direct"
            }

            # Separar atributos del título (el título va tras la última coma)
            # Ejemplo: #EXTINF:-1 tvg-id="123" group-title="Deportes", Nombre Canal
            comma_idx = line.rfind(",")
            if comma_idx != -1:
                title_part = line[comma_idx + 1:].strip()
                meta_part = line[8:comma_idx].strip()
                if title_part:
                    current_channel["name"] = title_part
            else:
                meta_part = line[8:].strip()

            # Extraer atributos clave="valor"
            attrs = ATTR_REGEX.findall(meta_part)
            for key, val in attrs:
                k_lower = key.lower()
                if k_lower == "tvg-id":
                    current_channel["tvg_id"] = val
                elif k_lower == "tvg-name":
                    current_channel["tvg_name"] = val
                elif k_lower == "tvg-logo":
                    current_channel["tvg_logo"] = val
                elif k_lower == "group-title":
                    current_channel["group"] = val.strip() or "General"

            # Si el nombre sigue vacío pero hay tvg-name, usarlo
            if (not current_channel["name"] or current_channel["name"] == "Canal Desconocido") and current_channel["tvg_name"]:
                current_channel["name"] = current_channel["tvg_name"]

        elif line.startswith("#"):
            # Otras directivas M3U (#EXTM3U, #EXTVLCOPT, etc.)
            continue
        else:
            # Es una línea de URL
            if current_channel is not None:
                current_channel["url"] = line
                ace_id = detect_acestream_id(line)
                current_channel["acestream_id"] = ace_id
                current_channel["stream_type"] = detect_stream_type(line, ace_id)

                # Generar ID único persistente
                channel_index += 1
                unique_key = f"{source_id}:{channel_index}:{current_channel['name']}:{line}"
                current_channel["id"] = hashlib.md5(unique_key.encode("utf-8")).hexdigest()[:16]

                yield current_channel
                current_channel = None
            else:
                # Caso donde la URL aparece sin #EXTINF previo
                ace_id = detect_acestream_id(line)
                channel_index += 1
                ch_name = f"Stream {channel_index}"
                if ace_id:
                    ch_name = f"AceStream {ace_id[:8]}..."
                
                unique_key = f"{source_id}:{channel_index}:{line}"
                yield {
                    "id": hashlib.md5(unique_key.encode("utf-8")).hexdigest()[:16],
                    "source_id": source_id,
                    "name": ch_name,
                    "tvg_id": "",
                    "tvg_name": "",
                    "tvg_logo": "",
                    "group": "General",
                    "url": line,
                    "acestream_id": ace_id,
                    "stream_type": detect_stream_type(line, ace_id)
                }


def parse_m3u_text(content: str, source_id: str = "") -> List[Dict]:
    """Parsea el contenido completo de texto de una lista M3U."""
    def line_generator():
        for l in content.splitlines():
            yield l

    return list(parse_m3u_lines(line_generator(), source_id=source_id))


def parse_m3u_file(file_path: str, source_id: str = "") -> List[Dict]:
    """Parsea un archivo M3U local leyendo línea por línea (ideal para archivos de muchos MB)."""
    def file_line_generator():
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for l in f:
                yield l

    return list(parse_m3u_lines(file_line_generator(), source_id=source_id))
