import requests
import re
import json
import base64
import time
import os
from urllib.parse import urlparse

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
}

BASE_SITES = [
    'https://tvnow247.today',
    'https://tvnow247.top'
]

OUTPUT_M3U = 'lista_tvnow247.m3u'

def probar_proxy_domain(domain, test_channel_id='51'):
    """Valida mediante sonda HTTP si un dominio responde con una lista M3U válida en /api/proxy/playlist."""
    if not domain:
        return False, None
    ts = int(time.time() * 1000)
    payload = {'channelId': str(test_channel_id), 'ts': ts}
    json_str = json.dumps(payload, separators=(',', ':'))
    token = base64.b64encode(json_str.encode('utf-8')).decode('utf-8').rstrip('=')
    test_url = f"https://{domain}/api/proxy/playlist?token={token}"
    headers = {**HEADERS, 'Referer': f"https://{domain}/"}
    try:
        r = requests.get(test_url, headers=headers, timeout=3)
        if r.status_code == 200 and '#EXTM3U' in r.text:
            return True, domain
    except Exception:
        pass
    return False, None

def cargar_modulos_clave_js(base_site, html_text):
    """Descarga rápidamente los módulos JS clave de la aplicación web."""
    js_texts = []
    matches = re.findall(r'assets/(?:channels|main|streamService|PlayerPage|TVChannelsPage)-[a-zA-Z0-9_-]+\.js', html_text)
    
    main_match = re.search(r'assets/main-[a-zA-Z0-9_-]+\.js', html_text)
    if main_match:
        try:
            r_main = requests.get(f"{base_site}/{main_match.group(0)}", headers=HEADERS, timeout=4)
            if r_main.status_code == 200:
                js_texts.append(r_main.text)
                sub_assets = re.findall(r'assets/(?:channels|streamService|PlayerPage|TVChannelsPage)-[a-zA-Z0-9_-]+\.js', r_main.text)
                matches.extend(sub_assets)
        except Exception:
            pass

    for path in set(matches):
        url = f"{base_site}/{path.lstrip('/')}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=4)
            if r.status_code == 200:
                js_texts.append(r.text)
        except Exception:
            pass
            
    return js_texts

def descubrir_proxy_domain_100_dinamico(res_html, js_texts, active_host, base_site):
    """Descubre el dominio proxy de forma 100% dinámica extrayendo URLs y subdominios del código fuente."""
    combined_code = res_html + '\n' + '\n'.join(js_texts)
    candidatos = []

    # 1. Consultar APIs de resolución dinámicas encontradas en el código fuente de la web
    api_urls = re.findall(r'https?://[a-zA-Z0-9_.-]+/api/resolve-[a-zA-Z0-9_-]+', combined_code)
    for api_ep in set(api_urls):
        try:
            r_api = requests.get(f"{api_ep}/51", headers=HEADERS, timeout=3)
            if r_api.status_code == 200:
                data = r_api.json()
                playlist_url = data.get('proxyPlaylistUrl') or data.get('m3u8') or data.get('proxyPlayerUrl')
                if playlist_url:
                    d = urlparse(playlist_url).netloc
                    valid, _ = probar_proxy_domain(d)
                    if valid:
                        print(f"Dominio proxy obtenido dinámicamente desde API ({api_ep}): {d}")
                        return d
        except Exception:
            pass

    # 2. URLs absolutas que contienen /api/proxy en el código fuente
    proxy_urls = re.findall(r'https?://([a-zA-Z0-9_.-]+\.[a-zA-Z]{2,})[^\'\"\s]*/api/proxy', combined_code)
    for p in proxy_urls:
        p_clean = p.replace('www.', '').strip('/')
        if p_clean not in candidatos:
            candidatos.append(p_clean)

    # 3. Subdominios del sitio web presentes en el código JS/HTML (ej: *.tvnow247.today, *.tvnow247.top)
    base_domain_parts = active_host.split('.')
    if len(base_domain_parts) >= 2:
        root_domain = '.'.join(base_domain_parts[-2:])
        subdoms = re.findall(r'\b([a-zA-Z0-9_-]+\.' + re.escape(root_domain) + r')\b', combined_code)
        for sd in subdoms:
            if sd not in candidatos:
                candidatos.append(sd)

    # 4. Todos los hostnames de URLs HTTP/HTTPS presentes en el código de la web
    all_urls = re.findall(r'https?://([a-zA-Z0-9_.-]+\.[a-zA-Z]{2,})', combined_code)
    for u in all_urls:
        u_clean = u.replace('www.', '').strip('/')
        if u_clean not in candidatos and not u_clean.endswith(('.png', '.jpg', '.jpeg', '.svg', '.js', '.css', '.org', '.google', '.doubleclick.net', '.vimeo.com', '.youtube.com', '.github.com', '.twitter.com')):
            candidatos.append(u_clean)

    # Añadir el hostname activo como opción
    if active_host not in candidatos:
        candidatos.append(active_host)

    # 5. Sondear cada candidato extraído dinámicamente de la web mediante sonda HTTP real
    for d in candidatos:
        valid, _ = probar_proxy_domain(d)
        if valid:
            print(f"Dominio proxy extraído dinámicamente del sitio web y validado: {d}")
            return d

    print(f"Usando hostname activo como fallback: {active_host}")
    return active_host

