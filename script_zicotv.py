#!/usr/bin/env python3
"""
Script de descarga y actualización de canales ZicoTV (HLS / M3U)
Extrae canales 24/7 y emisiones de eventos deportivos en directo (DAZN MotoGP, DAZN LaLiga, Movistar+, etc.)
Genera: lista_zicotv.m3u
"""

import json
import re
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from curl_cffi import requests
    USE_CURL_CFFI = True
except ImportError:
    import requests
    USE_CURL_CFFI = False

BASE_URLS = [
    'https://bp.zicotv.cc',
    'https://zicotv.cc'
]

OUTPUT_M3U = 'lista_zicotv.m3u'
LOGOS_XML_PATH = 'LOGOS-CANALES-TV.xml'

USER_AGENTS = [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15',
    'Mozilla/5.0 (iPad; CPU OS 18_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 18_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:134.0) Gecko/20100101 Firefox/134.0',
]

HEADERS = {
    'User-Agent': USER_AGENTS[0],
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
}

# Canales fijos 24/7 conocidos de la plataforma
CANALES_FIJOS = {
    1: {'name': 'DAZN LaLiga', 'group': 'Fútbol'},
    2: {'name': 'DAZN 1 Spain', 'group': 'Deportes'},
    3: {'name': 'DAZN 2 Spain', 'group': 'Deportes'},
    4: {'name': 'LaLiga TV Bar', 'group': 'Fútbol'},
    5: {'name': 'Movistar Liga de Campeones 3', 'group': 'Fútbol'},
    6: {'name': 'Movistar Liga de Campeones', 'group': 'Fútbol'},
    7: {'name': 'Movistar Liga de Campeones 4', 'group': 'Fútbol'},
    8: {'name': 'Movistar Liga de Campeones 2', 'group': 'Fútbol'},
    9: {'name': 'Movistar #Vamos', 'group': 'Deportes'},
    10: {'name': 'ESPN Deportes', 'group': 'Deportes'},
    11: {'name': 'DAZN F1 Spain', 'group': 'Motor'},
    12: {'name': 'LaLiga TV Hypermotion', 'group': 'Fútbol'},
    13: {'name': 'Sky Sports Main Event', 'group': 'Deportes'},
    14: {'name': 'Movistar Liga de Campeones 5', 'group': 'Fútbol'},
    15: {'name': 'DAZN LaLiga 2', 'group': 'Fútbol'},
    16: {'name': 'Sport TV 2', 'group': 'Deportes'},
    17: {'name': 'Amazon Prime Video Spain', 'group': 'Deportes'},
    18: {'name': 'Movistar Deportes 3', 'group': 'Deportes'},
    19: {'name': 'Movistar Deportes', 'group': 'Deportes'},
    20: {'name': 'Movistar Deportes 6', 'group': 'Deportes'},
    21: {'name': 'UFC', 'group': 'Combate'},
    22: {'name': 'LaLiga TV 2 por Movistar Plus', 'group': 'Fútbol'},
    23: {'name': 'Eurosport 1 Spain', 'group': 'Deportes'},
    24: {'name': 'LaLiga TV Hypermotion 2', 'group': 'Fútbol'},
    25: {'name': 'NBA 1', 'group': 'Baloncesto'},
    28: {'name': 'Movistar Plus+', 'group': 'General'},
    32: {'name': 'Multichampions', 'group': 'Fútbol'},
    34: {'name': 'DAZN 3 Spain', 'group': 'Deportes'},
    35: {'name': 'Movistar Deportes 2', 'group': 'Deportes'},
    36: {'name': 'Eurosport 2 Spain', 'group': 'Deportes'},
    37: {'name': 'DAZN 4 Spain', 'group': 'Deportes'},
    38: {'name': 'LaLiga TV Bar Aux', 'group': 'Fútbol'},
    39: {'name': 'Movistar LaLiga 4', 'group': 'Fútbol'},
    41: {'name': 'Movistar LaLiga 3', 'group': 'Fútbol'},
    46: {'name': 'Movistar Liga de Campeones 8', 'group': 'Fútbol'},
    47: {'name': 'Movistar Liga de Campeones 7', 'group': 'Fútbol'},
    48: {'name': 'Movistar Liga de Campeones 6', 'group': 'Fútbol'},
}

