#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script para generar la lista M3U de AK47Sports.
Obtiene los servidores y canales desde la API y Firebase Remote Config de AK47Sports,
desencripta la informacion AES-128-CBC y genera 'lista_ak47sports.m3u'.
Usa ThreadPoolExecutor para procesamiento concurrente y de alta velocidad.
"""

import argparse
import base64
import json
import logging
import os
import re
import sys
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

# Firebase Remote Config credentials extracted from AK47Sports APK
FIREBASE_KEY = os.getenv("AK47SPORTS_FIREBASE_CONFIG_URL_KEY", "AIzaSyCOhPo6X5o8517oC_tFtH_L8ka3ElacTu0")
FIREBASE_CONFIG_URL = f"https://firebaseremoteconfig.googleapis.com/v1/projects/ak47-sports/namespaces/firebase:fetch?key={FIREBASE_KEY}"
FIREBASE_PAYLOAD = {
    "appId": "1:987682933046:android:b0d2f52255416b34cb0b97",
    "appInstanceId": "12345678901234567890123456789012"
}
FALLBACK_SERVER = "https://hellobabu.xyz/"
KEY = b"l9K5bT5xC1wP7pK1"
IV = b"k5K4nN7oU8hL6l19"

LOG_FILE = "lista_ak47sports.log"
M3U_FILE = "lista_ak47sports.m3u"
M3U_ES_FILE = "lista_ak47sports_ES.m3u"

# Patrones de equipos espanoles para filtrado de eventos deportivos
SPANISH_TEAMS_PATTERNS = [
    # Futbol - LaLiga EA Sports & Hypermotion & categorias destacadas
    r"\breal\s+madrid\b",
    r"\br\.?\s*madrid\b",
    r"\batl[eé]tico(?:\s+de)?\s+madrid\b",
    r"\batleti\b",
    r"\bbar[cç]a\b",
    r"\bfc\s+barcelona\b",
    r"\bbarcelona\b",
    r"\bsevil+a(?:\s+fc)?\b",
    r"\b(?:real\s+)?betis(?:\s+balompi[eé])?\b",
    r"\breal\s+sociedad\b",
    r"\bvillar+eal(?:\s+cf)?\b",
    r"\bvalencia(?:\s+cf|\s+basket)?\b",
    r"\bathletic(?:\s+club|\s+bilbao)?\b",
    r"\bgirona(?:\s+fc|\s+basket)?\b",
    r"\bcelta(?:\s+de\s+vigo|\s+vigo)?\b",
    r"\b(?:ca\s+)?osasuna\b",
    r"\brayo(?:\s+val+ecano)?\b",
    r"\b(?:rcd\s+)?mallorca\b",
    r"\b(?:deportivo\s+)?alav[eé]s\b",
    r"\bgetafe(?:\s+cf)?\b",
    r"\b(?:rcd\s+)?espanyol\b",
    r"\b(?:ud\s+)?las\s+palmas\b",
    r"\b(?:cd\s+)?legan[eé]s\b",
    r"\b(?:real\s+)?val+adolid\b",
    r"\blevante(?:\s+ud)?\b",
    r"\b(?:real\s+)?zaragoza\b",
    r"\bracing(?:\s+de)?\s+santander\b",
    r"\b(?:racing(?:\s+de)?\s+)?ferrol\b",
    r"\b(?:real\s+)?sporting(?:\s+de)?\s+gij[oó]n\b",
    r"\breal\s+sporting\b",
    r"\b(?:real\s+)?oviedo\b",
    r"\belche(?:\s+cf)?\b",
    r"\b(?:sd\s+)?eibar\b",
    r"\b(?:ud\s+)?almer[ií]a\b",
    r"\bgranada(?:\s+cf|\s+baloncesto)?\b",
    r"\bc[aá]diz(?:\s+cf)?\b",
    r"\burgos(?:\s+cf)?\b",
    r"\balbacete(?:\s+balompi[eé])?\b",
    r"\bcastell[oó]n\b",
    r"\bm[aá]laga(?:\s+cf)?\b",
    r"(?<!central\s)\bc[oó]rdoba(?:\s+cf)?\b",
    r"\bdeportivo(?:\s+de)?\s+la\s+coru[ñn]a\b",
    r"\bdepor\b",
    r"\b(?:fc\s+)?cartagena\b",
    r"\b(?:cd\s+)?tenerife\b",
    r"\b(?:cd\s+)?eldense\b",
    r"\b(?:cd\s+)?mirand[eé]s\b",
    r"\b(?:sd\s+)?huesca\b",
    r"\bponferradina\b",
    r"\bcultural\s+leonesa\b",
    r"\bn[aà]stic(?:\s+de\s+tarragona)?\b",
    r"\bgimn[aá]stic\b",
    r"\brecreativo(?:\s+de\s+huelva)?\b",
    r"\breal\s+murcia\b",
    r"\bucam\s+murcia\b",
    r"\bunionistas\b",
    r"\bzamora\s+cf\b",
    r"\bbarakaldo\b",
    r"\bsestao\s+river\b",
    r"\balcoyano\b",
    r"\bh[eé]rcules(?:\s+cf)?\b",
    r"\bsabadell\b",
    r"\bad\s+ceuta\b",

    # Baloncesto (ACB / EuroLeague / EuroCup)
    r"\bbaskonia\b",
    r"\bunicaja(?:\s+m[aá]laga)?\b",
    r"\bjoventut(?:\s+badalona)?\b",
    r"\bla\s+laguna\s+tenerife\b",
    r"\blenovo\s+tenerife\b",
    r"\bgran\s+canaria(?:\s+cb)?\b",
    r"\b(?:baxi\s+)?manresa\b",
    r"\bbilbao\s+basket\b",
    r"\bbreog[aá]n\b",
    r"\bmorabanc\s+andorra\b",
    r"\bfor[cç]a\s+lleida\b",
    r"\bhiopos\s+lleida\b",
    r"\bleyma\s+coru[ñn]a\b",
    r"\bestudiantes(?:\s+madrid|\s+baloncesto)?\b",
    r"\bpalencia\s+baloncesto\b",

    # Balonmano / Futbol Sala
    r"\bbidasoa(?:\s+ir[uú]n)?\b",
    r"\bademar\s+le[oó]n\b",
    r"\bpalma\s+futsal\b",
    r"\belpozo\s+murcia\b",
    r"\bmovistar\s+inter\b",
    r"\binter\s+movistar\b",
    r"\bja[eé]n\s+para[ií]so\b",
    r"\bjimbee\s+cartagena\b",

    # Selecciones
    r"\bespa[ñn]a\b",
    r"\bspain\b",
    r"\bselecci[oó]n\s+espa[ñn]ola\b"
]

RE_SPANISH_TEAMS = re.compile("|".join(f"(?:{p})" for p in SPANISH_TEAMS_PATTERNS), re.IGNORECASE)
RE_EVENT_SEPARATORS = re.compile(r"\b(?:vs\.?|v)\b", re.IGNORECASE)

def is_spanish_team_event(title, group=""):
    """Determina si un titulo corresponde a un evento/partido en el que participa un equipo espanol."""
    if not title:
        return False
    # Debe ser un partido o enfrentamiento (vs, v, etc.)
    if not RE_EVENT_SEPARATORS.search(title):
        return False
    # Debe contener un equipo espanol
    return bool(RE_SPANISH_TEAMS.search(title))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

session = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
session.mount("http://", adapter)
session.mount("https://", adapter)

def get_base_server():
    """Obtiene el dominio activo del servidor desde Firebase Remote Config o fallback."""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 11; Pixel 5 Build/RD2A.210905.003)"
    }
    try:
        r = session.post(FIREBASE_CONFIG_URL, json=FIREBASE_PAYLOAD, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            entries = data.get("entries", {})
            api_url = entries.get("api_url")
            if api_url:
                if not api_url.endswith("/"):
                    api_url += "/"
                logging.info(f"Firebase Remote Config devolvio el servidor activo: {api_url}")
                return api_url
    except Exception as e:
        logging.warning(f"Error al consultar Firebase Remote Config: {e}")
    
    logging.info(f"Usando servidor fallback: {FALLBACK_SERVER}")
    return FALLBACK_SERVER

def fetch_and_decrypt(url):
    """Descarga y desencripta una respuesta AES-128-CBC de AK47Sports."""
    if "?p=" not in url:
        url += "?p=1"
    headers = {
        "User-Agent": "okhttp/4.9.0"
    }
    try:
        r = session.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        text = r.text.strip()
        if not text:
            return None
        data_bytes = base64.b64decode(text)
        cipher = AES.new(KEY, AES.MODE_CBC, IV)
        decrypted = unpad(cipher.decrypt(data_bytes), 16)
        dec_str = decrypted.decode("utf-8", "ignore")
        return json.loads(dec_str)
    except Exception:
        return None

def parse_m3u_url(url, group_name):
    """Parsea una lista M3U externa si esta disponible."""
    channels = []
    try:
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            return channels
        lines = r.text.splitlines()
        current_name = None
        current_logo = ""
        current_group = group_name
        current_license = None
        for line in lines:
            line = line.strip()
            if line.startswith("#EXTINF:"):
                logo_match = re.search(r'tvg-logo="([^"]+)"', line)
                if logo_match:
                    current_logo = logo_match.group(1)
                group_match = re.search(r'group-title="([^"]+)"', line)
                if group_match and group_match.group(1):
                    current_group = f"AK47Sports - {group_match.group(1)}"
                else:
                    current_group = group_name
                comma_idx = line.rfind(",")
                if comma_idx != -1:
                    current_name = line[comma_idx+1:].strip()
                else:
                    current_name = "Canal M3U"
            elif line.startswith("#KODIPROP:inputstream.adaptive.license_key="):
                current_license = line.split("=", 1)[1].strip()
            elif line and not line.startswith("#"):
                if current_name:
                    channels.append({
                        "group": current_group,
                        "title": current_name,
                        "logo": current_logo,
                        "url": line,
                        "license_key": current_license
                    })
                    current_name = None
                    current_logo = ""
                    current_group = group_name
                    current_license = None
    except Exception as e:
        logging.warning(f"Error al descargar playlist M3U externa {url}: {e}")
    return channels

def process_channel_item(ch, cat_name, base_server):
    """Procesa un elemento de canal individual y resuelve sus enlaces de transmision."""
    entries = []
    ch_str = ch.get("channel")
    if not ch_str:
        return entries
    try:
        ch_obj = json.loads(ch_str)
        if not ch_obj.get("visible", True):
            return entries
        
        ch_name = ch_obj.get("name", "Canal").strip()
        ch_logo = ch_obj.get("logo", "")
        ch_links = ch_obj.get("links")

        if ch_links:
            pro_url = base_server + ch_links
            pro_data = fetch_and_decrypt(pro_url)
            if pro_data:
                for stream in pro_data:
                    s_name = stream.get("name", "").strip()
                    s_link = stream.get("link", "").strip()
                    s_api = stream.get("api", "").strip()

                    display_title = f"{ch_name} - {s_name}" if s_name else ch_name

                    if s_link:
                        license_key = s_api if s_api and ":" in s_api else None
                        entries.append({
                            "group": f"AK47Sports - {cat_name}",
                            "title": display_title,
                            "logo": ch_logo,
                            "url": s_link,
                            "license_key": license_key
                        })
    except Exception:
        pass
    return entries

def write_m3u(filepath, entries, default_group=None):
    """Escribe las entradas en un archivo M3U."""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("#EXTM3U x-tvg-url=\"\"\n\n")
        for entry in entries:
            title = entry["title"]
            logo = entry.get("logo", "")
            group = default_group if default_group else entry.get("group", "AK47Sports")
            url = entry["url"]
            license_key = entry.get("license_key")

            f.write(f'#EXTINF:-1 tvg-name="{title}" tvg-logo="{logo}" group-title="{group}",{title}\n')
            if license_key:
                f.write("#KODIPROP:inputstream.adaptive.license_type=clearkey\n")
                f.write(f"#KODIPROP:inputstream.adaptive.license_key={license_key}\n")
            f.write(f"{url}\n\n")
    logging.info(f"Lista guardada exitosamente en '{filepath}' ({len(entries)} canales)")

def generate_playlist(es_only=False):
    if es_only:
        logging.info("Iniciando la extraccion rapida de eventos espanoles de AK47Sports (--es-only)...")
        endpoints = ["sports.txt"]
    else:
        logging.info("Iniciando la extraccion completa de canales de AK47Sports...")
        endpoints = ["sports.txt", "categories.txt"]

    base_server = get_base_server()
    playlist_entries = []
    seen_urls = set()

    for ep in endpoints:
        cat_url = base_server + ep
        logging.info(f"Obteniendo categorias desde {cat_url}...")
        categories = fetch_and_decrypt(cat_url)
        if not categories:
            logging.warning(f"No se pudieron obtener categorias de {ep}")
            continue

        logging.info(f"Obtenidas {len(categories)} categorias de {ep}")

        for item in categories:
            cat_str = item.get("cat")
            if not cat_str:
                continue
            try:
                cat_obj = json.loads(cat_str)
                if not cat_obj.get("visible", True):
                    continue
                
                cat_name = cat_obj.get("name", "AK47Sports").strip()
                cat_type = cat_obj.get("type")
                cat_api = cat_obj.get("api")

                if cat_type == "custom" and cat_api:
                    # En modo --es-only, solo procesamos categorias personalizadas que puedan ser eventos
                    if es_only and not ("event" in cat_name.lower() or "live" in cat_name.lower()):
                        continue

                    chan_url = base_server + cat_api
                    chan_list = fetch_and_decrypt(chan_url)
                    if not chan_list:
                        continue
                    
                    logging.info(f"Procesando categoria '{cat_name}' ({len(chan_list)} canales en paralelo)...")
                    
                    with ThreadPoolExecutor(max_workers=10) as executor:
                        futures = [executor.submit(process_channel_item, ch, cat_name, base_server) for ch in chan_list]
                        for future in as_completed(futures):
                            ch_entries = future.result()
                            for entry in ch_entries:
                                if entry["url"] not in seen_urls:
                                    seen_urls.add(entry["url"])
                                    playlist_entries.append(entry)

                elif cat_type == "m3u" and cat_api:
                    target_api = cat_api.replace("https:///", "https://").replace("http:///", "http://")
                    m3u_entries = parse_m3u_url(target_api, f"AK47Sports - {cat_name}")
                    for entry in m3u_entries:
                        if entry["url"] not in seen_urls:
                            seen_urls.add(entry["url"])
                            playlist_entries.append(entry)

            except Exception as e:
                logging.warning(f"Error procesando elemento de categoria: {e}")

    logging.info(f"Total de canales extraidos en esta ejecucion: {len(playlist_entries)}")

    # Filtrar eventos con equipos espanoles
    es_entries = [
        entry for entry in playlist_entries
        if is_spanish_team_event(entry["title"], entry.get("group", ""))
    ]
    logging.info(f"Total de eventos con equipos espanoles detectados: {len(es_entries)}")

    if not es_only:
        # Guardar lista completa
        write_m3u(M3U_FILE, playlist_entries)

    # Guardar lista reducida espanola
    write_m3u(M3U_ES_FILE, es_entries, default_group="AK47Sports - Eventos España")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scraper de listas M3U para AK47Sports.")
    parser.add_argument(
        "--es-only",
        action="store_true",
        help="Generar unicamente lista_ak47sports_ES.m3u procesando solo categorias deportivas de eventos (mucho mas rapido)."
    )
    args = parser.parse_args()
    generate_playlist(es_only=args.es_only)
