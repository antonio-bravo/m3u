#!/usr/bin/env python3
"""
Escáner y Generador de Listas M3U para CDN skibid.im / hayuhi.online
Escanea canales activos y genera lista_skibid.m3u con los streams funcionales.
"""

import urllib.request
import concurrent.futures
import sys

DEFAULT_TOKEN = (
    "eyJjaCI6ImZ1dDIiLCJlZGdlIjoicm9ibG94c3RhY2hhdG9ubGluZSIsImV4cCI6MTc5Mjk3OTI0OSwic2lkIjoiMzU2M2E5YjUtMTYxYS00Nzc2LTgxOWEtOGY4ZWMzZGM2MDdjIn0"
    ".gwnWlFH_5uzoZXsA6bsr6vdiyjaLALJUdK_zStHJr1s"
)

EDGES = [
    "robloxstachatonline.skibid.im",
    "barmaley.hayuhi.online",
    "barabulka.hayuhi.online",
    "kolebas.hayuhi.online",
    "konridskal.hayuhi.online",
    "ozinkovka.hayuhi.online",
    "ponosenko.hayuhi.online",
    "prohor.hayuhi.online",
    "roberto.hayuhi.online",
    "shurinvova.hayuhi.online",
    "trifopanasoma.hayuhi.online"
]

CANDIDATE_CHANNELS = [
    # Fútbol numerado
    *[f"fut{i}" for i in range(1, 30)],
    *[f"futbol{i}" for i in range(1, 20)],
    # Canales genéricos
    *[f"channel{i}" for i in range(1, 20)],
    *[f"canal{i}" for i in range(1, 20)],
    *[f"ch{i}" for i in range(1, 20)],
    # Deportes específicos
    "laliga", "laliga1", "laliga2", "m_laliga", "dazn", "dazn1", "dazn2", "daznf1",
    "movistar", "mplus", "campeones", "espn", "espn1", "espn2", "espn3",
    "foxsports", "dsports", "tntsports", "gol", "golplay", "tdp"
]

def check_stream(edge: str, ch: str, token: str):
    url = f"https://{edge}/hls/{ch}/playlist.m3u8?token={token}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            if resp.status == 200:
                body = resp.read().decode("utf-8", errors="ignore")
                fps = "25fps"
                if "FRAME-RATE=60" in body:
                    fps = "60fps"
                return ch, edge, fps
    except Exception:
        pass
    return None

def main():
    print("🔍 Escaneando canales activos en robloxstachatonline.skibid.im...")
    active_channels = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
        futures = [
            executor.submit(check_stream, EDGES[0], ch, DEFAULT_TOKEN)
            for ch in CANDIDATE_CHANNELS
        ]
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            if res:
                ch, edge, fps = res
                active_channels[ch] = fps
                print(f"  ✅ Encontrado: {ch} ({fps})")

    if not active_channels:
        print("⚠️ No se encontraron canales activos.")
        return

    # Generar M3U
    m3u_lines = ['#EXTM3U url-tvg=""']
    
    for ch, fps in sorted(active_channels.items()):
        name = ch.upper()
        if ch.startswith("fut"):
            num = ch.replace("fut", "")
            title = f"Fútbol {num} HD (720p {fps})"
            logo = "https://i.imgur.com/K3Vw8Vn.png"
            group = "Deportes Directo"
        elif ch.startswith("channel"):
            num = ch.replace("channel", "")
            title = f"Sports Channel {num} HD (720p {fps})"
            logo = "https://i.imgur.com/K3Vw8Vn.png"
            group = "Deportes Directo"
        else:
            title = f"{name} HD (720p {fps})"
            logo = "https://i.imgur.com/K3Vw8Vn.png"
            group = "Deportes Directo"

        m3u_lines.append(f'#EXTINF:-1 tvg-id="{name}" tvg-name="{title}" tvg-logo="{logo}" group-title="{group}",{title}')
        m3u_lines.append(f"https://{EDGES[0]}/hls/{ch}/playlist.m3u8\n")

    # Servidores espejo de respaldo
    m3u_lines.append("# --- Servidores Espejo de Respaldo (CDN Hayuhi) ---")
    for ch, fps in sorted(active_channels.items()):
        name = f"{ch.upper()}_MIRROR"
        title = f"{ch.upper()} HD [Mirror Barmaley]"
        m3u_lines.append(f'#EXTINF:-1 tvg-id="{name}" tvg-name="{title}" group-title="Deportes Espejos",{title}')
        m3u_lines.append(f"https://{EDGES[1]}/hls/{ch}/playlist.m3u8\n")

    output_path = "lista_skibid.m3u"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(m3u_lines))

    print(f"\n🎉 Lista guardada con éxito en: {output_path} ({len(active_channels)} canales)")

if __name__ == "__main__":
    main()
