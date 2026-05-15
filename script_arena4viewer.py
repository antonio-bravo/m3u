#!/usr/bin/env python3
"""
Extractor de AcestreamIDs para Arena4Viewer - Versión CORREGIDA + Fallback
"""
import os
import re
import sys
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse
import requests

# ==================== CONFIGURACIÓN ====================
OUTPUT_FILE = "arena4viewer.m3u"
LOG_FILE = "arena4viewer.log"
CARPETA_HTML = "canales_html"

# 🔧 DEBUG: Guardar HTMLs para diagnóstico (ACTIVADO por defecto para debug)
DEBUG_SAVE_HTML = False  # ← Cambia a False cuando funcione para ahorrar espacio

API_KEY = "fc8c75bd41f06b0fa1d32c8b0b76493d"
EXPIRE_DATE = (datetime.now() + timedelta(days=30)).strftime("%Y%m%d")
AGENDA_FILE = "misguia2.php"

ARENA_URLS = [
    "http://www.arena4viewer.in",
    "https://www.arena4viewer.pl",
    "https://www.arena4viewer.co.in",
    "https://www.arena4viewer.lv",
    "https://www.arena4viewer.top",

    "http://arena4viewer.in",
    "http://arena4viewer.pl",
    "https://arena4viewer.co.in",
    "https://arena4viewer.lv", 
]

HEADERS = {
    "User-Agent": "Arena4Viewer/6.8.2",
    "X-Requested-With": "com.bone.android.a4v.oficial",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Cookie": "beget=begetok",
    "Content-Type": "application/x-www-form-urlencoded",
}

# Patrón principal: avXX#acestream://HEX_40_CHARS
CANAL_PATTERN = re.compile(r'av\s*(\d{1,3})\s*#acestream://([a-fA-F0-9]{40})', re.I)

# ==================== LOGGING ====================
def configurar_logging():
    logging.root.handlers = []
    log_format = '%(asctime)s [%(levelname)-8s] %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    logging.basicConfig(
        level=logging.DEBUG,
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    for handler in logging.root.handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setLevel(logging.INFO)
    logging.info("🚀 Logger configurado - Archivo: %s", LOG_FILE)

def log_debug(msg, *args): logging.debug(msg, *args)
def log_info(msg, *args): logging.info(msg, *args)
def log_warning(msg, *args): logging.warning(msg, *args)
def log_error(msg, *args): logging.error(msg, *args)
def log_success(msg, *args): logging.info(f"✅ {msg}", *args)

# ==================== FUNCIONES AUXILIARES ====================
def normaliza_headers(referer=None):
    headers = HEADERS.copy()
    if referer:
        headers["Referer"] = referer
    return headers

def fetch_agenda_post(server):
    url = f"{server.rstrip('/')}/{AGENDA_FILE}"
    data = {"key": API_KEY, "expire": EXPIRE_DATE}
    log_debug("🔗 POST a %s con key=%s... expire=%s", url, API_KEY[:8], EXPIRE_DATE)
    try:
        resp = requests.post(url, headers=normaliza_headers(server + "/"), data=data, timeout=20, allow_redirects=True)
        log_debug("📡 Status: %d | Tamaño: %d bytes | URL final: %s", resp.status_code, len(resp.text), resp.url)
        if resp.status_code == 200 and len(resp.text) > 2000:
            if "<title>Arena4Viewer - Sports everywhere" in resp.text and "acestream://" not in resp.text.lower():
                log_warning("⚠️  Respuesta parece ser homepage, no agenda de canales")
                return None, None
            log_debug("✅ Respuesta válida: %d bytes desde %s", len(resp.text), server)
            return resp.text, resp.url
        else:
            log_warning("❌ Respuesta inválida: status=%d, tamaño=%d bytes", resp.status_code, len(resp.text))
            return None, None
    except requests.exceptions.Timeout:
        log_error("⏱️  Timeout conectando a %s", url)
        return None, None
    except requests.exceptions.ConnectionError:
        log_error("🔌 Error de conexión a %s", url)
        return None, None
    except requests.exceptions.SSLError as e:
        log_warning("🔒 SSL Error en %s: %s", url, e)
        try:
            resp = requests.post(url, headers=normaliza_headers(server + "/"), data=data, timeout=20, verify=False)
            if resp.status_code == 200 and len(resp.text) > 2000:
                return resp.text, resp.url
        except Exception as e2:
            log_error("❌ Reintento sin SSL también falló: %s", e2)
        return None, None
    except Exception as e:
        log_error("💥 Error inesperado en %s: %s: %s", url, type(e).__name__, e)
        return None, None

