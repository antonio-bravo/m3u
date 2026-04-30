#!/usr/bin/env python3
"""
Arena4Viewer Channel Fetcher
============================
Workflow que intenta obtener canales de múltiples servidores de Arena4Viewer.
Si una fuente falla, genera un warning pero continúa con las siguientes.
Genera: arena4viewer.m3u
"""

import requests
from bs4 import BeautifulSoup
import warnings
import sys
from datetime import datetime
import json
import os

# URLs de los servidores de Arena4Viewer
ARENA4VIEWER_URLS = [
    ("Principal", "http://www.arena4viewer.in/misguia2.php"),
    ("Alternativo 1", "http://www.arena4viewer.pl/misguia2.php"),
    ("Alternativo 2", "https://www.arena4viewer.co.in/misguia2.php"),
    ("Alternativo 3", "https://www.arena4viewer.cool/misguia2.php"),
    ("Alternativo 4", "https://www.arena4viewer.lv/misguia2.php"),
    ("Alternativo 5", "https://www.arena4viewer.top/misguia2.php"),
]

# URLs de streams adicionales
STREAM_URLS = [
    "http://www.livefootballol.me/channels/",
    "http://arenav.bget.ru/",
    "http://es.live3s.com/",
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
}

OUTPUT_FILE = "arena4viewer.m3u"
LOG_FILE = "arena4viewer.log"

