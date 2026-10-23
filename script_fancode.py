#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script para extraer todos los eventos deportivos de FanCode y generar listas M3U.
Procesa eventos en vivo y programados (Fútbol, Cricket, Tenis, F1, MotoGP, etc.),
resuelve los enlaces HLS a través de CDN/Proxy compatible y genera 'lista_fancode.m3u'.
"""

import argparse
import json
import logging
import os
import re
import sys
import requests

# Configuración de URLs fuente
FANCODE_SOURCES = [
    {
        "name": "drmlive_json",
        "url": "https://raw.githubusercontent.com/drmlive/fancode-live-events/main/fancode.json",
        "type": "json_drmlive"
    },
    {
        "name": "bytecapsule_json",
        "url": "https://raw.githubusercontent.com/byte-capsule/FanCode-Hls-Fetcher/main/Fancode_hls_m3u8.Json",
        "type": "json_bytecapsule"
    },
    {
        "name": "zyphx8_fancode_m3u",
        "url": "https://raw.githubusercontent.com/doctor-8trange/zyphx8/refs/heads/main/data/fancode.m3u",
        "type": "m3u_direct"
    },
    {
        "name": "zyphx8_np_m3u",
        "url": "https://raw.githubusercontent.com/doctor-8trange/zyphx8/refs/heads/main/data/np.m3u",
        "type": "m3u_direct"
    },
    {
        "name": "kajju027_m3u",
        "url": "https://raw.githubusercontent.com/kajju027/Fancode-Events-Json/main/fc.m3u",
        "type": "m3u_direct"
    }
]

LOG_FILE = "lista_fancode.log"
M3U_FILE = "lista_fancode.m3u"
M3U_ES_FILE = "lista_fancode_ES.m3u"
M3U_DIRECT_FILE = "lista_fancode_direct.m3u"

PROXY_DOMAIN = "https://dai-fancode.pages.dev"
PLAYER_HEADERS = "User-Agent=ReactNativeVideo/9.11.1 (Linux;Android 13) AndroidXMedia3/1.6.1&Referer=https://fancode.com/"

# Patrones de equipos o competiciones españolas
SPANISH_PATTERNS = [
    r"\blaliga\b", r"\bhypermotion\b", r"\bcopa\s+del\s+rey\b",
    r"\breal\s+madrid\b", r"\bbarcelona\b", r"\bbar[cç]a\b", r"\batl[eé]tico(?:\s+de)?\s+madrid\b",
    r"\bsevil+a\b", r"\bbetis\b", r"\breal\s+sociedad\b", r"\bvillar+eal\b", r"\bvalencia\b",
    r"\bathletic\b", r"\bgirona\b", r"\bcelta\b", r"\bosasuna\b", r"\brayo\b", r"\bmallorca\b",
    r"\balav[eé]s\b", r"\bgetafe\b", r"\bespanyol\b", r"\blas\s+palmas\b", r"\blegan[eé]s\b",
    r"\bval+adolid\b", r"\blevante\b", r"\bzaragoza\b", r"\bracing\b", r"\boviedo\b",
    r"\bsporting\b", r"\belche\b", r"\beibar\b", r"\balmer[ií]a\b", r"\bgranada\b",
    r"\bc[aá]diz\b", r"\burgos\b", r"\balbacete\b", r"\bcastell[oó]n\b", r"\bm[aá]laga\b",
    r"\bc[oó]rdoba\b", r"\bdeportivo\b", r"\bdepor\b", r"\bcartagena\b", r"\btenerife\b",
    r"\bspain\b", r"\bespa[ñn]a\b"
]
RE_SPANISH = re.compile("|".join(f"(?:{p})" for p in SPANISH_PATTERNS), re.IGNORECASE)

# Configuración de Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
})

def is_spanish_event(title, group=""):
    """Detecta si un evento corresponde a competición o equipo español."""
    full_text = f"{title} {group}"
    return bool(RE_SPANISH.search(full_text))

def build_stream_urls(raw_url):
    """
    Construye las variantes de URL para el stream:
    - proxy_url: URL a través de dai-fancode.pages.dev sin geo-bloqueo y con headers para reproductores
    - direct_url: URL original con token CDN de FanCode
    """
    if not raw_url:
        return None, None

    direct_url = raw_url.strip()
    
    # Si es un enlace CDN de in-mc-flive.fancode.com
    path_match = re.search(r'fancode\.com(/[^?]+)', direct_url)
    if path_match:
        rel_path = path_match.group(1)
        # Sustituir /1080p.m3u8 o similar por /index.m3u8
        idx_path = re.sub(r'/\d+p\.m3u8$', '/index.m3u8', rel_path)
        if not idx_path.endswith('.m3u8'):
            idx_path = idx_path.rstrip('/') + '/index.m3u8'
        proxy_url = f"{PROXY_DOMAIN}{idx_path}|{PLAYER_HEADERS}"
    elif "dai.google.com" in direct_url:
        proxy_url = direct_url
    else:
        proxy_url = f"{direct_url}|{PLAYER_HEADERS}" if "|" not in direct_url else direct_url

    return proxy_url, direct_url

def parse_drmlive_json(data):
    """Parsea el formato JSON detallado de drmlive."""
    matches = data.get("matches", [])
    events = []

    for m in matches:
        match_id = str(m.get("match_id", "")).strip()
        category = m.get("event_category", "Deportes").strip()
        event_name = m.get("event_name", "").strip()
        match_name = m.get("match_name", "").strip()
        title = m.get("title", "").strip()
        status = m.get("status", "LIVE").strip().upper()
        start_time = m.get("startTime", "").strip()
        logo = m.get("src", "").strip()
        raw_url = m.get("adfree_url") or m.get("dai_url", "")

        proxy_url, direct_url = build_stream_urls(raw_url)
        if not proxy_url:
            continue

        # Formatear nombre para el canal M3U
        display_name = match_name if match_name else title
        if not display_name:
            display_name = event_name or f"FanCode Match {match_id}"

        status_tag = f"[{status}]" if status else ""
        time_tag = f"({start_time})" if start_time and status != "LIVE" else ""
        final_title = f"{status_tag} {display_name} {time_tag}".strip()
        final_title = re.sub(r'\s+', ' ', final_title)

        group_title = f"FanCode - {category.title()}"

        events.append({
            "id": f"fancode-{match_id}",
            "title": final_title,
            "raw_name": display_name,
            "logo": logo,
            "group": group_title,
            "category": category,
            "status": status,
            "start_time": start_time,
            "url": proxy_url,
            "direct_url": direct_url
        })

    return events

def parse_bytecapsule_json(data):
    """Parsea el formato JSON de byte-capsule."""
    matches = data.get("matches", [])
    events = []

    for m in matches:
        match_id = str(m.get("match_id", "")).strip()
        category = m.get("event_catagory", "Deportes").strip()
        event_name = m.get("event_name", "").strip()
        match_name = m.get("match_name", "").strip()
        banner = m.get("banner", "").strip()
        raw_url = m.get("stream_link", "").strip()

        proxy_url, direct_url = build_stream_urls(raw_url)
        if not proxy_url:
            continue

        display_name = match_name or event_name or f"FanCode Event {match_id}"
        group_title = f"FanCode - {category.title()}"

        events.append({
            "id": f"fancode-{match_id}",
            "title": f"[LIVE] {display_name}",
            "raw_name": display_name,
            "logo": banner,
            "group": group_title,
            "category": category,
            "status": "LIVE",
            "start_time": "",
            "url": proxy_url,
            "direct_url": direct_url
        })

    return events

def parse_m3u_text(content):
    """Parsea una lista M3U estándar de FanCode."""
    events = []
    lines = content.splitlines()
    current_title = None
    current_logo = ""
    current_group = "FanCode - Eventos"
    current_id = ""

    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF:"):
            logo_m = re.search(r'tvg-logo="([^"]+)"', line)
            current_logo = logo_m.group(1) if logo_m else ""
            
            group_m = re.search(r'group-title="([^"]+)"', line)
            if group_m and group_m.group(1):
                cat = group_m.group(1).replace("Fancode-", "").replace("FanCode - ", "")
                current_group = f"FanCode - {cat.title()}"
            else:
                current_group = "FanCode - Eventos"

            id_m = re.search(r'tvg-id="([^"]+)"', line)
            current_id = id_m.group(1) if id_m else ""

            comma_idx = line.rfind(",")
            if comma_idx != -1:
                current_title = line[comma_idx+1:].strip()
            else:
                current_title = "Evento FanCode"

        elif line and not line.startswith("#"):
            if current_title:
                proxy_url, direct_url = build_stream_urls(line)
                if proxy_url:
                    events.append({
                        "id": f"fancode-{current_id}" if current_id else f"fancode-{len(events)+1}",
                        "title": current_title,
                        "raw_name": current_title,
                        "logo": current_logo,
                        "group": current_group,
                        "category": current_group.replace("FanCode - ", ""),
                        "status": "LIVE",
                        "start_time": "",
                        "url": proxy_url,
                        "direct_url": direct_url
                    })
                current_title = None
                current_logo = ""
                current_group = "FanCode - Eventos"
                current_id = ""

    return events

def get_canonical_stream_key(ev):
    """Extrae un identificador único canónico del evento o stream para evitar duplicados entre fuentes."""
    url = ev.get("direct_url") or ev.get("url") or ""
    
    # 1. Identificador de stream FanCode CDN (ej: mumbai/4250107_...)
    m_mumbai = re.search(r'mumbai/([a-zA-Z0-9_-]+)', url)
    if m_mumbai:
        base_slug = m_mumbai.group(1).split('_')[0]
        return f"stream_{base_slug}"
        
    # 2. Identificador de stream Google DAI
    m_dai = re.search(r'linear/hls/event/([a-zA-Z0-9_-]+)', url)
    if m_dai:
        return f"dai_{m_dai.group(1)}"
        
    # 3. Identificador numérico de match_id
    raw_id = ev.get("id", "").replace("fancode-", "").split("_")[0]
    if raw_id and raw_id.isdigit():
        return f"id_{raw_id}"
        
    # 4. Fallback por nombre normalizado
    norm_name = re.sub(r'[^a-zA-Z0-9]', '', ev.get("raw_name", "").lower())
    return f"name_{norm_name}"

def fetch_all_events():
    """Descarga eventos desde las fuentes configuradas con fallback automático y deduplicación inteligente."""
    all_events = []
    seen_keys = set()

    for source in FANCODE_SOURCES:
        name = source["name"]
        url = source["url"]
        stype = source["type"]

        logging.info(f"Consultando fuente FanCode '{name}' ({url})...")
        try:
            r = session.get(url, timeout=10)
            if r.status_code != 200:
                logging.warning(f"Fuente '{name}' devolvió código HTTP {r.status_code}")
                continue

            parsed_events = []
            if stype == "json_drmlive":
                parsed_events = parse_drmlive_json(r.json())
            elif stype == "json_bytecapsule":
                parsed_events = parse_bytecapsule_json(r.json())
            elif stype == "m3u_direct":
                parsed_events = parse_m3u_text(r.text)

            logging.info(f"Fuente '{name}' proporcionó {len(parsed_events)} eventos.")

            for ev in parsed_events:
                # Normalizar categoría y grupo
                cat = ev.get("category", "Deportes").strip().title()
                ev["category"] = cat
                ev["group"] = f"FanCode - {cat}"

                key = get_canonical_stream_key(ev)
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_events.append(ev)

        except Exception as e:
            logging.warning(f"Error al procesar fuente '{name}': {e}")

    logging.info(f"Total de eventos únicos recopilados: {len(all_events)}")
    return all_events

def write_m3u(filepath, events, use_direct=False):
    """Escribe los eventos en un archivo M3U con metadatos IPTV completos."""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("#EXTM3U x-tvg-url=\"\"\n\n")
        for ev in events:
            tvg_id = ev.get("id", "")
            title = ev.get("title", "Evento")
            logo = ev.get("logo", "")
            group = ev.get("group", "FanCode")
            stream_url = ev.get("direct_url") if use_direct else ev.get("url")

            if not stream_url:
                continue

            f.write(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{title}" tvg-logo="{logo}" group-title="{group}",{title}\n')
            f.write('#EXTVLCOPT:http-user-agent=ReactNativeVideo/9.11.1 (Linux;Android 13) AndroidXMedia3/1.6.1\n')
            f.write('#EXTVLCOPT:http-referrer=https://fancode.com/\n')
            f.write(f"{stream_url}\n\n")

    logging.info(f"Archivo M3U guardado con éxito en '{filepath}' ({len(events)} canales).")

def main():
    parser = argparse.ArgumentParser(description="Scraper y generador de listas M3U para FanCode.")
    parser.add_argument("--live-only", action="store_true", help="Incluir únicamente eventos en directo (LIVE).")
    parser.add_argument("--direct", action="store_true", help="Generar también lista con enlaces CDN directos sin proxy.")
    args = parser.parse_args()

    logging.info("=== Iniciando generador M3U para FanCode ===")
    events = fetch_all_events()

    if not events:
        logging.error("No se pudo obtener ningún evento de FanCode.")
        return 1

    # Filtrar por LIVE si se solicita
    if args.live_only:
        events = [e for e in events if e.get("status") == "LIVE"]
        logging.info(f"Filtrados {len(events)} eventos activos (LIVE).")

    # Guardar lista principal (con Proxy global)
    write_m3u(M3U_FILE, events, use_direct=False)

    # Si se solicitó la lista directa
    if args.direct:
        write_m3u(M3U_DIRECT_FILE, events, use_direct=True)

    # Filtrar eventos españoles (LaLiga Hypermotion, equipos españoles, etc.)
    es_events = [e for e in events if is_spanish_event(e["title"], e.get("group", ""))]
    if es_events:
        logging.info(f"Se detectaron {len(es_events)} eventos con participación o competición española.")
        write_m3u(M3U_ES_FILE, es_events, use_direct=False)
    else:
        logging.info("No se detectaron eventos específicos con equipos españoles en este momento.")

    # Resumen por categorías
    categories = {}
    for e in events:
        cat = e.get("category", "Otros")
        categories[cat] = categories.get(cat, 0) + 1

    logging.info("Resumen de eventos por categoría:")
    for cat, count in sorted(categories.items(), key=lambda x: x[1], reverse=True):
        logging.info(f"  - {cat}: {count} transmisiones")

    logging.info("=== Proceso finalizado con éxito ===")
    return 0

if __name__ == "__main__":
    sys.exit(main())
