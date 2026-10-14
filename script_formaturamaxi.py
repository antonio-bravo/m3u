#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script para generar y verificar la lista M3U de canales de admin2.formaturamaxi.com.br
Ejemplo de origen: https://paco@admin2.formaturamaxi.com.br/tnt.m3u8
"""

import argparse
import logging
import os
import sys
import time

DEFAULT_HOST = "https://admin2.formaturamaxi.com.br"
DEFAULT_USER = "paco"
M3U_FILE = "lista_formaturamaxi.m3u"
LOG_FILE = "lista_formaturamaxi.log"

# Canales verificados y clasificados por categoría
CHANNELS_DATA = [
    # TNT & Variantes
    {
        "id": "tnt",
        "title": "TNT",
        "group": "Formaturas - TNT",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/TNT_Logo_2016.svg/512px-TNT_Logo_2016.svg.png"
    },
    {
        "id": "tnt2",
        "title": "TNT 2",
        "group": "Formaturas - TNT",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/TNT_Logo_2016.svg/512px-TNT_Logo_2016.svg.png"
    },
    {
        "id": "tnt4",
        "title": "TNT 4",
        "group": "Formaturas - TNT",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/TNT_Logo_2016.svg/512px-TNT_Logo_2016.svg.png"
    },
    {
        "id": "tnt5",
        "title": "TNT 5",
        "group": "Formaturas - TNT",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/TNT_Logo_2016.svg/512px-TNT_Logo_2016.svg.png"
    },
    {
        "id": "tntmx",
        "title": "TNT México",
        "group": "Formaturas - TNT",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/TNT_Logo_2016.svg/512px-TNT_Logo_2016.svg.png"
    },
    {
        "id": "canal5",
        "title": "Canal 5 (México)",
        "group": "Formaturas - México",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/65/Canal_5_M%C3%A9xico.png/512px-Canal_5_M%C3%A9xico.png"
    },

    # Deportes México & LatAm
    {
        "id": "tudn",
        "title": "TUDN",
        "group": "Formaturas - Deportes LatAm",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e0/TUDN_logo.svg/512px-TUDN_logo.svg.png"
    },
    {
        "id": "tudnmx",
        "title": "TUDN México",
        "group": "Formaturas - Deportes LatAm",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e0/TUDN_logo.svg/512px-TUDN_logo.svg.png"
    },
    {
        "id": "aztecadeportes",
        "title": "Azteca Deportes",
        "group": "Formaturas - Deportes LatAm",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b5/Azteca_Deportes_logo.svg/512px-Azteca_Deportes_logo.svg.png"
    },
    {
        "id": "clarosports",
        "title": "Claro Sports",
        "group": "Formaturas - Deportes LatAm",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a2/Claro_Sports_logo_2022.svg/512px-Claro_Sports_logo_2022.svg.png"
    },
    {
        "id": "foxmx",
        "title": "Fox Sports México",
        "group": "Formaturas - Deportes LatAm",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c0/Fox_Sports_logo.svg/512px-Fox_Sports_logo.svg.png"
    },
    {
        "id": "vix",
        "title": "ViX",
        "group": "Formaturas - Deportes LatAm",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/77/ViX_logo.svg/512px-ViX_logo.svg.png"
    },
    {
        "id": "vix2",
        "title": "ViX 2",
        "group": "Formaturas - Deportes LatAm",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/77/ViX_logo.svg/512px-ViX_logo.svg.png"
    },
    {
        "id": "vixmx",
        "title": "ViX México",
        "group": "Formaturas - Deportes LatAm",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/77/ViX_logo.svg/512px-ViX_logo.svg.png"
    },
    {
        "id": "bein",
        "title": "beIN Sports",
        "group": "Formaturas - Deportes LatAm",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/07/BeIN_Sports_logo.svg/512px-BeIN_Sports_logo.svg.png"
    },
    {
        "id": "beines",
        "title": "beIN Sports Español",
        "group": "Formaturas - Deportes LatAm",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/07/BeIN_Sports_logo.svg/512px-BeIN_Sports_logo.svg.png"
    },
    {
        "id": "gol",
        "title": "GOL PLAY",
        "group": "Formaturas - Deportes LatAm",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1d/Gol_Play_logo.svg/512px-Gol_Play_logo.svg.png"
    },
    {
        "id": "sportv",
        "title": "SporTV Brasil",
        "group": "Formaturas - Deportes LatAm",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c7/SporTV_2021.svg/512px-SporTV_2021.svg.png"
    },

    # ESPN
    {
        "id": "espn",
        "title": "ESPN",
        "group": "Formaturas - ESPN",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2f/ESPN_wordmark.svg/512px-ESPN_wordmark.svg.png"
    },
    {
        "id": "espnmx",
        "title": "ESPN México",
        "group": "Formaturas - ESPN",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2f/ESPN_wordmark.svg/512px-ESPN_wordmark.svg.png"
    },
    {
        "id": "espn2mx",
        "title": "ESPN 2 México",
        "group": "Formaturas - ESPN",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2f/ESPN_wordmark.svg/512px-ESPN_wordmark.svg.png"
    },
    {
        "id": "espn3mx",
        "title": "ESPN 3 México",
        "group": "Formaturas - ESPN",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2f/ESPN_wordmark.svg/512px-ESPN_wordmark.svg.png"
    },

    # Colombia
    {
        "id": "win",
        "title": "Win Sports",
        "group": "Formaturas - Colombia",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e4/Win_Sports_logo_2020.png/512px-Win_Sports_logo_2020.png"
    },
    {
        "id": "winplus",
        "title": "Win Sports+",
        "group": "Formaturas - Colombia",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/36/Win_Sports%2B_logo_2020.png/512px-Win_Sports%2B_logo_2020.png"
    },
    {
        "id": "rcn",
        "title": "RCN Nuestra Tele",
        "group": "Formaturas - Colombia",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/36/Canal_RCN_logo_2023.svg/512px-Canal_RCN_logo_2023.svg.png"
    },

    # NFL
    {
        "id": "nfl",
        "title": "NFL Game Pass 1",
        "group": "Formaturas - NFL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png"
    },
    {
        "id": "nfl2",
        "title": "NFL Game Pass 2",
        "group": "Formaturas - NFL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png"
    },
    {
        "id": "nfl3",
        "title": "NFL Game Pass 3",
        "group": "Formaturas - NFL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png"
    },
    {
        "id": "nfl4",
        "title": "NFL Game Pass 4",
        "group": "Formaturas - NFL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png"
    },
    {
        "id": "nfl5",
        "title": "NFL Game Pass 5",
        "group": "Formaturas - NFL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png"
    },
    {
        "id": "nfl6",
        "title": "NFL Game Pass 6",
        "group": "Formaturas - NFL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png"
    },
    {
        "id": "nfl7",
        "title": "NFL Game Pass 7",
        "group": "Formaturas - NFL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png"
    },
    {
        "id": "nfl8",
        "title": "NFL Game Pass 8",
        "group": "Formaturas - NFL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png"
    },
    {
        "id": "nfl9",
        "title": "NFL Game Pass 9",
        "group": "Formaturas - NFL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png"
    },
    {
        "id": "nfl10",
        "title": "NFL Game Pass 10",
        "group": "Formaturas - NFL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png"
    },
    {
        "id": "nfl14",
        "title": "NFL Game Pass 14",
        "group": "Formaturas - NFL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a2/National_Football_League_logo.svg/512px-National_Football_League_logo.svg.png"
    },
    {
        "id": "redzone",
        "title": "NFL RedZone",
        "group": "Formaturas - NFL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/b/b3/NFL_RedZone_logo.svg/512px-NFL_RedZone_logo.svg.png"
    },

    # NBA
    {
        "id": "nba",
        "title": "NBA League Pass 1",
        "group": "Formaturas - NBA",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/0/03/National_Basketball_Association_logo.svg/512px-National_Basketball_Association_logo.svg.png"
    },
    {
        "id": "nba2",
        "title": "NBA League Pass 2",
        "group": "Formaturas - NBA",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/0/03/National_Basketball_Association_logo.svg/512px-National_Basketball_Association_logo.svg.png"
    },
    {
        "id": "nba3",
        "title": "NBA League Pass 3",
        "group": "Formaturas - NBA",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/0/03/National_Basketball_Association_logo.svg/512px-National_Basketball_Association_logo.svg.png"
    },
    {
        "id": "nba4",
        "title": "NBA League Pass 4",
        "group": "Formaturas - NBA",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/0/03/National_Basketball_Association_logo.svg/512px-National_Basketball_Association_logo.svg.png"
    },
    {
        "id": "nba5",
        "title": "NBA League Pass 5",
        "group": "Formaturas - NBA",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/0/03/National_Basketball_Association_logo.svg/512px-National_Basketball_Association_logo.svg.png"
    },
    {
        "id": "nba6",
        "title": "NBA League Pass 6",
        "group": "Formaturas - NBA",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/0/03/National_Basketball_Association_logo.svg/512px-National_Basketball_Association_logo.svg.png"
    },

    # NHL
    {
        "id": "nhl",
        "title": "NHL Center Ice 1",
        "group": "Formaturas - NHL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3a/05_NHL_Shield.svg/512px-05_NHL_Shield.svg.png"
    },
    {
        "id": "nhl2",
        "title": "NHL Center Ice 2",
        "group": "Formaturas - NHL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3a/05_NHL_Shield.svg/512px-05_NHL_Shield.svg.png"
    },
    {
        "id": "nhl3",
        "title": "NHL Center Ice 3",
        "group": "Formaturas - NHL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3a/05_NHL_Shield.svg/512px-05_NHL_Shield.svg.png"
    },
    {
        "id": "nhl4",
        "title": "NHL Center Ice 4",
        "group": "Formaturas - NHL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3a/05_NHL_Shield.svg/512px-05_NHL_Shield.svg.png"
    },
    {
        "id": "nhl5",
        "title": "NHL Center Ice 5",
        "group": "Formaturas - NHL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3a/05_NHL_Shield.svg/512px-05_NHL_Shield.svg.png"
    },
    {
        "id": "nhl6",
        "title": "NHL Center Ice 6",
        "group": "Formaturas - NHL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3a/05_NHL_Shield.svg/512px-05_NHL_Shield.svg.png"
    },
    {
        "id": "nhl7",
        "title": "NHL Center Ice 7",
        "group": "Formaturas - NHL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3a/05_NHL_Shield.svg/512px-05_NHL_Shield.svg.png"
    },
    {
        "id": "nhl8",
        "title": "NHL Center Ice 8",
        "group": "Formaturas - NHL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3a/05_NHL_Shield.svg/512px-05_NHL_Shield.svg.png"
    },
    {
        "id": "nhl9",
        "title": "NHL Center Ice 9",
        "group": "Formaturas - NHL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3a/05_NHL_Shield.svg/512px-05_NHL_Shield.svg.png"
    },
    {
        "id": "nhl10",
        "title": "NHL Center Ice 10",
        "group": "Formaturas - NHL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3a/05_NHL_Shield.svg/512px-05_NHL_Shield.svg.png"
    },
    {
        "id": "nhl13",
        "title": "NHL Center Ice 13",
        "group": "Formaturas - NHL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3a/05_NHL_Shield.svg/512px-05_NHL_Shield.svg.png"
    },
    {
        "id": "nhl14",
        "title": "NHL Center Ice 14",
        "group": "Formaturas - NHL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3a/05_NHL_Shield.svg/512px-05_NHL_Shield.svg.png"
    },
    {
        "id": "nhl15",
        "title": "NHL Center Ice 15",
        "group": "Formaturas - NHL",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/3/3a/05_NHL_Shield.svg/512px-05_NHL_Shield.svg.png"
    },

    # MLB
    {
        "id": "mlb",
        "title": "MLB Extra Innings 1",
        "group": "Formaturas - MLB",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Major_League_Baseball_logo.svg/512px-Major_League_Baseball_logo.svg.png"
    },
    {
        "id": "mlb2",
        "title": "MLB Extra Innings 2",
        "group": "Formaturas - MLB",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Major_League_Baseball_logo.svg/512px-Major_League_Baseball_logo.svg.png"
    },
    {
        "id": "mlb4",
        "title": "MLB Extra Innings 4",
        "group": "Formaturas - MLB",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Major_League_Baseball_logo.svg/512px-Major_League_Baseball_logo.svg.png"
    },

    # Motor & F1
    {
        "id": "f1",
        "title": "Formula 1 Main",
        "group": "Formaturas - Motor",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/F1.svg/512px-F1.svg.png"
    },
    {
        "id": "f1tv",
        "title": "F1 TV Live",
        "group": "Formaturas - Motor",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/F1.svg/512px-F1.svg.png"
    },

    # Lucha Libre / Fighting
    {
        "id": "wwe",
        "title": "WWE Network 1",
        "group": "Formaturas - Fighting",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/03/WWE_Logo.svg/512px-WWE_Logo.svg.png"
    },
    {
        "id": "wwe2",
        "title": "WWE Network 2",
        "group": "Formaturas - Fighting",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/03/WWE_Logo.svg/512px-WWE_Logo.svg.png"
    },
    {
        "id": "aew",
        "title": "AEW Wrestling",
        "group": "Formaturas - Fighting",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3f/All_Elite_Wrestling_logo.svg/512px-All_Elite_Wrestling_logo.svg.png"
    },

    # Fútbol & MLS
    {
        "id": "mls",
        "title": "MLS Season Pass",
        "group": "Formaturas - Fútbol & MLS",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/76/MLS_crest_logo_RGB_gradient.svg/512px-MLS_crest_logo_RGB_gradient.svg.png"
    },
    {
        "id": "premier2",
        "title": "Premier League 2",
        "group": "Formaturas - Fútbol & MLS",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/f/f2/Premier_League_Logo.svg/512px-Premier_League_Logo.svg.png"
    },

    # Eventos & PPV
    {
        "id": "ppv",
        "title": "PPV Eventos 1",
        "group": "Formaturas - Eventos & PPV",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/91/Television_icon.svg/512px-Television_icon.svg.png"
    },
    {
        "id": "ppv2",
        "title": "PPV Eventos 2",
        "group": "Formaturas - Eventos & PPV",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/91/Television_icon.svg/512px-Television_icon.svg.png"
    },
    {
        "id": "main",
        "title": "Main Event Feed",
        "group": "Formaturas - Eventos & PPV",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/91/Television_icon.svg/512px-Television_icon.svg.png"
    }
]

def build_stream_url(host: str, channel_id: str, user: str = None) -> str:
    """Construye la URL con formato de usuario HTTP opcional (ej: https://paco@admin2...)."""
    if user:
        if "://" in host:
            proto, domain = host.split("://", 1)
            base_url = f"{proto}://{user}@{domain}"
        else:
            base_url = f"https://{user}@{host}"
    else:
        base_url = host

    return f"{base_url}/{channel_id}.m3u8"

def generate_m3u(filepath: str, host: str = DEFAULT_HOST, user: str = DEFAULT_USER, channels=None):
    """Escribe la lista en formato M3U estándar compatible con reproductores IPTV."""
    if channels is None:
        channels = CHANNELS_DATA

    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# Formatura Maxi - Actualizado: {now_str}\n")
        f.write(f"# Fuente: {host}\n\n")
        for ch in channels:
            cid = ch["id"]
            title = ch.get("title", cid)
            group = ch.get("group", "Formaturas")
            logo = ch.get("logo", "")
            url = build_stream_url(host, cid, user)

            f.write(f'#EXTINF:-1 tvg-id="{cid}" tvg-name="{title}" tvg-logo="{logo}" group-title="{group}",{title}\n')
            f.write(f"{url}\n\n")

    logging.info(f"Lista M3U generada con éxito en '{filepath}' con {len(channels)} canales.")

def verify_channels_with_selenium(host: str = DEFAULT_HOST):
    """Verifica la disponibilidad en vivo de cada canal usando Selenium con Chrome headless."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    opts.add_argument("--headless=new")
    driver = webdriver.Chrome(options=opts)
    driver.set_script_timeout(60)

    try:
        logging.info("Conectando con el servidor base...")
        driver.get(host)
        time.sleep(1)

        names = [ch["id"] for ch in CHANNELS_DATA]
        script = """
        const names = arguments[0];
        const done = arguments[1];
        const results = {};
        Promise.all(names.map(async (name) => {
            try {
                const url = '%s/' + name + '.m3u8';
                const res = await fetch(url);
                if (res.status === 200) {
                    const text = await res.text();
                    results[name] = text.includes('#EXTM3U');
                } else {
                    results[name] = false;
                }
            } catch(e) {
                results[name] = false;
            }
        })).then(() => done(results));
        """ % host

        active_map = driver.execute_async_script(script, names)
        active_channels = [ch for ch in CHANNELS_DATA if active_map.get(ch["id"], False)]
        logging.info(f"Verificación completada: {len(active_channels)}/{len(CHANNELS_DATA)} canales activos.")
        return active_channels
    finally:
        driver.quit()

def main():
    parser = argparse.ArgumentParser(description="Generador de lista M3U para admin2.formaturamaxi.com.br")
    parser.add_argument("--output", default=M3U_FILE, help="Ruta del archivo M3U de salida")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Servidor base")
    parser.add_argument("--user", default=DEFAULT_USER, help="Usuario opcional en la URL (paco)")
    parser.add_argument("--no-user", action="store_true", help="No incluir usuario en la URL")
    parser.add_argument("--verify", action="store_true", help="Verificar canales en vivo antes de escribir el archivo")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )

    user = None if args.no_user else args.user

    if args.verify:
        channels = verify_channels_with_selenium(args.host)
    else:
        channels = CHANNELS_DATA

    generate_m3u(args.output, host=args.host, user=user, channels=channels)

if __name__ == "__main__":
    main()
