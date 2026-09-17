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
import re
import subprocess
from urllib.parse import urlparse
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import blackboxprotobuf

# ==================== CONFIGURACION ====================
OUTPUT_FILE = "lista_rbtv_futbol.m3u"
LOG_FILE = "lista_rbtv_futbol.log"

SPORT_TYPE_FOOTBALL = 1
BS_CODE_MATCH_LIVE = 100

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Encoding": "gzip",
}

REQUEST_TIMEOUT = 10

# Regex precompiladas para optimización
RE_URL_DOMINIO = re.compile(r'https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
RE_META_URL = re.compile(r'content=["\'][^"\']*url=([^"\']+)["\']', re.IGNORECASE)
RE_JS_URL = re.compile(r'window\.location(?:\.href)?\s*=\s*["\'](https?://[^"\']+)["\']', re.IGNORECASE)
RE_JS_TAG = re.compile(r'(?:src|href)=["\']([^"\']+\.js)["\']', re.IGNORECASE)
RE_APIS_DATA = re.compile(r'apis-data[a-zA-Z0-9.-]*\.[a-zA-Z]{2,}', re.IGNORECASE)
RE_ROUTE_PATTERN = re.compile(r'[\"\'\`]/[^\'\"\`]*:matchId[^\'\"\`]*\.html[\"\'\`]', re.IGNORECASE)


def crear_session_optimizada():
    """Crea una sesión de requests optimizada con retries y connection pooling."""
    session = requests.Session()
    retry_strategy = Retry(
        total=2,
        backoff_factor=0.3,
        status_forcelist=[500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=15, pool_maxsize=15)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _obtener_remote_m3u_url():
    """Calcula dinámicamente la URL remota del archivo M3U en GitHub leyendo la configuración git local."""
    try:
        res = subprocess.run(["git", "config", "--get", "remote.origin.url"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout.strip():
            git_url = res.stdout.strip()
            clean_repo = re.sub(r'https?://([^@]+@)?github\.com/', '', git_url).replace('git@github.com:', '').replace('.git', '')
            if clean_repo:
                return f"https://raw.githubusercontent.com/{clean_repo}/main/lista_rbtv_futbol.m3u"
    except Exception:
        pass
    return "https://raw.githubusercontent.com/antonio-bravo/m3u/main/lista_rbtv_futbol.m3u"


def _extraer_dominios_de_texto(texto):
    """Extrae URLs de dominios web relevantes desde un contenido de texto."""
    dominios = []
    matches = RE_URL_DOMINIO.findall(texto)
    for m in matches:
        parsed = urlparse(m)
        if parsed.scheme and parsed.netloc:
            netloc = parsed.netloc.lower()
            if not any(x in netloc for x in ["github", "logos", "schema", "w3.org", "tcdru136ovur", "tcllu137fien"]):
                base = f"{parsed.scheme}://{parsed.netloc}"
                if base not in dominios:
                    dominios.append(base)
    return dominios


def obtener_seed_domains(session):
    """Genera dinámicamente la lista de dominios semilla a probar sin depender de valores estáticos."""
    seeds = []

    # 1. Variable de entorno RBTV_SEED_DOMAINS (separadas por coma)
    env_seeds = os.getenv("RBTV_SEED_DOMAINS")
    if env_seeds:
        for s in env_seeds.split(","):
            s = s.strip().rstrip("/")
            if s and s not in seeds:
                seeds.append(s)

    # 2. Extraer dominios de la lista M3U local (si existe)
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                for d in _extraer_dominios_de_texto(f.read()):
                    if d not in seeds:
                        seeds.append(d)
        except Exception as e:
            logging.debug(f"No se pudo extraer semillas de {OUTPUT_FILE}: {e}")

    # 3. Extraer dominios del log local (si existe)
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                for d in _extraer_dominios_de_texto(f.read()):
                    if d not in seeds:
                        seeds.append(d)
        except Exception as e:
            logging.debug(f"No se pudo extraer semillas de {LOG_FILE}: {e}")

    # 4. Extraer dominios desde el repositorio remoto en GitHub (URL calculada dinámicamente)
    remote_m3u_url = _obtener_remote_m3u_url()
    try:
        resp = session.get(remote_m3u_url, headers=HEADERS, timeout=5)
        if resp.status_code == 200:
            for d in _extraer_dominios_de_texto(resp.text):
                if d not in seeds:
                    seeds.append(d)
    except Exception as e:
        logging.debug(f"No se pudo consultar semillas remotas desde {remote_m3u_url}: {e}")

    # 5. Portales principales / mirrors de reserva
    portales_reserva = [
        "http://rbtv77.com",
        "http://superabbit77.com",
        "https://superabbit77.com",
    ]
    for p in portales_reserva:
        if p not in seeds:
            seeds.append(p)

    logging.info(f"Semillas descubiertas dinamicamente ({len(seeds)}): {seeds}")
    return seeds


def _probar_semilla(session, seed):
    """Petición individual de semilla para evaluación concurrente con scoring."""
    try:
        resp = session.get(seed, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if resp.status_code == 200:
            final_url = resp.url
            meta_match = RE_META_URL.search(resp.text)
            js_match = RE_JS_URL.search(resp.text)
            if meta_match:
                final_url = meta_match.group(1)
            elif js_match:
                final_url = js_match.group(1)

            parsed = urlparse(final_url)
            if parsed.scheme and parsed.netloc:
                base = f"{parsed.scheme}://{parsed.netloc}"
                is_redirected = len(resp.history) > 0 or parsed.netloc.lower() not in seed.lower()
                has_football = "football" in resp.text.lower() or "football" in final_url.lower()
                score = (2 if is_redirected else 0) + (1 if has_football else 0)
                logging.debug(f"Semilla '{seed}' resuelta en '{base}' (score {score})")
                return (score, base, seed)
    except Exception as e:
        logging.debug(f"Fallo al resolver SITE_BASE con semilla {seed}: {e}")
    return (-1, None, seed)


def obtener_site_base(session):
    """Detecta dinámicamente el dominio base activo evaluando semillas en paralelo con priorización."""
    env_base = os.getenv("RBTV_SITE_BASE")
    if env_base:
        base = env_base.rstrip("/")
        logging.info(f"Usando SITE_BASE desde variable de entorno: {base}")
        return base

    seed_domains = obtener_seed_domains(session)

    # Evaluación concurrente en paralelo de las semillas
    with ThreadPoolExecutor(max_workers=min(len(seed_domains), 8)) as executor:
        results = list(executor.map(lambda s: _probar_semilla(session, s), seed_domains))

    validos = [r for r in results if r[1] is not None]
    if validos:
        validos.sort(key=lambda x: x[0], reverse=True)
        best_score, best_base, best_seed = validos[0]
        logging.info(f"SITE_BASE resuelto dinamicamente (score {best_score}) desde semilla '{best_seed}': {best_base}")
        return best_base

    if seed_domains:
        fallback = seed_domains[0]
        logging.warning(f"No se pudo resolver redireccion directa; usando primera semilla descubierta: {fallback}")
        return fallback

    raise RuntimeError("No se pudo descubrir ninguna semilla ni resolver SITE_BASE dinamicamente.")


def obtener_informacion_sitio_dinamica(session, site_base):
    """Inspecciona los bundles JavaScript de SITE_BASE para extraer DATA_HOSTS y la plantilla de ruta MATCH_ROUTE_PATTERN."""
    env_hosts = os.getenv("RBTV_DATA_HOSTS")
    hosts = [h.strip().rstrip("/") for h in env_hosts.split(",") if h.strip()] if env_hosts else []
    route_pattern = None

    try:
        resp = session.get(site_base, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            js_urls = RE_JS_TAG.findall(resp.text)
            for js_url in js_urls[:15]:
                try:
                    js_resp = session.get(js_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                    if js_resp.status_code == 200:
                        # 1. Extraer DATA_HOSTS
                        if not env_hosts:
                            matches_api = RE_APIS_DATA.findall(js_resp.text)
                            for m in matches_api:
                                parsed = urlparse(m if m.startswith("http") else f"https://{m}")
                                host_base = f"{parsed.scheme}://{parsed.netloc}"
                                if host_base not in hosts:
                                    hosts.append(host_base)

                        # 2. Extraer plantilla de ruta de partidos (MATCH_ROUTE_PATTERN)
                        if not route_pattern:
                            matches_route = RE_ROUTE_PATTERN.findall(js_resp.text)
                            for r_match in matches_route:
                                pattern = r_match.strip("\"'`")
                                if ":leagueLink" in pattern or "football" in pattern or ":sportTypeStr" in pattern:
                                    route_pattern = pattern
                                    logging.info(f"Plantilla de ruta de partidos descubierta dinamicamente desde JS: '{route_pattern}'")
                                    break
                except Exception:
                    pass
    except Exception as e:
        logging.warning(f"Fallo al extraer información dinamica desde {site_base}: {e}")

    if not hosts:
        parsed_base = urlparse(site_base)
        domain_parts = parsed_base.netloc.split(".")
        parent_domain = ".".join(domain_parts[-2:]) if len(domain_parts) >= 2 else parsed_base.netloc
        hosts = [
            f"https://apis-data10.{parent_domain}",
            f"https://apis-data-defra10.{parent_domain}",
            "https://apis-data10.tcllu137fien.ru",
            "https://apis-data-defra10.tcllu137fien.ru",
        ]
        logging.info(f"Usando DATA_HOSTS de reserva: {hosts}")
    else:
        logging.info(f"DATA_HOSTS extraidos dinamicamente desde JS ({len(hosts)}): {hosts}")

    return hosts, route_pattern


def construir_url_partido_dinamica(site_base, route_pattern, match_id, league_slug, match_slug, kickoff):
    """Construye la URL del partido interpretando dinámicamente la plantilla descubierta del JavaScript de la web."""
    mmyyyy = kickoff.strftime("%m%Y") if kickoff else datetime.now(timezone.utc).strftime("%m%Y")
    football_page = f"{site_base}/football.html"

    if route_pattern:
        url_path = re.sub(r'\([^\)]+\)', '', route_pattern)
        url_path = url_path.replace(":sportTypeStr", "football")

        if league_slug:
            url_path = url_path.replace(":leagueLink", league_slug)
        else:
            url_path = url_path.replace(":leagueLink", "")

        url_path = url_path.replace(":matchId", str(match_id))

        if match_slug:
            url_path = url_path.replace(":teamLink", match_slug)
        else:
            url_path = url_path.replace(":teamLink", "")

        url_path = url_path.replace(":matchDateMMYYYY", mmyyyy)

        # Limpiar marcadores no sustituidos y guiones/barras duplicadas
        url_path = re.sub(r':\w+', '', url_path)
        url_path = re.sub(r'-+', '-', url_path).replace("/-", "/").replace("-.html", ".html").replace("/.html", ".html")

        return f"{site_base}{url_path}?icg=RVM&ilang=en"

    # Fallback si no hay patrón descubierto de los bundles JS
    if league_slug and match_slug:
        return f"{site_base}/football/{league_slug}-match-{match_id}/{match_slug}-{mmyyyy}.html?icg=RVM&ilang=en"
    elif match_slug:
        return f"{site_base}/football/match-{match_id}/{match_slug}-{mmyyyy}.html?icg=RVM&ilang=en"
    else:
        return f"{football_page}?matchId={match_id}"


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


def obtener_partidos_futbol(session, data_hosts):
    """Descarga y decodifica la agenda de futbol probando los DATA_HOSTS dinamicos."""
    ultimo_error = None
    for host in data_hosts:
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
            time.sleep(0.5)

    raise RuntimeError(f"No se pudo obtener la agenda de ningun host: {ultimo_error}")


def parsear_partido(item, site_base, route_pattern=None):
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
    league_slug = _texto(extra.get("21"))

    kickoff = None
    if isinstance(fecha_ms, int):
        kickoff = datetime.fromtimestamp(fecha_ms / 1000, tz=timezone.utc).astimezone()

    if not (match_id and home and away):
        return None

    url = construir_url_partido_dinamica(site_base, route_pattern, match_id, league_slug, match_slug, kickoff)

    return {
        "match_id": match_id,
        "liga": liga_nombre,
        "home": home,
        "away": away,
        "logo": logo,
        "kickoff": kickoff,
        "slug": match_slug,
        "league_slug": league_slug,
        "url": url,
    }


def generar_m3u(partidos, site_base):
    partidos_validos = [p for p in partidos if p]
    partidos_validos.sort(key=lambda p: (p["liga"], p["kickoff"] or datetime.max.replace(tzinfo=timezone.utc)))
    football_page = f"{site_base}/football.html"

    lineas = ["#EXTM3U"]
    for p in partidos_validos:
        hora = p["kickoff"].strftime("%H:%M") if p["kickoff"] else "--:--"
        titulo = f"[{hora}] {p['liga']} - {p['home']} vs {p['away']}"
        logo = p["logo"] or ""
        url = p.get("url") or f"{football_page}?matchId={p['match_id']}"
        lineas.append(f'#EXTINF:-1 tvg-logo="{logo}" group-title="{p["liga"]}",{titulo}')
        lineas.append(url)

    return "\n".join(lineas) + "\n", len(partidos_validos)


def main():
    configurar_logging()
    session = crear_session_optimizada()

    site_base = obtener_site_base(session)
    data_hosts, route_pattern = obtener_informacion_sitio_dinamica(session, site_base)

    try:
        partidos_raw = obtener_partidos_futbol(session, data_hosts)
    except Exception as e:
        logging.error(f"No se pudo generar la lista: {e}")
        sys.exit(1)

    partidos = [parsear_partido(item, site_base=site_base, route_pattern=route_pattern) for item in partidos_raw]
    contenido, total = generar_m3u(partidos, site_base=site_base)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(contenido)

    logging.info(f"Generado {OUTPUT_FILE} con {total} partidos de futbol (SITE_BASE: {site_base})")


if __name__ == "__main__":
    main()
