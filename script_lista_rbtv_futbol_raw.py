#!/usr/bin/env python3
"""
Genera una agenda M3U con enlaces RAW de reproductor directo (servidos desde los dominios
de reproductor MadPlay77/Superabbit descubiertos dinámicamente desde la API).

Al acceder a estas URLs en el dominio de reproductor, la aplicación carga directamente
el reproductor del partido sin el error 'no-data'.
"""
import os
import sys
import time
import json
import logging
from datetime import datetime, timezone

import requests
import blackboxprotobuf
from script_lista_rbtv_futbol import (
    crear_session_optimizada,
    obtener_site_base,
    obtener_informacion_sitio_dinamica,
    obtener_partidos_futbol,
    parsear_partido,
    HEADERS,
    REQUEST_TIMEOUT
)

OUTPUT_FILE = "lista_rbtv_futbol_raw.m3u"
LOG_FILE = "lista_rbtv_futbol_raw.log"


def configurar_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def rot47(s):
    """Descodifica cadenas ofuscadas con el cifrado de sustitución ROT-47."""
    x = []
    for i in range(len(s)):
        j = ord(s[i])
        if 33 <= j <= 126:
            x.append(chr(33 + ((j - 33 + 47) % 94)))
        else:
            x.append(s[i])
    return ''.join(x)


def obtener_player_domains_dinamicos(session, data_hosts):
    """Extrae dinámicamente los dominios del reproductor desofuscando el JSON ROT-47 de /api/common/params."""
    player_domains = []
    for host in data_hosts:
        url = f"{host}/api/common/params"
        try:
            resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                text_rot = rot47(resp.text)
                data = json.loads(text_rot)
                g_player = data.get("g_player_domains")
                if g_player:
                    parsed_g = json.loads(g_player)
                    for key in ["foth", "snd", "trd", "fith"]:
                        doms = parsed_g.get(key, [])
                        for d in doms:
                            if isinstance(d, str):
                                d_clean = d.rstrip("/")
                                if d_clean.startswith("http") and d_clean not in player_domains:
                                    player_domains.append(d_clean)
        except Exception as e:
            logging.warning(f"Error extrayendo g_player_domains de {host}: {e}")

    if not player_domains:
        player_domains = [
            "https://jack39eo.mpgreatestclgczbmiddle.my",
            "https://nadia63bc.mp77g69ainei3gx2voxygen.ru",
            "https://lola57.30nqt3mainfkyl16mirror.cfd"
        ]

    logging.info(f"Dominios de reproductor descubiertos ({len(player_domains)}): {player_domains}")
    return player_domains


def generar_m3u_raw(partidos_validos, player_domains, site_base):
    """Construye las entradas M3U con URLs directas de reproductor."""
    primary_domain = player_domains[0] if player_domains else site_base
    partidos_validos.sort(key=lambda p: (p["liga"], p["kickoff"] or datetime.max.replace(tzinfo=timezone.utc)))

    lineas = ["#EXTM3U"]
    total_entradas = 0

    for p in partidos_validos:
        hora = p["kickoff"].strftime("%H:%M") if p["kickoff"] else "--:--"
        base_titulo = f"[{hora}] {p['liga']} - {p['home']} vs {p['away']}"
        logo = p["logo"] or ""
        
        # Sustituir el dominio web base por el dominio de reproductor directo (MadPlay77 / Player domain)
        web_url = p.get("url", "")
        if web_url.startswith(site_base):
            raw_url = web_url.replace(site_base, primary_domain)
        else:
            raw_url = web_url

        lineas.append(f'#EXTINF:-1 tvg-logo="{logo}" group-title="{p["liga"]}",{base_titulo}')
        lineas.append(raw_url)
        total_entradas += 1

    return "\n".join(lineas) + "\n", total_entradas


def main():
    configurar_logging()
    session = crear_session_optimizada()

    site_base = obtener_site_base(session)
    data_hosts, route_pattern = obtener_informacion_sitio_dinamica(session, site_base)
    player_domains = obtener_player_domains_dinamicos(session, data_hosts)

    try:
        partidos_raw = obtener_partidos_futbol(session, data_hosts)
    except Exception as e:
        logging.error(f"No se pudo generar la lista raw: {e}")
        sys.exit(1)

    partidos = [parsear_partido(item, site_base=site_base, route_pattern=route_pattern) for item in partidos_raw]
    partidos_validos = [p for p in partidos if p]

    contenido, total = generar_m3u_raw(partidos_validos, player_domains, site_base)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(contenido)

    logging.info(f"Generado {OUTPUT_FILE} con {total} entradas de reproductor directo.")


if __name__ == "__main__":
    main()