def obtener_canales_y_proxy():
    res = None
    base_site_usado = None
    
    for site in BASE_SITES:
        print(f"Probando sitio fuente: {site}...")
        try:
            r = requests.get(site, headers=HEADERS, timeout=6)
            if r.status_code == 200:
                res = r
                base_site_usado = site
                print(f"Conexión exitosa a {site}")
                break
        except Exception as e:
            print(f"No se pudo conectar a {site}: {e}")

    if not res:
        raise ConnectionError("No se pudo conectar a ninguno de los dominios base especificados.")

    active_host = urlparse(res.url).netloc.replace('www.', '')
    
    print("Descargando módulos JavaScript clave de la aplicación web...")
    js_texts = cargar_modulos_clave_js(base_site_usado, res.text)
    
    # Buscar el bundle que contiene el catálogo de canales
    channels_js_text = ''
    for text in js_texts:
        if 'channel_name' in text and 'channel_id' in text:
            channels_js_text = text
            break
            
    if not channels_js_text:
        channels_js_text = res.text

    # Descubrir el dominio proxy de forma 100% dinámica
    proxy_domain = descubrir_proxy_domain_100_dinamico(res.text, js_texts, active_host, base_site_usado)
    
    # Extraer objetos de canales con regex
    raw_channels = re.findall(r'\{[^{}]*?channel_name:[^{}]*?channel_id:[^{}]*?\}', channels_js_text)
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
            
    return canales, proxy_domain

def generar_url_proxy(channel_id, domain):
    ts = int(time.time() * 1000)
    payload = {'channelId': str(channel_id), 'ts': ts}
    json_str = json.dumps(payload, separators=(',', ':'))
    token = base64.b64encode(json_str.encode('utf-8')).decode('utf-8').rstrip('=')
    return f"https://{domain}/api/proxy/playlist?token={token}"

def generar_lista_m3u(canales, proxy_domain, filename=OUTPUT_M3U):
    print(f"Generando archivo M3U: {filename}...")
    with open(filename, 'w', encoding='utf-8') as f:
        f.write('#EXTM3U\n')
        for c in canales:
            url = generar_url_proxy(c['id'], proxy_domain)
            f.write(f'#EXTINF:-1 tvg-id="{c["id"]}" tvg-name="{c["name"]}" tvg-logo="{c["logo"]}" group-title="{c["group"]}",{c["name"]}\n')
            f.write(f'{url}\n')
            
    print(f"¡Éxito! Lista M3U generada correctamente con {len(canales)} canales usando el dominio '{proxy_domain}'.")

if __name__ == '__main__':
    try:
        canales, proxy_domain = obtener_canales_y_proxy()
        if canales:
            generar_lista_m3u(canales, proxy_domain)
        else:
            print("No se encontraron canales válidos.")
    except Exception as e:
        print(f"Error durante el proceso: {e}")
