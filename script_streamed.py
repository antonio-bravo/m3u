import requests
import json
import time
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
}

BASE_DOMAINS = [
    'streamed.pk',
    'streamed.su',
    'streamed.site'
]

OUTPUT_M3U = 'lista_streamed.m3u'

def obtener_partidos_y_dominio():
    for d in BASE_DOMAINS:
        print(f"Probando dominio fuente Streamed: {d}...")
        url = f"https://{d}/api/matches/all"
        try:
            r = requests.get(url, headers=HEADERS, timeout=8)
            if r.status_code == 200 and r.text.startswith('['):
                matches = r.json()
                print(f"Conexión exitosa a {d}. Se encontraron {len(matches)} eventos/partidos.")
                return matches, d
        except Exception as e:
            print(f"No se pudo obtener la lista desde {d}: {e}")

    raise ConnectionError("No se pudo conectar a ninguno de los dominios base de Streamed.")

def obtener_streams_de_fuente(args):
    domain, m, s = args
    title = m.get('title', 'Evento Deportivo')
    category = m.get('category', 'Deportes').title()
    poster = m.get('poster', '')
    logo_url = f"https://{domain}{poster}" if poster.startswith('/') else poster
    
    src_name = s.get('source')
    src_id = s.get('id')
    if not src_name or not src_id:
        return []

    results = []
    stream_api = f"https://{domain}/api/stream/{src_name}/{src_id}"
    try:
        r_st = requests.get(stream_api, headers=HEADERS, timeout=4)
        if r_st.status_code == 200:
            streams = r_st.json()
            for st in streams:
                embed_url = st.get('embedUrl')
                if not embed_url:
                    continue
                lang = st.get('language', 'EN').upper()
                stream_no = st.get('streamNo', 1)
                quality = 'HD' if st.get('hd') else 'SD'
                
                channel_title = f"{title} ({category}) - Opción {stream_no} [{lang}] [{quality}]"
                results.append({
                    'name': channel_title,
                    'id': f"{src_name}-{src_id}-{stream_no}",
                    'logo': logo_url,
                    'group': category,
                    'url': embed_url
                })
    except Exception:
        pass
    return results

def generar_lista_m3u(matches, domain, filename=OUTPUT_M3U):
    print(f"Procesando enlaces de streaming en paralelo para {len(matches)} eventos...")
    
    tasks = []
    for m in matches:
        sources = m.get('sources', [])
        for s in sources:
            tasks.append((domain, m, s))
            
    entradas_m3u = []
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(obtener_streams_de_fuente, t) for t in tasks]
        for future in as_completed(futures):
            res = future.result()
            if res:
                entradas_m3u.extend(res)
                
    print(f"Escribiendo {len(entradas_m3u)} transmisiones en {filename}...")
    with open(filename, 'w', encoding='utf-8') as f:
        f.write('#EXTM3U\n')
        for item in entradas_m3u:
            f.write(f'#EXTINF:-1 tvg-id="{item["id"]}" tvg-name="{item["name"]}" tvg-logo="{item["logo"]}" group-title="{item["group"]}",{item["name"]}\n')
            f.write(f'{item["url"]}\n')

    print(f"¡Éxito! Lista M3U para Streamed generada correctamente con {len(entradas_m3u)} transmisiones.")

if __name__ == '__main__':
    try:
        matches, domain = obtener_partidos_y_dominio()
        if matches:
            generar_lista_m3u(matches, domain)
        else:
            print("No se encontraron eventos válidos.")
    except Exception as e:
        print(f"Error durante el proceso: {e}")