def extraer_streams_del_html(html):
    """
    Extrae el contenido de streams con patrones flexibles.
    Retorna el contenido crudo o None si no se encuentra.
    """
    # Patrones para encontrar el div streams (más flexibles)
    patterns = [
        r'<div[^>]*class=["\']?streams["\']?[^>]*style=["\']?display:\s*none;?["\']?[^>]*>(.*?)</div>',
        r'<div[^>]*style=["\']?display:\s*none;?["\']?[^>]*class=["\']?streams["\']?[^>]*>(.*?)</div>',
        r'<div[^>]*class=["\']?streams["\']?[^>]*>([^<]+)</div>',
        r'class=["\']?streams["\']?[^>]*>([^<]+)',
        r'streams[^>]*>([^<]+)',  # Fallback muy flexible
    ]
    
    for i, pattern in enumerate(patterns, 1):
        match = re.search(pattern, html, re.DOTALL | re.I)
        if match:
            content = match.group(1).strip() if match.lastindex and match.lastindex >= 1 else match.group(0)
            log_debug("📦 Sección 'streams' encontrada (patrón %d): %d caracteres", i, len(content))
            return content
    
    log_warning("⚠️  No se encontró sección 'streams' con patrones de div")
    return None

def extraer_canales_directos_del_html(html):
    """
    Fallback: Busca canales directamente en todo el HTML usando el patrón avXX#acestream://ID
    Esto funciona incluso si no hay un div "streams" wrapper.
    """
    canales = {}
    
    # Buscar TODAS las ocurrencias del patrón en todo el HTML
    matches = CANAL_PATTERN.findall(html)
    
    if not matches:
        log_debug("🔍 No se encontraron canales con patrón directo en el HTML")
        return canales
    
    for canal_num_str, ace_id in matches:
        canal_num = int(canal_num_str)
        ace_id_normalizado = ace_id.lower()
        canales[canal_num] = ace_id_normalizado
        log_debug("📺 Canal %03d -> ID: %s (extraído directamente)", canal_num, ace_id_normalizado)
    
    canales_ordenados = dict(sorted(canales.items()))
    log_info("📊 Extraídos %d canales directamente del HTML: %s", len(canales_ordenados), list(canales_ordenados.keys()))
    
    return canales_ordenados

def extraer_canales_dinamicos(streams_content):
    """Extrae canales desde el contenido de streams (formato tradicional)"""
    canales = {}
    if not streams_content:
        return canales
    
    matches = CANAL_PATTERN.findall(streams_content)
    for canal_num_str, ace_id in matches:
        canal_num = int(canal_num_str)
        canales[canal_num] = ace_id.lower()
        log_debug("📺 Canal %03d -> ID: %s", canal_num, ace_id)
    
    return dict(sorted(canales.items()))

def sanear_nombre_servidor(server):
    parsed = urlparse(server)
    hostname = parsed.netloc or parsed.path
    return hostname.replace(".", "_").replace(":", "_").replace("/", "_").replace("-", "_")

def guardar_html_debug(html, server_url, timestamp=None):
    """Guarda HTML para debug SOLO si DEBUG_SAVE_HTML=True"""
    if not DEBUG_SAVE_HTML:
        return None
    if not os.path.exists(CARPETA_HTML):
        os.makedirs(CARPETA_HTML, exist_ok=True)
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    servidor_saneado = sanear_nombre_servidor(server_url)
    filename = os.path.join(CARPETA_HTML, f"agenda_{servidor_saneado}_{timestamp}.html")
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(html)
        log_debug("💾 HTML guardado en: %s", filename)
        return filename
    except Exception as e:
        log_warning("⚠️  No se pudo guardar HTML: %s", e)
        return None

def generar_entrada_m3u(canal_num, ace_id, channel_name=None):
    if channel_name is None:
        channel_name = f"ArenaVision {canal_num:02d}"
    stream_url = f"http://127.0.0.1:6878/ace/getstream?id={ace_id}"
    return f'#EXTINF:-1 tvg-id="{channel_name}" tvg-name="{channel_name}" tvg-logo="" group-title="Arena4Viewer",{channel_name}\n{stream_url}'

