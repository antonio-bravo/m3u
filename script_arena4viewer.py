import requests
from bs4 import BeautifulSoup
import re
import os

# Configuración
OUTPUT_FILE = "area4viewer.m3u"
ARENA_URLS = [
    "http://www.arena4viewer.in/misguia2.php",
    "http://www.arena4viewer.pl/misguia2.php",
    "https://www.arena4viewer.co.in/misguia2.php",
    "https://www.arena4viewer.lv/misguia2.php"
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def extract_acestream_ids(text):
    """Busca hashes hex de 40 caracteres típicos de AceStream."""
    return re.findall(r'[a-fA-F0-9]{40}', text)

def get_channels():
    found_ids = set()
    channels = []

    for url in ARENA_URLS:
        try:
            print(f"Consultando {url}...")
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200: continue
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # 1. Buscar en el div oculto 'streams'
            streams_div = soup.find('div', class_='streams', style=lambda x: x and 'display:none' in x.replace(" ", ""))
            if streams_div:
                ids = extract_acestream_ids(streams_div.get_text())
                for aid in ids:
                    found_ids.add(aid)

            # 2. Buscar en todos los enlaces <a> que tengan acestream://
            for a in soup.find_all('a', href=True):
                href = a['href']
                if 'acestream://' in href:
                    ids = extract_acestream_ids(href)
                    for aid in ids:
                        found_ids.add(aid)
        except Exception as e:
            print(f"Error en {url}: {e}")

    # Generar lista final
    for i, aid in enumerate(sorted(found_ids), 1):
        channels.append({
            "name": f"Arena4Viewer CH {i}",
            "id": aid,
            "url": f"http://127.0.0.1:6878/ace/getstream?id={aid}"
        })
    
    return channels

def save_m3u(channels):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            # He añadido un logo genérico de deportes y el formato que pediste
            f.write(f'#EXTINF:-1 tvg-id="{ch["name"]}" tvg-name="{ch["name"]}" tvg-logo="https://raw.githubusercontent.com/davidmuma/picons_dobleM/master/icon/M%2B%20Liga%20de%20Campeones.png",{ch["name"]}\n')
            f.write(f"{ch['url']}\n")
    print(f"Archivo {OUTPUT_FILE} generado con {len(channels)} canales.")

if __name__ == "__main__":
    lista_canales = get_channels()
    if lista_canales:
        save_m3u(lista_canales)
    else:
        print("No se encontraron canales.")
