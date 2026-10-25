#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Scraper y Generador de Listas M3U para DaddyLiveHD / DLHD
=============================================================================
Extrae canales lineales 24/7 (EE.UU., UK, España, Latinoamérica y Deportes)
y eventos deportivos en directo del día desde los mirrors activos de DaddyLiveHD.
Genera listas M3U estándar compatibles con VLC, TiviMate, OTT Navigator, etc.
"""

import urllib.request
import ssl
import json
import re
import os
import sys
import time
from typing import List, Dict, Any, Tuple

# Dominios candidatos con auto-fallback
DLHD_DOMAINS = [
    "https://daddylive.li",
    "https://daddylive.at",
    "https://daddylive.app",
    "https://daddylive.mov"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://daddylive.li/channel"
}

OUTPUT_ALL = "lista_daddylive.m3u"
OUTPUT_SPORTS = "lista_daddylive_deportes.m3u"
OUTPUT_ES = "lista_daddylive_ES.m3u"


def get_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_json(url: str, timeout: int = 12) -> Any:
    """Descarga y decodifica un archivo JSON con manejo de BOM y caracteres escapados."""
    ctx = get_ssl_context()
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        raw = r.read().decode("utf-8", errors="ignore").strip()
        raw = raw.replace("\ufeff", "")
        # Corregir posibles comas finales antes de llaves
        raw = re.sub(r",\s*([\]}])", r"\1", raw)
        return json.loads(raw)


def find_active_domain() -> str:
    """Encuentra el mirror activo de DaddyLiveHD probando conectividad HTTP."""
    ctx = get_ssl_context()
    for d in DLHD_DOMAINS:
        try:
            req = urllib.request.Request(f"{d}/channel", headers=HEADERS)
            with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
                if r.status == 200:
                    print(f"✅ Conexión establecida con DaddyLiveHD: {d}")
                    return d
        except Exception:
            continue
    print("⚠️ No se pudo conectar a los mirrors principales, usando default https://daddylive.li")
    return "https://daddylive.li"


def classify_channel(name: str, url: str) -> str:
    """Clasifica un canal según su nombre y procedencia."""
    n_lower = name.lower()

    # Deportes
    sports_keywords = [
        "sport", "espn", "dazn", "movistar liga", "movistar deportes", "laliga",
        "tnt sport", "bein", "premier sport", "nba", "nfl", "mlb", "nhl", "ufc",
        "f1", "formula 1", "motogp", "golf", "tennis", "eurosport", "fight",
        "wwe", "boxing", "supersport", "racing", "redzone", "fox sport"
    ]
    if any(k in n_lower for k in sports_keywords):
        return "Deportes"

    # España y Latinoamérica
    es_keywords = [
        "antena 3", "telecinco", "la 1", "la 2", "cuatro", "la sexta", "tve",
        "canal sur", "tv3", "aragon tv", "telemadrid", "caracol", "rcn",
        "azteca", "las estrellas", "telefe", "eltrece", "chilevision", "mega",
        "america tv", "latin america", "argentina", "colombia", "mexico", "chile", "spain"
    ]
    if any(k in n_lower for k in es_keywords):
        return "España & Latam"

    # Canales de TV abierta y cable EE.UU. / UK
    us_uk_keywords = [
        "abc", "cbs", "nbc", "fox", "cw", "pbs", "hbo", "cinemax", "amc",
        "tnt", "tbs", "usa network", "discovery", "history", "animal planet",
        "bravo", "fx", "syfy", "comedy central", "paramount", "cnn", "msnbc", "bbc"
    ]
    if any(k in n_lower for k in us_uk_keywords):
        return "USA & UK Networks"

    # Noticias
    if "news" in n_lower or "noticias" in n_lower:
        return "Noticias"

    # Películas y Series
    if any(k in n_lower for k in ["movie", "cinema", "film", "cine", "series", "action"]):
        return "Cine & Series"

    # Música
    if any(k in n_lower for k in ["music", "mtv", "radio"]):
        return "Música"

    return "General"


def extract_247_channels(base_domain: str) -> List[Dict[str, str]]:
    """Extrae canales lineales 24/7 desde los JSONs de DaddyLive."""
    channels = []
    seen_urls = set()

    # 1. Player 9: Canales directos HLS (.m3u8)
    try:
        url_p9 = f"{base_domain}/player/player9.json"
        print(f"📥 Descargando catálogo 24/7 de {url_p9}...")
        p9_data = fetch_json(url_p9, timeout=15)
        print(f"   -> {len(p9_data)} canales cargados desde Player 9.")
        for item in p9_data:
            name = item.get("name", "").strip()
            stream_url = item.get("url", "").strip()
            if not name or not stream_url or stream_url in seen_urls:
                continue
            seen_urls.add(stream_url)
            group = classify_channel(name, stream_url)
            channels.append({
                "id": f"dlhd_{len(channels)+1}",
                "name": name,
                "url": stream_url,
                "logo": "",
                "group": group
            })
    except Exception as e:
        print(f"   [Error] Player 9: {e}")

    # 2. Player 2: Canales ESPN
    try:
        url_p2 = f"{base_domain}/player/player2.json"
        p2_data = fetch_json(url_p2, timeout=8)
        print(f"   -> {len(p2_data)} canales cargados desde Player 2.")
        for item in p2_data:
            name = item.get("name", "").strip()
            stream_url = item.get("url1") or item.get("url2") or ""
            if name and stream_url and stream_url not in seen_urls:
                seen_urls.add(stream_url)
                channels.append({
                    "id": f"dlhd_{len(channels)+1}",
                    "name": f"DaddyLive: {name}",
                    "url": stream_url,
                    "logo": "",
                    "group": "Deportes"
                })
    except Exception as e:
        print(f"   [Error] Player 2: {e}")

    # 3. Player 6: beIN Sports
    try:
        url_p6 = f"{base_domain}/player/player6.json"
        p6_data = fetch_json(url_p6, timeout=8)
        print(f"   -> {len(p6_data)} canales cargados desde Player 6.")
        for item in p6_data:
            name = item.get("name", "").strip()
            stream_url = item.get("url1") or item.get("url2") or ""
            if name and stream_url and stream_url not in seen_urls:
                seen_urls.add(stream_url)
                channels.append({
                    "id": f"dlhd_{len(channels)+1}",
                    "name": f"DaddyLive: {name}",
                    "url": stream_url,
                    "logo": "",
                    "group": "Deportes"
                })
    except Exception as e:
        print(f"   [Error] Player 6: {e}")

    return channels


def extract_live_events(base_domain: str) -> List[Dict[str, str]]:
    """Extrae la agenda de eventos deportivos en directo del día."""
    events_channels = []
    seen_urls = set()

    # Archivo tv1.json: Programación por categorías
    try:
        url_tv1 = f"{base_domain}/player/tv1.json"
        print(f"📅 Descargando agenda de eventos deportivos de {url_tv1}...")
        tv1_data = fetch_json(url_tv1, timeout=8)
        for date_str, cat_list in tv1_data.items():
            if not isinstance(cat_list, list):
                continue
            for cat_item in cat_list:
                cat_name = cat_item.get("Category", "Deportes").replace("⚽", "").replace("🏏", "").replace("🏈", "").replace("🏎️", "").replace("🏒", "").strip()
                for ev in cat_item.get("events", []):
                    ev_time = ev.get("time", "")
                    ev_title = ev.get("event", "Evento en Vivo").strip()
                    for ch in ev.get("channels", []):
                        ch_name = ch.get("channel_name", "Link 1")
                        stream_url = ch.get("embed_1") or ch.get("embed_2") or ch.get("embed_3") or ""
                        if stream_url and stream_url not in seen_urls:
                            seen_urls.add(stream_url)
                            full_name = f"[{ev_time}] {ev_title} ({ch_name})"
                            events_channels.append({
                                "id": f"event_{len(events_channels)+1}",
                                "name": full_name,
                                "url": stream_url,
                                "logo": "",
                                "group": f"Eventos En Vivo - {cat_name}" if cat_name else "Eventos En Vivo"
                            })
        print(f"   -> {len(events_channels)} transmisiones en directo encontradas en TV1.")
    except Exception as e:
        print(f"   [Error] TV1 Schedule: {e}")

    # Archivo tv2.json: Eventos más populares por audiencia
    try:
        url_tv2 = f"{base_domain}/player/tv2.json"
        tv2_data = fetch_json(url_tv2, timeout=8)
        pop_events = tv2_data.get("Popular Live (by viewers)", {}).get("Live Events", [])
        for ev in pop_events:
            ev_title = ev.get("event", "Evento Popular").strip()
            for ch in ev.get("channels", []):
                for key in sorted(ch.keys()):
                    if key.startswith("embed_"):
                        stream_url = ch[key]
                        if stream_url and stream_url not in seen_urls:
                            seen_urls.add(stream_url)
                            full_name = f"🔥 [TOP LIVE] {ev_title} ({key.replace('embed_', 'Opción ')})"
                            events_channels.append({
                                "id": f"pop_{len(events_channels)+1}",
                                "name": full_name,
                                "url": stream_url,
                                "logo": "",
                                "group": "Eventos En Vivo - Destacados"
                            })
        print(f"   -> Eventos populares acumulados en total: {len(events_channels)}")
    except Exception as e:
        print(f"   [Error] TV2 Schedule: {e}")

    return events_channels


def write_m3u(filename: str, channels: List[Dict[str, str]]):
    """Guarda los canales en formato estándar M3U8."""
    with open(filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            logo_attr = f' tvg-logo="{ch["logo"]}"' if ch.get("logo") else ""
            f.write(f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-name="{ch["name"]}"{logo_attr} group-title="{ch["group"]}",{ch["name"]}\n')
            f.write(f'{ch["url"]}\n')


def main():
    print("=================================================================")
    print("🚀 Iniciando Scraper de DaddyLiveHD / DLHD")
    print("=================================================================")

    domain = find_active_domain()

    # 1. Extraer canales 24/7
    all_channels = extract_247_channels(domain)

    # 2. Extraer eventos en vivo del día
    live_events = extract_live_events(domain)

    # Combinar
    full_list = live_events + all_channels

    if not full_list:
        print("❌ No se pudieron recuperar canales. Verifica la conexión o los mirrors.")
        sys.exit(1)

    print("\n=================================================================")
    print(f"📊 Estadísticas:")
    print(f"   - Eventos en directo del día: {len(live_events)}")
    print(f"   - Canales 24/7 lineales: {len(all_channels)}")
    print(f"   - Total acumulado: {len(full_list)}")
    print("=================================================================")

    # Guardar lista completa
    write_m3u(OUTPUT_ALL, full_list)
    print(f"✅ Lista completa guardada: {OUTPUT_ALL} ({len(full_list)} canales)")

    # Guardar lista exclusiva de Deportes y Eventos en Directo
    sports_list = [ch for ch in full_list if "Deportes" in ch["group"] or "Eventos En Vivo" in ch["group"]]
    write_m3u(OUTPUT_SPORTS, sports_list)
    print(f"⚽ Lista exclusiva de Deportes: {OUTPUT_SPORTS} ({len(sports_list)} canales)")

    # Guardar lista exclusiva de España y Latinoamérica
    es_list = [ch for ch in full_list if "España & Latam" in ch["group"] or "Eventos En Vivo" in ch["group"]]
    write_m3u(OUTPUT_ES, es_list)
    print(f"🇪🇸 Lista de España & Latam: {OUTPUT_ES} ({len(es_list)} canales)")

    print("\n🎉 ¡Proceso completado con éxito!")


if __name__ == "__main__":
    main()