# Logos directos frecuentes
LOGOS_DESTACADOS = {
    'dazn motogp': 'https://raw.githubusercontent.com/Icastresana/logos/main/dazn_motogp.png',
    'dazn f1': 'https://upload.wikimedia.org/wikipedia/commons/thumb/1/1a/DAZN_F1_logo.png/512px-DAZN_F1_logo.png',
    'dazn f1 spain': 'https://upload.wikimedia.org/wikipedia/commons/thumb/1/1a/DAZN_F1_logo.png/512px-DAZN_F1_logo.png',
    'dazn laliga': 'https://upload.wikimedia.org/wikipedia/commons/9/98/DAZN_LaLiga_2023_Logo.svg',
    'dazn laliga 2': 'https://upload.wikimedia.org/wikipedia/commons/9/98/DAZN_LaLiga_2023_Logo.svg',
    'dazn 1': 'https://upload.wikimedia.org/wikipedia/commons/a/a2/DAZN_1_Logo.svg',
    'dazn 1 spain': 'https://upload.wikimedia.org/wikipedia/commons/a/a2/DAZN_1_Logo.svg',
    'dazn 2': 'https://upload.wikimedia.org/wikipedia/commons/a/a2/DAZN_1_Logo.svg',
    'dazn 2 spain': 'https://upload.wikimedia.org/wikipedia/commons/a/a2/DAZN_1_Logo.svg',
    'movistar #vamos': 'https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/Vamos_logo_2021.svg/512px-Vamos_logo_2021.svg.png',
    'movistar plus+': 'https://upload.wikimedia.org/wikipedia/commons/thumb/0/07/Movistar_Plus%2B_2023_logo.svg/512px-Movistar_Plus%2B_2023_logo.svg.png',
    'eurosport 1': 'https://upload.wikimedia.org/wikipedia/commons/thumb/d/d0/Eurosport_1_Logo_2015.svg/512px-Eurosport_1_Logo_2015.svg.png',
    'eurosport 2': 'https://upload.wikimedia.org/wikipedia/commons/thumb/4/4c/Eurosport_2_Logo_2015.svg/512px-Eurosport_2_Logo_2015.svg.png',
}

def cargar_diccionario_logos(xml_path=LOGOS_XML_PATH):
    """Carga los logos desde el archivo XML si existe."""
    logos_dict = dict(LOGOS_DESTACADOS)
    if not os.path.exists(xml_path):
        return logos_dict

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for ch in root.findall('.//channel'):
            name = ch.get('name')
            logo = ch.find('logo_url')
            if name and logo is not None and logo.text:
                clean_name = name.lower().strip()
                if clean_name not in logos_dict:
                    logos_dict[clean_name] = logo.text.strip()
    except Exception as e:
        print(f"Nota: No se pudo parsear {xml_path}: {e}")

    return logos_dict

def buscar_logo(channel_name, logos_dict):
    """Busca el logo más adecuado para el canal."""
    if not channel_name:
        return ""
    name_clean = channel_name.lower().strip()

    # Coincidencia exacta
    if name_clean in logos_dict:
        return logos_dict[name_clean]

    # Coincidencia parcial
    for k, v in logos_dict.items():
        if k in name_clean or name_clean in k:
            return v

    return ""

def normalizar_grupo(sport):
    """Traduce o normaliza la categoría deportiva al español."""
    traducciones = {
        'soccer': 'Fútbol',
        'football': 'Fútbol',
        'basketball': 'Baloncesto',
        'motorsport': 'Motor',
        'fighting': 'Combate',
        'tennis': 'Tenis',
        'ice hockey': 'Hockey Hielo',
        'volleyball': 'Voleibol',
        'handball': 'Balonmano',
        'american football': 'Fútbol Americano',
        'padel': 'Pádel',
        'darts': 'Dardos',
        'futsal': 'Fútbol Sala',
        'cricket': 'Cricket',
        'other': 'Eventos en Directo',
    }
    if not sport:
        return 'General'
    return traducciones.get(sport.lower().strip(), sport.capitalize())

def crear_sesion():
    """Crea una sesión HTTP optimizada con soporte para browser TLS impersonation."""
    if USE_CURL_CFFI:
        return requests.Session(impersonate="safari17_0")
    s = requests.Session()
    return s

def obtener_base_url_activa():
    """Determina qué dominio base responde correctamente."""
    s = crear_sesion()
    for base in BASE_URLS:
        try:
            r = s.get(f"{base}/api/v1", headers=HEADERS, timeout=6)
            if r.status_code == 200:
                return base
        except Exception:
            pass
    return BASE_URLS[0]

