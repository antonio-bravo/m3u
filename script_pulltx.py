#!/usr/bin/env python3
"""
Script para generar y verificar listas M3U a partir de servidores Tencent Cloud PullTX
Ejemplo de origen: https://pulltx.klmysrmyy.com/live/211_5879715_1.m3u8?txSecret=...&txTime=...
"""

import hashlib
import time
import requests

# Configuración del servidor y token base
DEFAULT_HOST = "https://pulltx.klmysrmyy.com"
AUTH_USER = "paco"  # Usuario HTTP auth opcional (si se usa en la URL)

# Parámetros del enlace de muestra enviado por el usuario
SAMPLE_STREAM_ID = "211_5879715_1"
SAMPLE_TX_SECRET = "6cd9504b767b8675a091af77d285694e"
SAMPLE_TX_TIME = "6ACA028A"

# Clave secreta para generación de firmas txSecret (si se conoce la clave del CDN)
# Fórmula Tencent Cloud: MD5(SecretKey + StreamName + txTime)
TX_SECRET_KEY = ""  # Dejar en blanco si se reutilizan los tokens de URL

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Connection": "keep-alive"
}

def generate_tx_secret(stream_name: str, tx_time_hex: str, secret_key: str) -> str:
    """Calcula la firma txSecret utilizando el estándar de Tencent Cloud Live Streaming Anti-Leech."""
    if not secret_key:
        return ""
    raw = f"{secret_key}{stream_name}{tx_time_hex}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()

def get_current_tx_time_hex(validity_seconds: int = 86400) -> str:
    """Genera la marca de tiempo txTime en formato Hexadecimal (UTC)."""
    expire_timestamp = int(time.time()) + validity_seconds
    return hex(expire_timestamp)[2:].upper()

def build_stream_url(host: str, stream_id: str, tx_secret: str, tx_time: str, user: str = None) -> str:
    """Construye la URL m3u8 completa del canal."""
    if user:
        # Formato con usuario en URL ej: https://paco@pulltx...
        if "://" in host:
            proto, domain = host.split("://", 1)
            base_url = f"{proto}://{user}@{domain}"
        else:
            base_url = f"https://{user}@{host}"
    else:
        base_url = host

    url = f"{base_url}/live/{stream_id}.m3u8"
    params = []
    if tx_secret:
        params.append(f"txSecret={tx_secret}")
    if tx_time:
        params.append(f"txTime={tx_time}")
    
    if params:
        url += "?" + "&".join(params)
    return url

def check_stream_status(url: str, timeout: int = 5) -> bool:
    """Verifica si la URL del stream responde correctamente (HTTP 200/206/302)."""
    try:
        response = requests.head(url, headers=HEADERS, timeout=timeout, allow_redirects=True, verify=False)
        if response.status_code in [200, 206, 302]:
            return True
        # Probar con GET parcial si HEAD no es soportado por el CDN
        response = requests.get(url, headers=HEADERS, timeout=timeout, stream=True, verify=False)
        return response.status_code in [200, 206, 302]
    except Exception:
        return False

def scan_channel_range(prefix: int = 211, start_id: int = 5879700, count: int = 30, quality: int = 1, verify_online: bool = False):
    """
    Escanea un rango de IDs de canal y genera las entradas M3U.
    """
    channels = []
    tx_time = SAMPLE_TX_TIME
    tx_secret = SAMPLE_TX_SECRET

    print(f"[*] Generando lista de canales para prefijo {prefix} (rango {start_id} - {start_id + count})...")

    for i in range(count):
        channel_num = start_id + i
        stream_id = f"{prefix}_{channel_num}_{quality}"
        
        # Si tenemos clave secreta, generamos un token nuevo para cada canal
        if TX_SECRET_KEY:
            curr_tx_time = get_current_tx_time_hex()
            curr_tx_secret = generate_tx_secret(stream_id, curr_tx_time, TX_SECRET_KEY)
        else:
            # Reutilizar parámetros del token actual
            curr_tx_time = tx_time
            curr_tx_secret = tx_secret

        url = build_stream_url(DEFAULT_HOST, stream_id, curr_tx_secret, curr_tx_time, user=AUTH_USER)
        channel_title = f"Canal {prefix} - {channel_num}"

        if verify_online:
            is_live = check_stream_status(url)
            status_str = "ONLINE" if is_live else "OFFLINE"
            print(f"  -> [{status_str}] {channel_title}: {url}")
            if not is_live:
                continue
        else:
            print(f"  -> {channel_title}")

        channels.append({
            "title": channel_title,
            "id": stream_id,
            "url": url
        })

    return channels

def save_m3u(channels, output_filename="lista_pulltx.m3u"):
    """Guarda la lista de canales en un archivo M3U con formato estándar."""
    lines = ["#EXTM3U"]
    for ch in channels:
        lines.append(f'#EXTINF:-1 tvg-id="{ch["id"]}" group-title="PullTX Live",{ch["title"]}')
        lines.append(ch["url"])
    
    content = "\n".join(lines) + "\n"
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write(content)
    
    print(f"[+] Lista M3U guardada con éxito en '{output_filename}' ({len(channels)} canales).")

if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    # Escanear rango alrededor del ID proporcionado (5879715)
    channels_list = scan_channel_range(
        prefix=211,
        start_id=5879700,
        count=30,
        quality=1,
        verify_online=False  # Cambiar a True si el dominio resuelve en red local
    )
    
    save_m3u(channels_list, "lista_pulltx.m3u")
