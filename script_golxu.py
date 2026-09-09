import requests
import json
import re
import base64
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import unquote, quote

BASE_URL = 'https://golxu.st/'
API_URL = 'https://golxu.st/craftcurlid.php'
FALLBACK_URL = 'https://golxu.st/agendapiid.php'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Referer': BASE_URL,
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
}

LANGUAGE_MAP = {
    'SPANISH': 'ESPAÑOL',
    'SPANISH ALT': 'ESPAÑOL ALT',
    'ENGLISH': 'INGLÉS',
    'PORTUGUESE': 'PORTUGUÉS',
    'FRENCH': 'FRANCÉS',
    'ITALIAN': 'ITALIANO',
    'GERMAN': 'ALEMÁN',
}

def to_base64(s: str) -> str:
    """Encoder de Base64 compatible con JS btoa(unescape(encodeURIComponent(str)))"""
    return base64.b64encode(s.encode('utf-8')).decode('utf-8')

def fetch_json_data():
    """Obtiene los datos directamente desde craftcurlid.php"""
    try:
        url = f"{API_URL}?cb={int(datetime.now().timestamp() * 1000)}"
        response = requests.get(url, headers=HEADERS, timeout=12)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and data:
                return data
    except Exception as err:
        print(f"Advertencia: No se pudo obtener JSON desde API directa ({err}). Intentando fallback...")
    return None

def fetch_fallback_data():
    """Deobfuscador de respaldo para agendapiid.php"""
    try:
        response = requests.get(FALLBACK_URL, headers=HEADERS, timeout=15)
        if response.status_code != 200:
            return None
        text = response.text

        array_match = re.search(r'var\s+[a-zA-Z0-9_$]+\s*=\s*\[(.*?)\];', text, re.DOTALL)
        decoder_match = re.search(r'-\s*(\d+)\s*\)\s*;\s*\}\s*\)\s*;\s*document\.write', text)

        if not array_match or not decoder_match:
            return None

        offset = int(decoder_match.group(1))
        raw_elements = re.findall(r'\"([^\"]+)\"', array_match.group(2))

        char_codes = []
        for item in raw_elements:
            try:
                decoded_b64 = base64.b64decode(item).decode('utf-8', errors='ignore')
                digits_only = re.sub(r'\D', '', decoded_b64)
                if digits_only:
                    val = int(digits_only) - offset
                    char_codes.append(chr(val))
            except Exception:
                pass

        html_str = ''.join(char_codes)
        match_fetch = re.search(r'fetch\(`(https?://[^`]+)`\)', html_str)
        if match_fetch:
            fetch_url = match_fetch.group(1).replace('${Date.now()}', str(int(datetime.now().timestamp() * 1000)))
            r = requests.get(fetch_url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                return r.json()
    except Exception as err:
        print(f"Error en fallback: {err}")
    return None

def process_events(raw_data):
    """Procesa y agrupa los eventos recibidos"""
    if not raw_data:
        return []

    grouped = {}
    today_str = datetime.now().strftime('%Y-%m-%d')

    for item in raw_data:
        raw_link = item.get('link', '') or ''
        if not raw_link:
            continue

        hora = item.get('hora', '00:00').strip()
        categoria = (item.get('categoria', 'EVENTO') or 'EVENTO').strip().upper()
        info = (item.get('info', '') or '').strip()
        raw_titulo = (item.get('titulo', '') or '').strip()

        # Extraer hash del link
        if 'v=' in raw_link:
            hash_stream = raw_link.split('v=')[1].strip()
        elif '/' in raw_link:
            hash_stream = raw_link.rstrip('/').split('/')[-1].strip()
        else:
            hash_stream = raw_link.strip()

        if not hash_stream:
            continue

        # Limpieza del título e idioma
        titulo_limpio = re.sub(r'\[[^\]]+\]', '', raw_titulo).strip()
        match_lang = re.search(r'\[([^\]]+)\]', raw_titulo)

        if match_lang:
            lang_code = match_lang.group(1).strip().upper()
            nombre_opcion = LANGUAGE_MAP.get(lang_code, lang_code)
        else:
            nombre_opcion = 'OPCIÓN 1'

        league_label = info if info else categoria
        group_key = f"{titulo_limpio}_{hora}_{league_label}".lower()

        target_url = f"https://www.vdocity.com/{hash_stream}"
        player_url = f"https://golxu.st/eventos.html?r={to_base64(target_url)}"

        if group_key not in grouped:
            grouped[group_key] = {
                'datetime': f"{today_str} {hora}".strip(),
                'league': league_label,
                'teams': titulo_limpio if titulo_limpio else league_label,
                'channels': []
            }

        # Evitar duplicados de la misma URL
        if not any(c['url'] == player_url for c in grouped[group_key]['channels']):
            ch_idx = len(grouped[group_key]['channels']) + 1
            op_label = nombre_opcion if nombre_opcion != 'OPCIÓN 1' else f"OPCIÓN {ch_idx}"
            grouped[group_key]['channels'].append({
                'channel_name': op_label,
                'channel_id': f"{len(grouped) + 1}-{ch_idx}",
                'url': player_url
            })

    return list(grouped.values())

def generate_xml(events, output_path='lista_golxu.xml'):
    """Genera la lista XML con formato idéntico a lista_reproductor_web.xml"""
    root = ET.Element('events')
    for event in events:
        event_elem = ET.SubElement(root, 'event')
        ET.SubElement(event_elem, 'datetime').text = event['datetime']
        ET.SubElement(event_elem, 'league').text = event['league']
        ET.SubElement(event_elem, 'teams').text = event['teams']

        channels_elem = ET.SubElement(event_elem, 'channels')
        for channel in event['channels']:
            channel_elem = ET.SubElement(channels_elem, 'channel')
            ET.SubElement(channel_elem, 'channel_name').text = channel['channel_name']
            ET.SubElement(channel_elem, 'channel_id').text = str(channel['channel_id'])
            ET.SubElement(channel_elem, 'url').text = channel['url']

    def indent(elem, level=0):
        i = "\n" + "  " * level
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = i + "  "
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
            for subelem in elem:
                indent(subelem, level + 1)
            if not subelem.tail or not elem.tail.strip():
                subelem.tail = i
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = i

    indent(root)
    tree = ET.ElementTree(root)
    tree.write(output_path, encoding='utf-8', xml_declaration=True)

def generate_m3u(events, output_path='lista_golxu.m3u'):
    """Genera el archivo de lista de reproducción M3U"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('#EXTM3U\n')
        for event in events:
            for channel in event['channels']:
                f.write(
                    f'#EXTINF:-1,{event["datetime"]} - {event["league"]} - {event["teams"]} - {channel["channel_name"]}\n'
                    f'{channel["url"]}\n'
                )

def main():
    print("Obteniendo agenda deportiva desde Golxu...")
    raw_data = fetch_json_data()
    if not raw_data:
        raw_data = fetch_fallback_data()

    if not raw_data:
        print("Error crítico: No se pudieron obtener eventos de Golxu.")
        sys.exit(1)

    events = process_events(raw_data)
    if not events:
        print("Error: No se procesaron eventos válidos.")
        sys.exit(1)

    print(f"Se procesaron {len(events)} eventos exitosamente.")

    generate_xml(events, 'lista_golxu.xml')
    generate_m3u(events, 'lista_golxu.m3u')
    print("Archivos lista_golxu.xml y lista_golxu.m3u generados correctamente.")

if __name__ == '__main__':
    main()