def log_message(message, level="INFO"):
    """Registra mensajes en consola y archivo de log."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] [{level}] {message}"
    print(log_entry)
    
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry + "\n")

def fetch_arena4viewer_channels():
    """Intenta obtener canales de las diferentes fuentes de Arena4Viewer."""
    all_channels = []
    successful_source = None
    
    for source_name, url in ARENA4VIEWER_URLS:
        try:
            log_message(f"Intentando obtener canales de: {source_name} ({url})", "INFO")
            
            response = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
            
            if response.status_code == 200:
                html_content = response.text
                
                # Buscar el div con los streams
                soup = BeautifulSoup(html_content, 'html.parser')
                streams_div = soup.find('div', class_='streams')
                
                if streams_div and streams_div.get('style') == 'display:none;':
                    # Extraer streams del div oculto
                    channels = parse_streams_from_div(streams_div)
                    if channels:
                        all_channels.extend(channels)
                        successful_source = source_name
                        log_message(f"✓ Canales obtenidos exitosamente de: {source_name}", "INFO")
                        break  # Salir del loop si tenemos éxito
                else:
                    # Intentar buscar streams de otra forma
                    channels = parse_streams_from_html(html_content)
                    if channels:
                        all_channels.extend(channels)
                        successful_source = source_name
                        log_message(f"✓ Canales obtenidos de: {source_name}", "INFO")
                        break
            else:
                log_message(f"⚠ Error HTTP {response.status_code} en {source_name}", "WARNING")
                
        except requests.Timeout:
            log_message(f"⚠ Timeout conectando a {source_name}", "WARNING")
        except requests.ConnectionError as e:
            log_message(f"⚠ Error de conexión con {source_name}: {str(e)[:50]}", "WARNING")
        except Exception as e:
            log_message(f"⚠ Error inesperado en {source_name}: {str(e)[:50]}", "WARNING")
            continue
    
    return all_channels, successful_source

def parse_streams_from_div(streams_div):
    """Parsea streams desde el div oculto."""
    channels = []
    
    # El contenido puede estar en texto o como atributos
    streams_text = streams_div.get_text(strip=True)
    
    if streams_text:
        # Parsear líneas de streams
        for line in streams_text.split('\n'):
            line = line.strip()
            if not line:
                continue
                
            if line.startswith('acestream://'):
                channel_id = line.replace('acestream://', '')
                channels.append({
                    'name': f'AV-{channel_id[:8]}',
                    'url': f'http://127.0.0.1:6878/ace/getstream?id={channel_id}',
                    'type': 'acestream'
                })
            elif line.startswith('sop://'):
                channel_id = line.replace('sop://', '')
                channels.append({
                    'name': f'SOP-{channel_id[:8]}',
                    'url': f'sop://{channel_id}',
                    'type': 'sopcast'
                })
            elif line.startswith('http://') or line.startswith('https://'):
                channels.append({
                    'name': f'WEB-{line[:30]}',
                    'url': line,
                    'type': 'http'
                })
    
    return channels

def parse_streams_from_html(html_content):
    """Parsea streams desde el HTML general."""
    channels = []
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Buscar todos los enlaces que contengan streams
    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        
        if 'acestream://' in href or 'ace/getstream' in href:
            if 'acestream://' in href:
                channel_id = href.split('acestream://')[1]
                url = f'http://127.0.0.1:6878/ace/getstream?id={channel_id}'
            else:
                channel_id = href.split('id=')[1] if 'id=' in href else href
                url = href
                
            channels.append({
                'name': f'AV-{channel_id[:8]}',
                'url': url,
                'type': 'acestream'
            })
        elif 'sop://' in href:
            channel_id = href.replace('sop://', '')
            channels.append({
                'name': f'SOP-{channel_id[:8]}',
                'url': href,
                'type': 'sopcast'
            })
        elif href.startswith('http://') or href.startswith('https://'):
            # Filtrar solo URLs de streaming válidas
            if any(x in href for x in ['stream', 'live', 'watch', 'embed', 'player']):
                channels.append({
                    'name': link.get_text(strip=True) or f'WEB-{len(channels)+1}',
                    'url': href,
                    'type': 'http'
                })
    
    return channels

def fetch_additional_streams():
    """Obtiene streams adicionales de otras fuentes."""
    all_streams = []
    
    for url in STREAM_URLS:
        try:
            log_message(f"Obteniendo streams de: {url}", "INFO")
            response = requests.get(url, headers=HEADERS, timeout=20)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Buscar enlaces de stream
                for link in soup.find_all('a', href=True):
                    href = link.get('href', '')
                    if href.startswith('http'):
                        name = link.get_text(strip=True) or href.split('/')[-1]
                        all_streams.append({
                            'name': name,
                            'url': href,
                            'type': 'http'
                        })
                        
        except Exception as e:
            log_message(f"⚠ Error obteniendo streams de {url}: {str(e)[:30]}", "WARNING")
            continue
    
    return all_streams

def generate_m3u(channels):
    """Genera el archivo M3U con los canales."""
    m3u_content = ["#EXTM3U"]
    
    for i, channel in enumerate(channels, 1):
        # Crear nombre de canal
        channel_name = channel.get('name', f'Canal {i}')
        channel_type = channel.get('type', 'video')
        
        # ExtINF line con metadatos
        extinf = f'#EXTINF:-1 tvg-name="{channel_name}" tvg-type="{channel_type}" group-title="Arena4Viewer",{channel_name}'
        m3u_content.append(extinf)
        m3u_content.append(channel['url'])
    
    return "\n".join(m3u_content) + "\n"

def main():
    """Función principal del workflow."""
    print("=" * 60)
    print("Arena4Viewer Channel Fetcher")
    print("=" * 60)
    
    # Inicializar archivo de log
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"=== Ejecución iniciada: {datetime.now().isoformat()} ===\n")
    
    log_message("Iniciando workflow de Arena4Viewer", "INFO")
    
    all_channels = []
    
    # 1. Intentar obtener de fuentes principales de Arena4Viewer
    log_message("=" * 40, "INFO")
    log_message("FASE 1: Obteniendo de servidores Arena4Viewer", "INFO")
    log_message("=" * 40, "INFO")
    
    arena4_channels, source = fetch_arena4viewer_channels()
    
    if arena4_channels:
        all_channels.extend(arena4_channels)
        log_message(f"✓ Obtenidos {len(arena4_channels)} canales de Arena4Viewer", "INFO")
    else:
        log_message("⚠ No se pudieron obtener canales de Arena4Viewer", "WARNING")
    
    # 2. Intentar obtener streams adicionales
    log_message("=" * 40, "INFO")
    log_message("FASE 2: Obteniendo streams adicionales", "INFO")
    log_message("=" * 40, "INFO")
    
    additional_streams = fetch_additional_streams()
    
    if additional_streams:
        all_channels.extend(additional_streams)
        log_message(f"✓ Obtenidos {len(additional_streams)} streams adicionales", "INFO")
    else:
        log_message("⚠ No se pudieron obtener streams adicionales", "WARNING")
    
    # 3. Eliminar duplicados
    if all_channels:
        seen = set()
        unique_channels = []
        for channel in all_channels:
            if channel['url'] not in seen:
                seen.add(channel['url'])
                unique_channels.append(channel)
        
        all_channels = unique_channels
        log_message(f"Total de canales únicos: {len(all_channels)}", "INFO")
    
    # 4. Generar archivo M3U
    if all_channels:
        m3u_content = generate_m3u(all_channels)
        
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(m3u_content)
        
        log_message(f"✓ Archivo {OUTPUT_FILE} generado con {len(all_channels)} canales", "INFO")
        print(f"\n✓ Archivo generado: {OUTPUT_FILE}")
        print(f"  Canales: {len(all_channels)}")
    else:
        # Crear archivo vacío con header
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n# Canales no disponibles\n")
        
        log_message("⚠ No se pudieron obtener canales de ninguna fuente", "WARNING")
        print(f"\n⚠ Archivo {OUTPUT_FILE} generado vacío (sin canales disponibles)")
    
    # Resumen final
    log_message("=" * 40, "INFO")
    log_message("RESUMEN FINAL", "INFO")
    log_message("=" * 40, "INFO")
    log_message(f"Canales totales: {len(all_channels)}", "INFO")
    log_message(f"Archivo de salida: {OUTPUT_FILE}", "INFO")
    log_message(f"Log: {LOG_FILE}", "INFO")
    
    print("\n" + "=" * 60)
    print("Workflow completado")
    print("=" * 60)

if __name__ == "__main__":
    main()