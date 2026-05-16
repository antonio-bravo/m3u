#!/usr/bin/env python3
"""
Extractor de AcestreamIDs para Arena4Viewer - Versión OPTIMIZADA
"""
import os
import re
import sys
import logging
import base64
from datetime import datetime, timedelta
from urllib.parse import urlparse
import requests
import urllib3

# Desactivar advertencias de SSL para los servidores con certificados expirados
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== CONFIGURACIÓN ====================
OUTPUT_FILE = "arena4viewer.m3u"
LOG_FILE = "arena4viewer.log"

# Credenciales detectadas en la lógica de la app
API_KEY = "fc8c75bd41f06b0fa1d32c8b0b76493d"
# La app suele enviar la fecha actual o una fija para la validación
EXPIRE_DATE = datetime.now().strftime("%Y%m%d")
AGENDA_FILE = "misguia2.php"

# Dominios extraídos de strings.xml
ARENA_URLS = [
    "https://www.arena4viewer.in",
    "https://www.arena4viewer.pl",
    "https://www.arena4viewer.co.in",
    "https://www.arena4viewer.cool",
    "https://www.arena4viewer.top",
    "https://www.arena4viewer.lv"
]

# Cabeceras exactas que usa la app (com.bone.android.a4v.oficial)
HEADERS = {
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 7 Build/UQ1A.231205.015)",
    "X-Requested-With": "com.bone.android.a4v.oficial",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept-Encoding": "gzip",
    "Connection": "Keep-Alive"
}

# Regex mejorado para capturar av1#acestream://ID o AV 1#acestream://ID
CANAL_PATTERN = re.compile(r'av\s*(\d{1,3})\s*#acestream://([a-fA-F0-9]{40})', re.I)

# ==================== LOGGING ====================
def configurar_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )

# ==================== LÓGICA DE EXTRACCIÓN ====================

def fetch_channels(server_url):
    """Realiza la petición POST emulando a la app"""
    target = f"{server_url.rstrip('/')}/{AGENDA_FILE}"
    # El payload que espera el PHP
    payload = {
        "key": API_KEY,
        "expire": EXPIRE_DATE
    }
    
    try:
        session = requests.Session()
        # Primero intentamos con POST que es el método oficial
        response = session.post(
            target, 
            data=payload, 
            headers=HEADERS, 
            timeout=15, 
            verify=False
        )
        
        if response.status_code == 200:
            return response.text
        else:
            logging.warning(f"⚠️ Servidor {server_url} respondió con status {response.status_code}")
            return None
    except Exception as e:
        logging.error(f"❌ Error conectando a {server_url}: {e}")
        return None

def parse_channels(html_content):
    """Extrae los IDs buscando en el div oculto o en todo el texto"""
    channels = {}
    
    # Limpieza básica de entidades HTML por si acaso
    clean_html = html_content.replace('&nbsp;', ' ').replace('&#160;', ' ')
    
    # Buscar primero dentro del div "streams" mencionado en la descompilación
    streams_div = re.search(r'class="streams"[^>]*>(.*?)</div>', clean_html, re.DOTALL | re.I)
    search_area = streams_div.group(1) if streams_div else clean_html
    
    matches = CANAL_PATTERN.findall(search_area)
    for num, ace_id in matches:
        channels[int(num)] = ace_id.lower()
        
    return channels

def generar_m3u(channels, server_used):
    """Crea el archivo M3U compatible con Ace Stream"""
    if not channels:
        return False
    
    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            f.write(f"# Arena4Viewer - Actualizado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Fuente: {server_used}\n\n")
            
            for num in sorted(channels.keys()):
                ace_id = channels[num]
                f.write(f'#EXTINF:-1 tvg-id="AV{num}" tvg-logo="" group-title="ArenaVision",ArenaVision {num}\n')
                # URL para motor Ace Stream local (ajusta el puerto si es necesario)
                f.write(f"acestream://{ace_id}\n\n")
        return True
    except Exception as e:
        logging.error(f"💥 Error al escribir M3U: {e}")
        return False

# ==================== EJECUCIÓN ====================

def main():
    configurar_logging()
    logging.info("🚀 Iniciando extracción de Arena4Viewer...")
    
    found_channels = {}
    successful_server = ""

    for url in ARENA_URLS:
        logging.info(f"📡 Probando servidor: {url}")
        content = fetch_channels(url)
        
        if content:
            channels = parse_channels(content)
            if channels:
                found_channels = channels
                successful_server = url
                logging.info(f"✅ ¡Éxito! Encontrados {len(channels)} canales en {url}")
                break
            else:
                logging.warning(f"❓ Conexión ok pero no se encontraron canales en {url}")
    
    if found_channels:
        if generar_m3u(found_channels, successful_server):
            logging.info(f"✨ Archivo '{OUTPUT_FILE}' generado correctamente.")
            logging.info(f"📺 Rango de canales: {min(found_channels.keys())} al {max(found_channels.keys())}")
    else:
        logging.error("❌ No se pudo obtener la lista de canales de ninguna fuente.")

if __name__ == "__main__":
    main()