def resolver_stream_canal(base_url, channel_id, channel_name, group_title, logos_dict, session=None):
    """
    Obtiene el reproductor del canal, realiza el POST de renovación y extrae la URL HLS activa.
    Soporta curl_cffi (Safari/Chrome), requests estándar, múltiples dominios base y diagnósticos detallados.
    """
    candidate_bases = [base_url]
    for b in BASE_URLS:
        if b not in candidate_bases:
            candidate_bases.append(b)

    debug_channel = channel_id in (1, 2, 3, 4, 5)

    for current_base in candidate_bases:
        player_url = f"{current_base}/api/player/{channel_id}"

        for ua in USER_AGENTS:
            # Probar sesiones: curl_cffi y standard requests como fallback
            sessions_to_try = []
            if session:
                sessions_to_try.append(("custom_session", session))
            else:
                if USE_CURL_CFFI:
                    try:
                        sessions_to_try.append(("curl_cffi_safari", requests.Session(impersonate="safari17_0")))
                    except Exception:
                        pass
                    try:
                        sessions_to_try.append(("curl_cffi_chrome", requests.Session(impersonate="chrome124")))
                    except Exception:
                        pass
                try:
                    import requests as std_req
                    sessions_to_try.append(("std_requests", std_req.Session()))
                except Exception:
                    pass

            for s_name, s in sessions_to_try:
                # Probar sin Referer (como en un iframe limpio) y con Referer
                for send_referer in [False, True]:
                    req_headers = {
                        'User-Agent': ua,
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
                    }
                    if send_referer:
                        req_headers['Referer'] = f"{current_base}/"

                    try:
                        r_player = s.get(player_url, headers=req_headers, timeout=10)
                        if r_player.status_code != 200:
                            if debug_channel and not send_referer and s_name.startswith("curl"):
                                print(f"  [DEBUG {channel_id} | {s_name}] GET {player_url} -> HTTP {r_player.status_code} ({len(r_player.text)}b)")
                            continue

                        html = r_player.text

                        # Obtener título real si está disponible
                        m_title = re.search(r'<title>Reproductor\s*-\s*([^<]+)</title>', html)
                        if m_title and m_title.group(1).strip():
                            channel_name = m_title.group(1).strip()

                        # Extraer ZT_RENEW
                        m_renew = re.search(r'var\s+ZT_RENEW\s*=\s*(\{.*?\});', html)
                        if not m_renew:
                            if debug_channel and not send_referer:
                                print(f"  [DEBUG {channel_id} | {s_name}] ZT_RENEW no encontrado. Título: {m_title.group(0) if m_title else 'None'}, HTML: {html[:150]!r}")
                            continue

                        renew_info = json.loads(m_renew.group(1))
                        renew_rel_path = renew_info.get('url')
                        if not renew_rel_path:
                            continue

                        renew_full_url = f"{current_base}{renew_rel_path}"

                        post_headers = {
                            'User-Agent': ua,
                            'Accept': 'application/json, text/plain, */*',
                            'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
                            'Content-Type': 'application/json',
                            'Referer': player_url,
                            'Origin': current_base,
                        }

                        # Probar renovación con native: true primero (3 horas) y fallback a sin native
                        r_renew = None
                        for payload in [{'native': True}, {'native': True, 'view': True}, {}]:
                            try:
                                r_renew = s.post(renew_full_url, json=payload, headers=post_headers, timeout=10)
                                if r_renew.status_code == 200:
                                    break
                            except Exception:
                                pass

                        if not r_renew or r_renew.status_code != 200:
                            if debug_channel and not send_referer:
                                print(f"  [DEBUG {channel_id} | {s_name}] POST renew -> HTTP {r_renew.status_code if r_renew else 'None'}, Resp: {r_renew.text[:100] if r_renew else ''!r}")
                            continue

                        renew_data = r_renew.json()
                        sources = renew_data.get('sources', [])
                        valid_sources = [src for src in sources if src.get('url') and not src.get('locked')]

                        if not valid_sources:
                            continue

                        # Tomar la primera CDN disponible
                        chosen_source = valid_sources[0]
                        stream_url = chosen_source.get('url')
                        logo_url = buscar_logo(channel_name, logos_dict)

                        return {
                            'id': channel_id,
                            'name': channel_name,
                            'group': group_title,
                            'logo': logo_url,
                            'url': stream_url,
                            'cdn_label': chosen_source.get('label', 'CDN')
                        }
                    except Exception as e:
                        if debug_channel and not send_referer and s_name.startswith("curl"):
                            print(f"  [DEBUG {channel_id} | {s_name}] Excepción: {type(e).__name__}: {e}")
                        continue
    return None

