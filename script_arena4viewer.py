import os
import re
from datetime import datetime
from bs4 import BeautifulSoup
import requests

# Archivos de salida
OUTPUT_FILE = "area4viewer.m3u"
LOG_FILE = "arena4viewer.log"

# Servidores espejo para consultar las agendas y los canales
ARENA_URLS = [
    "http://arena4viewer.in",
    "http://arena4viewer.pl",
    "https://arena4viewer.co.in",
    "https://arena4viewer.lv",
]

# Cabeceras exactas simulando la App Oficial Arena4Viewer
HEADERS = {
    "User-Agent": "Arena4Viewer/6.8.2",
    "X-Requested-With": "com.bone.android.a4v.oficial",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Cookie": "beget=begetok",
}


def escribir_log(mensaje):
    """Escribe un mensaje en el archivo de log con la fecha y hora actual."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {mensaje}\n")


def extraer_hash_de_texto(texto):
    """Busca el valor 'TV:hash_32_caracteres' o un hash Acestream clásico de 40 caracteres."""
    match_tv = re.search(r"TV:([a-fA-F0-9]{32})", texto)
    if match_tv:
        return match_tv.group(1)

    match_ace = re.search(r"([a-fA-F0-9]{40})", texto)
    if match_ace:
        return match_ace.group(1)
    return None


def obtener_canales_activos_de_la_agenda():
    """Analiza la tabla de partidos de hoy y devuelve solo los números de canal emisores."""
    canales_activos = set()

    for base_url in ARENA_URLS:
        url_guia = f"{base_url}/misguia2.php"
        HEADERS["Referer"] = f"{base_url}/"
        try:
            print(f"Leyendo agenda de partidos desde {url_guia}...")
            resp = requests.get(url_guia, headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                escribir_log(
                    f"ERROR AGENDA: {url_guia} devolvió status {resp.status_code}"
                )
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            tds = soup.find_all("td", class_="auto-style3")

            for td in tds:
                texto = td.get_text()
                if "[" in texto and "/" not in texto and ":" not in texto:
                    numeros = re.findall(r"\d+", texto)
                    for num in numeros:
                        canales_activos.add(int(num))

            if canales_activos:
                lista_ordenada = sorted(list(canales_activos))
                escribir_log(
                    f"OK AGENDA: {len(lista_ordenada)} canales detectados desde {base_url}. Canales: {lista_ordenada}"
                )
                return lista_ordenada
        except Exception as e:
            escribir_log(f"CRITICAL AGENDA: Error conectando a {url_guia}: {e}")
            continue

    return []


def escanear_id_de_canal(num_canal):
    """Entra al enlace de cada canal detectado y extrae su ID real actual."""
    canal_str = f"{num_canal:02d}"

    for base_url in ARENA_URLS:
        url_canal = f"{base_url}/{canal_str}"
        HEADERS["Referer"] = f"{base_url}/"
        try:
            resp = requests.get(url_canal, headers=HEADERS, timeout=6)
            if resp.status_code == 200:
                aid = extraer_hash_de_texto(resp.text)
                if aid:
                    return aid, "OK", base_url
                else:
                    return None, "HASH_NOT_FOUND", base_url
            else:
                escribir_log(
                    f"CANAL {canal_str}: Espejo {base_url} devolvió HTTP {resp.status_code}"
                )
        except Exception as e:
            # Continúa silenciosamente al siguiente espejo si este falla
            continue

    return None, "ALL_MIRRORS_FAILED", None


def generar_m3u():
    # Inicializar o limpiar el archivo de log para la nueva ejecución
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(
            f"=== INICIO DE EXTRACCIÓN ARENA4VIEWER - {datetime.now()} ===\n"
        )

    canales_a_buscar = obtener_canales_activos_de_la_agenda()

    if not canales_a_buscar:
        escribir_log(
            "AVISO: No se detectó la tabla de la agenda. Cargando canales 1 al 30 por defecto."
        )
        print(
            "[¡!] No se detectó la tabla. Iniciando escaneo básico de canales 1 al 30 por defecto..."
        )
        canales_a_buscar = list(range(1, 31))

    channels_list = []

    print("\nIniciando extracción de IDs de Acestream individuales...")
    for num in canales_a_buscar:
        canal_str = f"{num:02d}"
        print(f"Buscando ID de Canal {canal_str}...", end="\r")

        aid, estado, espejo_exitoso = escanear_id_de_canal(num)

        if estado == "OK":
            print(f"[OK] ArenaVision {canal_str} -> Registrado con éxito.")
            escribir_log(
                f"CANAL {canal_str}: ID extraído correctamente ({aid}) desde {espejo_exitoso}"
            )
            channels_list.append(
                {
                    "name": f"ArenaVision {canal_str}",
                    "url": f"http://127.0.0{aid}",
                }
            )
        elif estado == "HASH_NOT_FOUND":
            print(f"[FALTA] Canal ArenaVision {canal_str} no tiene ID activo.")
            escribir_log(
                f"CANAL {canal_str}: Web accesible en {espejo_exitoso} pero no se encontró la etiqueta 'TV:' o el hash."
            )
        else:
            print(f"[ERROR] Canal ArenaVision {canal_str} inaccesible.")
            escribir_log(
                f"CANAL {canal_str}: Fallaron todos los servidores espejo configurados."
            )

    # Construcción del archivo .m3u final
    if channels_list:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in channels_list:
                f.write(
                    f'#EXTINF:-1 tvg-id="{ch["name"]}" tvg-name="{ch["name"]}" tvg-logo="https://githubusercontent.com",{ch["name"]}\n'
                )
                f.write(f"{ch['url']}\n")
        print(
            f"\n¡Completado! Archivo '{OUTPUT_FILE}' generado con {len(channels_list)} canales activos."
        )
        escribir_log(
            f"FIN CON ÉXITO: M3U creado con {len(channels_list)} canales mapeados."
        )
    else:
        print("\nNo se pudo recuperar ningún ID de Acestream válido.")
        escribir_log(
            "FIN CON ERRORES: El archivo M3U no se creó porque no se capturó ningún ID."
        )


if __name__ == "__main__":
    generar_m3u()
