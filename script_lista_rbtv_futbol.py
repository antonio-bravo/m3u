#!/usr/bin/env python3
"""
Genera una agenda M3U de partidos de futbol desde la API publica de RBTV+/Superabbit77.

Flujo (reversado desde com.rblive.app-release-321-v3.0.321.apk, ApiConstants.java):
  1) GET /api/common/bs?code=100&sportType=1&stream=true  -> firma temporal (params)
  2) GET /sfver{params}/api/match/live?sportType=1&language=0&stream=true -> protobuf

La respuesta no tiene esquema publico (protobuf lite sin .proto), asi que se
decodifica de forma generica con blackboxprotobuf y se leen los campos por
posicion, verificados manualmente contra los nombres de las clases *OrBuilder.java
del APK (PBDataMatch, PBDataLeague, PBDataTeam).

No se generan enlaces de reproduccion (.m3u8): ese endpoint vive en otro host
("live/other") protegido por el modulo de pago/activacion de la app y no se
intenta sortear. Cada entrada enlaza a la pagina de futbol del sitio.
"""
import os
import sys
import time
import logging
from datetime import datetime, timezone

import requests
import blackboxprotobuf

# ==================== CONFIGURACION ====================
OUTPUT_FILE = "lista_rbtv_futbol.m3u"
LOG_FILE = "lista_rbtv_futbol.log"

SITE_BASE = os.getenv("RBTV_SITE_BASE", "https://ganzogyz.78vxlpoorkx2kjecut.cfd")
FOOTBALL_PAGE = f"{SITE_BASE}/football.html"

# Hosts de la API de datos (sin sesion/cuenta). Se prueban en orden.
DATA_HOSTS = [
    "https://apis-data10.tcdru136ovur.ru",
    "https://apis-data-defra10.tcdru136ovur.ru",
]

SPORT_TYPE_FOOTBALL = 1
BS_CODE_MATCH_LIVE = 100

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Encoding": "gzip",
}

REQUEST_TIMEOUT = 15


def configurar_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def obtener_params_firma(session, base_host, code, sport_type):
    """Llama a /api/common/bs y extrae el segmento de firma 'sfver{params}'."""
    url = f"{base_host}/api/common/bs"
    params = {"code": code, "sportType": sport_type, "stream": "true"}
    resp = session.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    msg, _ = blackboxprotobuf.decode_message(resp.content)
    # PBResponse.data (campo "10") -> PBBodySignatureResp.kv (campo "1") -> {key: code, value: params}
    entry = msg["10"]["1"]
    valor = entry["2"]
    return valor.decode() if isinstance(valor, bytes) else valor


def _texto(valor):
    if isinstance(valor, bytes):
        return valor.decode("utf-8", errors="ignore")
    if valor is None:
        return ""
    return str(valor)


def obtener_partidos_futbol(session):
    """Descarga y decodifica la agenda de futbol desde el primer host que responda."""
    ultimo_error = None
    for host in DATA_HOSTS:
        try:
            params = obtener_params_firma(session, host, BS_CODE_MATCH_LIVE, SPORT_TYPE_FOOTBALL)
            url = f"{host}/sfver{params}/api/match/live"
            resp = session.get(
                url,
                params={"sportType": SPORT_TYPE_FOOTBALL, "language": 0, "stream": "true"},
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            msg, _ = blackboxprotobuf.decode_message(resp.content)
            partidos_raw = msg.get("10", {}).get("1", [])
            if isinstance(partidos_raw, dict):
                partidos_raw = [partidos_raw]
            logging.info(f"OK {host}: {len(partidos_raw)} partidos recibidos")
            return partidos_raw
        except Exception as e:
            ultimo_error = e
            logging.warning(f"Fallo con host {host}: {e}")
            time.sleep(1)

    raise RuntimeError(f"No se pudo obtener la agenda de ningun host: {ultimo_error}")


def parsear_partido(item):
    """Extrae los campos utiles de un item de PBDataMatch decodificado."""
    match_id = item.get("1")
    fecha_ms = item.get("3")

    liga_info = item.get("10", {}) or {}
    liga_nombre = _texto(liga_info.get("3", {}).get("2")) or "Otros"
    liga_logo = _texto(liga_info.get("4"))

    contenders = item.get("30", [])
    if isinstance(contenders, dict):
        contenders = [contenders]

    equipos = []
    for c in contenders:
        info = c.get("10")
        if info:
            equipos.append(_texto(info.get("3", {}).get("2")))

    nombre_completo = None
    for c in contenders:
        if "2" in c and "10" not in c:
            nombre_completo = _texto(c.get("2"))
            break

    home = equipos[0] if len(equipos) > 0 else ""
    away = equipos[1] if len(equipos) > 1 else ""
    if not (home and away) and nombre_completo:
        home, _, away = nombre_completo.partition(" vs ")

    logo = None
    for c in contenders:
        info = c.get("10")
        if info and info.get("4"):
            logo = _texto(info.get("4"))
            break
    if not logo:
        logo = liga_logo

    extra = item.get("150", {}) or {}
    match_slug = _texto(extra.get("20"))

    kickoff = None
    if isinstance(fecha_ms, int):
        kickoff = datetime.fromtimestamp(fecha_ms / 1000, tz=timezone.utc).astimezone()

    if not (match_id and home and away):
        return None

    return {
        "match_id": match_id,
        "liga": liga_nombre,
        "home": home,
        "away": away,
        "logo": logo,
        "kickoff": kickoff,
        "slug": match_slug,
    }


def generar_m3u(partidos):
    partidos_validos = [p for p in partidos if p]
    partidos_validos.sort(key=lambda p: (p["liga"], p["kickoff"] or datetime.max.replace(tzinfo=timezone.utc)))

    lineas = ["#EXTM3U"]
    for p in partidos_validos:
        hora = p["kickoff"].strftime("%H:%M") if p["kickoff"] else "--:--"
        titulo = f"[{hora}] {p['liga']} - {p['home']} vs {p['away']}"
        logo = p["logo"] or ""
        # Enlace real por partido no verificable (SPA tras Cloudflare); se enlaza a la pagina de futbol.
        url = f"{FOOTBALL_PAGE}?matchId={p['match_id']}"
        lineas.append(f'#EXTINF:-1 tvg-logo="{logo}" group-title="{p["liga"]}",{titulo}')
        lineas.append(url)

    return "\n".join(lineas) + "\n", len(partidos_validos)


def main():
    configurar_logging()
    session = requests.Session()

    try:
        partidos_raw = obtener_partidos_futbol(session)
    except Exception as e:
        logging.error(f"No se pudo generar la lista: {e}")
        sys.exit(1)

    partidos = [parsear_partido(item) for item in partidos_raw]
    contenido, total = generar_m3u(partidos)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(contenido)

    logging.info(f"Generado {OUTPUT_FILE} con {total} partidos de futbol")


if __name__ == "__main__":
    main()