def recolectar_canales_disponibles(base_url):
    """
    Combina los canales fijos 24/7 con los canales propios de eventos en directo
    obtenidos desde la API pública /v1/events (ej. DAZN MotoGP).
    """
    canales_dict = {}

    # 1. Agregar canales fijos 24/7
    for cid, info in CANALES_FIJOS.items():
        canales_dict[cid] = {
            'name': info['name'],
            'group': info['group']
        }

    # 2. Consultar eventos actuales para capturar canales de eventos en directo
    events_url = f"{base_url}/api/v1/events"
    try:
        print(f"Consultando eventos en directo desde {events_url}...")
        s = crear_sesion()
        r_events = s.get(events_url, headers=HEADERS, timeout=10)
        if r_events.status_code == 200:
            events_data = r_events.json().get('data', [])
            print(f"Eventos obtenidos: {len(events_data)}")
            for event in events_data:
                sport = normalizar_grupo(event.get('sport_category'))
                event_title = event.get('title') or ''
                for source in event.get('sources', []):
                    if source.get('source') == 'own':
                        cid = source.get('channel_id')
                        cname = source.get('name') or f"Canal {cid}"
                        if cid and cid not in canales_dict:
                            if event_title and event_title != cname:
                                full_name = f"{cname} - {event_title}"
                            else:
                                full_name = cname
                            canales_dict[cid] = {
                                'name': full_name,
                                'group': sport
                            }
    except Exception as e:
        print(f"Advertencia al consultar eventos de ZicoTV: {e}")

    print(f"Total de canales identificados para resolver: {len(canales_dict)}")
    return canales_dict

def generar_lista_m3u(canales_resueltos, archivo_salida=OUTPUT_M3U):
    """Genera el archivo M3U con los canales y sus URLs resueltas."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    proxy_base = os.environ.get("PROXY_BASE_URL", "").rstrip("/")
    contenido = "#EXTM3U\n"
    contenido += f"# ZicoTV Channels List - {timestamp}\n"
    contenido += f"# Canales activos: {len(canales_resueltos)}\n"
    if proxy_base:
        contenido += f"# Modo Proxy activado: {proxy_base}\n"
    contenido += "\n"

    # Ordenar por grupo y luego por nombre
    canales_ordenados = sorted(canales_resueltos, key=lambda c: (c['group'], c['name']))

    grupo_actual = None
    for c in canales_ordenados:
        if c['group'] != grupo_actual:
            grupo_actual = c['group']
            contenido += f"\n# -------- {grupo_actual.upper()} --------\n"

        tvg_id = f"zicotv-{c['id']}"
        logo_attr = f' tvg-logo="{c["logo"]}"' if c.get('logo') else ''
        contenido += f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{c["name"]}"{logo_attr} group-title="{c["group"]}",{c["name"]}\n'
        stream_url = f"{proxy_base}/zico/live/{c['id']}.m3u8" if proxy_base else c['url']
        contenido += f"{stream_url}\n"

    with open(archivo_salida, 'w', encoding='utf-8') as f:
        f.write(contenido)

    tamano = os.path.getsize(archivo_salida)
    print(f"\n✓ Archivo '{archivo_salida}' generado correctamente:")
    print(f"  - Canales activos incluidos: {len(canales_resueltos)}")
    print(f"  - Tamaño del archivo: {tamano:,} bytes")

def main():
    print("=" * 60)
    print("Script de Actualización de Canales ZicoTV")
    print("=" * 60)

    logos_dict = cargar_diccionario_logos()
    print(f"Diccionario de logos cargado ({len(logos_dict)} referencias).")

    base_url = obtener_base_url_activa()
    print(f"Usando dominio base: {base_url}")

    canales_meta = recolectar_canales_disponibles(base_url)

    print(f"\nResolviendo enlaces HLS con tokens activos en paralelo ({'curl_cffi HTTP/3' if USE_CURL_CFFI else 'requests'})...")
    canales_resueltos = []
    fallos = []

    with ThreadPoolExecutor(max_workers=6) as executor:
        futuros = {
            executor.submit(
                resolver_stream_canal,
                base_url,
                cid,
                meta['name'],
                meta['group'],
                logos_dict
            ): (cid, meta['name']) for cid, meta in canales_meta.items()
        }

        for f in as_completed(futuros):
            cid, cname = futuros[f]
            try:
                res = f.result()
                if res and res.get('url'):
                    print(f"  ✓ [{res['id']:5d}] {res['name']:<35} ({res['cdn_label']})")
                    canales_resueltos.append(res)
                else:
                    fallos.append(f"[{cid}] {cname}")
            except Exception as e:
                fallos.append(f"[{cid}] {cname}: {e}")

    if not canales_resueltos:
        print("\n✗ No se pudieron resolver canales válidos.")
        if fallos:
            print(f"Muestra de canales no resueltos ({min(5, len(fallos))} de {len(fallos)}):")
            for fal in fallos[:5]:
                print(f"  - {fal}")
        return False

    generar_lista_m3u(canales_resueltos)
    print("\n✓ Proceso completado exitosamente.")
    return True

if __name__ == '__main__':
    ok = main()
    sys.exit(0 if ok else 1)