# ==================== FUNCIÓN PRINCIPAL ====================
def generar_m3u():
    configurar_logging()
    
    log_info("="*70)
    log_info("🚀 INICIO DE EXTRACCIÓN ARENA4VIEWER")
    log_info("📅 Fecha: %s", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    log_info("🔑 API Key: %s", API_KEY)
    log_info("📆 Expire: %s", EXPIRE_DATE)
    log_info("🐛 DEBUG_SAVE_HTML: %s", DEBUG_SAVE_HTML)
    log_info("="*70)
    
    channels_dict = {}
    servidor_exitoso = None
    url_exitosa = None
    
    log_info("🔄 Descargando agenda desde %d servidores...", len(ARENA_URLS))
    
    for idx, server in enumerate(ARENA_URLS, 1):
        log_info("[%d/%d] Probando: %s", idx, len(ARENA_URLS), server)
        html, url_respuesta = fetch_agenda_post(server)
        
        if not html:
            log_warning("❌ Sin respuesta válida de %s", server)
            continue
        
        log_success("✅ Agenda descargada desde %s (%d bytes)", server, len(html))
        guardar_html_debug(html, server)
        servidor_exitoso = server
        url_exitosa = url_respuesta
        
        # ESTRATEGIA 1: Intentar extraer desde div streams (formato tradicional)
        log_debug("🔍 Intentando extracción desde sección 'streams'...")
        streams_content = extraer_streams_del_html(html)
        
        if streams_content:
            log_info("📦 Sección streams encontrada, extrayendo canales...")
            channels_dict = extraer_canales_dinamicos(streams_content)
        
        # ESTRATEGIA 2 (FALLBACK): Buscar canales directamente en todo el HTML
        if not channels_dict:
            log_info("🔄 Fallback: Buscando canales directamente en el HTML completo...")
            channels_dict = extraer_canales_directos_del_html(html)
        
        if channels_dict:
            log_success("✅ Extraídos %d canales desde %s", len(channels_dict), server)
            break
        else:
            log_warning("⚠️  No se encontraron canales en %s", server)
    
    if not channels_dict:
        log_error("💥 FATAL: No se pudo extraer ningún canal")
        log_info("💡 Causas posibles:")
        log_info("   • No hay eventos EN VIVO ahora")
        log_info("   • El formato del HTML ha cambiado")
        log_info("   • Se requiere JavaScript para cargar los streams")
        if DEBUG_SAVE_HTML:
            log_info("🔎 Revisa: grep -i 'av.*#acestream' %s/*.html", CARPETA_HTML)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n# ERROR: Sin canales - %s\n" % datetime.now())
        return False
    
    log_info("📝 Generando M3U con %d canales...", len(channels_dict))
    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            f.write(f"# Generated by Arena4Viewer Extractor - {datetime.now()}\n")
            f.write(f"# Canales: {len(channels_dict)} | Servidor: {servidor_exitoso}\n\n")
            for canal_num in sorted(channels_dict.keys()):
                ace_id = channels_dict[canal_num]
                entry = generar_entrada_m3u(canal_num, ace_id)
                f.write(entry + "\n\n")
        log_success("📄 Archivo '%s' generado con %d entradas", OUTPUT_FILE, len(channels_dict))
    except Exception as e:
        log_error("💥 Error escribiendo M3U: %s", e)
        return False
    
    log_info("="*70)
    log_info("🎉 EXTRACCIÓN COMPLETADA")
    log_info("📊 Resumen: %d canales | Rango: %03d-%03d | Servidor: %s", 
            len(channels_dict), min(channels_dict.keys()), max(channels_dict.keys()), servidor_exitoso)
    log_info("📋 Muestra (primeros 5):")
    for i, (cn, aid) in enumerate(sorted(channels_dict.items())[:5], 1):
        log_info("   %d. AV%03d: ...%s", i, cn, aid[-10:])
    
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n=== FIN ===\nCanales: {len(channels_dict)}\nServidor: {servidor_exitoso}\n")
    
    log_info("✨ ¡Listo! Usa '%s' en VLC/Kodi/Ace Stream", OUTPUT_FILE)
    return True

if __name__ == "__main__":
    print("="*70)
    print("📺 Arena4Viewer Extractor - Versión Corregida + Fallback")
    print(f"🕐 Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🐛 DEBUG_SAVE_HTML: {DEBUG_SAVE_HTML}")
    print("="*70)
    try:
        exito = generar_m3u()
        print(f"\n{'✅ ¡ÉXITO!' if exito else '❌ Sin resultados'} - Revisa '{LOG_FILE}'")
    except KeyboardInterrupt:
        log_warning("⚠️  Interrumpido por usuario")
    except Exception as e:
        log_error("💥 Error crítico: %s: %s", type(e).__name__, e)
        import traceback
        log_error(traceback.format_exc())
    finally:
        print(f"📄 Log: {os.path.abspath(LOG_FILE)}")
        if DEBUG_SAVE_HTML:
            print(f"📁 HTMLs: {os.path.abspath(CARPETA_HTML)}/")