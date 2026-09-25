import requests
import re
import json
import base64
import time
import os

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Referer': 'https://tvnow247.today/',
    'Origin': 'https://tvnow247.today'
}

BASE_SITE = 'https://tvnow247.today'
PROXY_DOMAIN = 'chunk.tvnow247.today'
OUTPUT_M3U = 'lista_tvnow247.m3u'

def obtener_canales():
    print("Obteniendo página principal de tvnow247.today...")
    res = requests.get(BASE_SITE, headers=HEADERS, timeout=10)
    res.raise_for_status()
    
    # Buscar el bundle de JavaScript que contiene la lista de canales
    match_js = re.search(r'assets/channels-[A-Za-z0-9_-]+\.js', res.text)
    if not match_js:
        raise ValueError("No se pudo localizar el bundle de canales en el HTML principal.")
    
    js_url = f"{BASE_SITE}/{match_js.group(0)}"
    print(f"Descargando bundle de canales desde: {js_url}")
    
    res_js = requests.get(js_url, headers=HEADERS, timeout=15)
    res_js.raise_for_status()
    
    # Extraer objetos de canales con regex
    raw_channels = re.findall(r'\{[^{}]*?channel_name:[^{}]*?channel_id:[^{}]*?\}', res_js.text)
    print(f"Se encontraron {len(raw_channels)} canales en el código fuente.")
    
    canales = []
    for item in raw_channels:
        name_m = re.search(r'channel_name:\s*\"([^\"]+)\"', item)
        id_m = re.search(r'channel_id:\s*\"([^\"]+)\"', item)
        logo_m = re.search(r'logoUrl:\s*\"([^\"]+)\"', item)
        group_m = re.search(r'country:\s*\"([^\"]+)\"', item)
        
        if name_m and id_m:
            name = name_m.group(1).strip()
            cid = id_m.group(1).strip()
            logo = logo_m.group(1).strip() if logo_m else ''
            group = group_m.group(1).strip() if group_m else 'General'
            canales.append({
                'name': name,
                'id': cid,
                'logo': logo,
                'group': group
            })
            
    return canales

def generar_url_proxy(channel_id, domain=PROXY_DOMAIN):
    ts = int(time.time() * 1000)
    payload = {'channelId': str(channel_id), 'ts': ts}
    json_str = json.dumps(payload, separators=(',', ':'))
    token = base64.b64encode(json_str.encode('utf-8')).decode('utf-8').rstrip('=')
    return f"https://{domain}/api/proxy/playlist?token={token}"

def generar_lista_m3u(canales, filename=OUTPUT_M3U):
    print(f"Generando archivo M3U: {filename}...")
    with open(filename, 'w', encoding='utf-8') as f:
        f.write('#EXTM3U\n')
        for c in canales:
            url = generar_url_proxy(c['id'])
            f.write(f'#EXTINF:-1 tvg-id="{c["id"]}" tvg-name="{c["name"]}" tvg-logo="{c["logo"]}" group-title="{c["group"]}",{c["name"]}\n')
            f.write(f'{url}\n')
            
    print(f"¡Éxito! Lista M3U generada correctamente con {len(canales)} canales.")

if __name__ == '__main__':
    try:
        canales = obtener_canales()
        if canales:
            generar_lista_m3u(canales)
        else:
            print("No se encontraron canales válidos.")
    except Exception as e:
        print(f"Error durante el proceso: {e}")
