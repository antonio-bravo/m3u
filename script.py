import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from difflib import get_close_matches
import asyncio
from playwright.async_api import async_playwright
import time
try:
    import aiohttp
except ImportError:
    aiohttp = None

def obtener_url_diaria():
    """Retorna la URL base de Platinsport"""
    return "https://www.platinsport.com"

def extraer_eventos_con_requests(url):
    """Fallback: Extrae eventos usando requests en lugar de Selenium"""
    print("Usando fallback con requests...")
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    eventos = []
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"Error al acceder a {url}: {response.status_code}")
            return eventos
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Find all acestream links directly
        acestream_links = soup.find_all('a', href=re.compile(r'acestream://'))
        print(f"Se encontraron {len(acestream_links)} links de acestream")
        
        if not acestream_links:
            print("No se encontraron links de acestream en la página")
            return eventos
        
        # Try to extract events from the HTML
        # Look for time elements and match with acestream links
        times = soup.find_all('time')
        
        for acestream_link in acestream_links[:10]:  # Limitar a 10 primeros
            try:
                href = acestream_link.get('href', '')
                canal_text = acestream_link.get_text(strip=True)
                
                # Try to find nearby time element
                hora_evento = None
                parent = acestream_link.find_parent()
                while parent:
                    time_elem = parent.find('time')
                    if time_elem:
                        time_val = time_elem.get('datetime', '')
                        try:
                            hora_evento = datetime.fromisoformat(time_val.replace("Z", "")).time()
                            break
                        except:
                            pass
                    parent = parent.find_parent()
                
                if not hora_evento:
                    hora_evento = datetime.strptime("23:59", "%H:%M").time()
                
                hora_evento = convertir_a_utc_mas_1(hora_evento)
                
                eventos.append({
                    "hora": hora_evento,
                    "nombre": f"Evento de Platinsport",
                    "canal": canal_text if canal_text else "Canal Desconocido",
                    "url": href
                })
            except Exception as e:
                print(f"Error procesando acestream link: {e}")
                continue
        
        print(f"Se extrajeron {len(eventos)} eventos con requests")
        return eventos
    except Exception as e:
        print(f"Error en extraer_eventos_con_requests: {e}")
        return eventos

def extraer_eventos(url):
    """Extrae eventos usando múltiples fuentes como fallback"""
    print(f"Intentando extraer eventos de Platinsport...")
    
    # Primero intentar con Playwright (Platinsport)
    eventos = extraer_eventos_playwright(url)
    
    if not eventos:
        print("Platinsport falló, intentando PlayTorrio API...")
        eventos = extraer_eventos_playtorrio()
    
    if not eventos:
        print("PlayTorrio falló, intentando DEPORTE-LIBRE...")
        eventos = extraer_eventos_deporte_libre()
    
    if not eventos:
        print("DEPORTE-LIBRE falló, intentando Acestream API...")
        eventos = extraer_eventos_acestream_api()
    
    if not eventos:
        print("Todas las fuentes fallaron, generando lista vacía")
    
    return eventos

def extraer_eventos_playwright(url):
    """Extrae eventos de Platinsport usando Playwright"""
    print(f"Cargando URL con Playwright: {url}")
    
    async def scrape():
        eventos = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            try:
                print("Navegando a la página...")
                await page.goto(url, wait_until="networkidle")
                print("Página cargada")
                
                # Accept cookies
                try:
                    await page.locator("button:has-text('I ACCEPT')").click(timeout=3000)
                    print("Cookies aceptadas")
                    await page.wait_for_timeout(1000)
                except:
                    print("No se pudieron aceptar cookies")
                
                # Get all time elements and PLAY buttons
                times = await page.locator("time").all()
                plays = await page.locator("a:has-text('PLAY')").all()
                
                print(f"Elementos <time> encontrados: {len(times)}")
                print(f"Botones PLAY encontrados: {len(plays)}")
                
                # Get time values
                time_values = []
                for time_elem in times:
                    datetime_attr = await time_elem.get_attribute("datetime")
                    if datetime_attr:
                        time_values.append(datetime_attr)
                
                # Helper function to generate key for go() function
                def generate_key():
                    from base64 import b64encode
                    from datetime import datetime
                    today = datetime.now().isoformat()[:10]
                    return b64encode((today + "PLATINSPORT").encode()).decode()
                
                # Process first few PLAY buttons
                max_events = min(20, len(plays))
                print(f"Procesando primeros {max_events} eventos...")
                
                for idx in range(max_events):
                    try:
                        time_str = time_values[idx] if idx < len(time_values) else ""
                        
                        # Parse time
                        try:
                            hora_evento = datetime.fromisoformat(time_str.replace("Z", "")).time()
                        except:
                            hora_evento = datetime.strptime("23:59", "%H:%M").time()
                        
                        hora_evento = convertir_a_utc_mas_1(hora_evento)
                        
                        # Get PLAY button href
                        play_btn = plays[idx]
                        href = await play_btn.get_attribute("href")
                        
                        # Determine target URL
                        target_url = None
                        
                        if href and href.startswith("javascript:go("):
                            file_start = href.find("('") + 2
                            file_end = href.find("')", file_start)
                            if file_start > 1 and file_end > file_start:
                                file_param = href[file_start:file_end]
                                key = generate_key()
                                target_url = f"https://www.platinsport.com/link/{file_param}?key={key}"
                                print(f"\n  Evento {idx+1}: {hora_evento}")
                                print(f"    → Generando URL: /link/{file_param}?key=...")
                        elif href and href.startswith("http"):
                            target_url = href
                            print(f"\n  Evento {idx+1}: {hora_evento}")
                            print(f"    → URL: {href[:60]}")
                        else:
                            continue
                        
                        # Navigate to target URL
                        if target_url:
                            popup = await browser.new_page()
                            try:
                                await popup.goto(target_url, wait_until="networkidle", timeout=15000)
                                await popup.wait_for_timeout(2000)
                                
                                popup_html = await popup.content()
                                popup_soup = BeautifulSoup(popup_html, "html.parser")
                                
                                # Find acestream links
                                acestream_links = popup_soup.find_all('a', href=lambda x: x and 'acestream://' in x)
                                print(f"    - Acestream links: {len(acestream_links)}")
                                
                                for link in acestream_links:
                                    canal_text = link.get_text(strip=True)
                                    url_acestream = link.get('href', '')
                                    
                                    if url_acestream:
                                        eventos.append({
                                            "hora": hora_evento,
                                            "nombre": f"Evento de Platinsport",
                                            "canal": canal_text if canal_text else "Canal",
                                            "url": url_acestream
                                        })
                                
                                await popup.close()
                            except Exception as e:
                                print(f"    - Error: {str(e)[:60]}")
                                try:
                                    await popup.close()
                                except:
                                    pass
                    
                    except Exception as e:
                        pass
                
                print(f"Total eventos extraídos de Platinsport: {len(eventos)}")
                
            except Exception as e:
                print(f"Error en Playwright: {e}")
            finally:
                await browser.close()
        
        return eventos
    
    # Run async function
    eventos = asyncio.run(scrape())
    return eventos

def extraer_eventos_playtorrio():
    """Extrae eventos usando la API de PlayTorrio como fallback"""
    print("Extrayendo eventos de PlayTorrio API...")
    
    try:
        import aiohttp
        import json
        from datetime import datetime, timezone
        
        # APIs de PlayTorrio
        CDNLIVE_API = 'https://ntvstream-scraper.aymanisthedude1.workers.dev/cdnlive'
        ALL_SOURCES_API = 'https://ntvstream-scraper.aymanisthedude1.workers.dev/matches'
        
        # Headers para la API
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Referer': 'https://iptv.playtorrio.xyz/',
            'Origin': 'https://iptv.playtorrio.xyz',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        
        async def fetch_events():
            eventos = []
            
            async with aiohttp.ClientSession(headers=headers) as session:
                try:
                    # Extraer de CDN Live
                    print("  - Consultando CDN Live API...")
                    async with session.get(CDNLIVE_API) as response:
                        if response.status == 200:
                            cdn_data = await response.json()
                            print(f"    CDN Live: {len(cdn_data.get('channels', []))} canales")
                            
                            for channel in cdn_data.get('channels', [])[:100]:  # Limitar
                                try:
                                    # Solo canales deportivos
                                    categories = channel.get('categories', [])
                                    if 'sport' in categories or 'sports' in categories:
                                        name = channel.get('name', 'Canal Desconocido')
                                        country = channel.get('country', 'us')
                                        logo = channel.get('logo', '')
                                        stream_url = channel.get('url', '')
                                        
                                        if stream_url:
                                            eventos.append({
                                                "hora": datetime.strptime("23:59", "%H:%M").time(),
                                                "nombre": f"Canal Deportivo - {name}",
                                                "canal": name,
                                                "url": stream_url
                                            })
                                except:
                                    continue
                    
                    # Extraer de All Sources
                    print("  - Consultando All Sources API...")
                    await asyncio.sleep(5)  # Delay para evitar rate limiting
                    
                    async with session.get(ALL_SOURCES_API) as response:
                        if response.status == 200:
                            all_data = await response.json()
                            print(f"    All Sources: {len(all_data.get('matches', []))} eventos")
                            
                            for match in all_data.get('matches', [])[:50]:  # Limitar
                                try:
                                    # Parse match time
                                    match_time = match.get('time', '')
                                    if match_time:
                                        dt = datetime.fromisoformat(match_time.replace('Z', '+00:00'))
                                        dt_utc1 = dt + timedelta(hours=1)
                                        hora_evento = dt_utc1.time()
                                    else:
                                        hora_evento = datetime.strptime("23:59", "%H:%M").time()
                                    
                                    # Get match details
                                    title = match.get('title', 'Evento Desconocido')
                                    league = match.get('league', '')
                                    
                                    # Get streaming links
                                    streams = match.get('streams', [])
                                    for stream in streams:
                                        channel = stream.get('channel', 'Canal Desconocido')
                                        url = stream.get('url', '')
                                        
                                        if url and ('acestream://' in url or url.startswith('http')):
                                            eventos.append({
                                                "hora": hora_evento,
                                                "nombre": f"{league} - {title}" if league else title,
                                                "canal": channel,
                                                "url": url
                                            })
                                            
                                except Exception as e:
                                    continue
                            
                except Exception as e:
                    print(f"Error fetching PlayTorrio: {e}")
            
            return eventos
        
        eventos = asyncio.run(fetch_events())
        print(f"Eventos extraídos de PlayTorrio: {len(eventos)}")
        return eventos
        
    except ImportError:
        print("aiohttp no disponible para PlayTorrio")
        return []
    except Exception as e:
        print(f"Error en extraer_eventos_playtorrio: {e}")
        return []

def extraer_eventos_acestream_api():
    """Extrae canales deportivos de la API de Acestream como último fallback"""
    print("Extrayendo canales de Acestream API...")
    
    try:
        API_URL = "https://api.acestream.me/all?api_version=1&api_key=test_api_key"
        
        response = requests.get(API_URL, timeout=15)
        if response.status_code != 200:
            return []
        
        data = response.json()
        eventos = []
        
        if isinstance(data, list):
            for item in data[:200]:  # Limitar a 200 canales
                try:
                    name = item.get('name', 'Unknown')
                    infohash = item.get('infohash', '')
                    categories = item.get('categories', [])
                    
                    # Solo canales deportivos
                    if 'sport' in categories or 'sports' in categories:
                        if infohash:
                            url_acestream = f"http://127.0.0.1:6878/ace/getstream?id={infohash}"
                            
                            eventos.append({
                                "hora": datetime.strptime("23:59", "%H:%M").time(),
                                "nombre": f"Canal Deportivo - {name}",
                                "canal": name,
                                "url": url_acestream
                            })
                            
                except Exception as e:
                    continue
        
        print(f"Canales extraídos de Acestream API: {len(eventos)}")
        return eventos
        
    except Exception as e:
        print(f"Error en extraer_eventos_acestream_api: {e}")
        return []

def extraer_eventos_deporte_libre():
    """Extrae eventos de DEPORTE-LIBRE.FANS como último fallback"""
    print("Extrayendo eventos de DEPORTE-LIBRE...")
    
    try:
        main_url = 'https://deporte-libre.click/canales-24-7.php'
        
        response = requests.get(main_url, timeout=15)
        if response.status_code != 200:
            return []
        
        soup = BeautifulSoup(response.text, "html.parser")
        eventos = []
        
        # Find channel links
        for a_tag in soup.find_all('a'):
            channel_name = a_tag.text.strip()
            channel_url = a_tag.get('href')
            
            if channel_name and channel_url and channel_url.startswith('/stream/'):
                full_url = 'https://deporte-libre.click' + channel_url
                
                # Try to get streaming URLs from channel page
                try:
                    channel_response = requests.get(full_url, timeout=10)
                    if channel_response.status_code == 200:
                        channel_soup = BeautifulSoup(channel_response.text, "html.parser")
                        
                        # Look for streaming links
                        for stream_a in channel_soup.find_all('a', {'class': 'btn'}):
                            stream_url = stream_a.get('href')
                            if stream_url and ('acestream://' in stream_url or stream_url.startswith('http')):
                                if stream_url.startswith('/'):
                                    stream_url = 'https://deporte-libre.click' + stream_url
                                
                                eventos.append({
                                    "hora": datetime.strptime("23:59", "%H:%M").time(),  # No time info
                                    "nombre": "Canal 24/7",
                                    "canal": channel_name,
                                    "url": stream_url
                                })
                                
                except Exception as e:
                    continue
        
        print(f"Eventos extraídos de DEPORTE-LIBRE: {len(eventos)}")
        return eventos
        
    except Exception as e:
        print(f"Error en extraer_eventos_deporte_libre: {e}")
        return []

def eliminar_repeticiones_live_stream(event_text):
    # Elimina las repeticiones de "LIVE STREAM"
    while "LIVE STREAM" in event_text:
        event_text = event_text.replace("LIVE STREAM", "").strip()
    return event_text

def convertir_a_utc_mas_1(hora):
    dt = datetime.combine(datetime.today(), hora)
    dt_utc1 = dt + timedelta(hours=1)
    return dt_utc1.time()

def normalizar_nombre(nombre):
    # Normaliza el nombre eliminando espacios adicionales y convirtiendo a minúsculas
    return re.sub(r'\s+', ' ', nombre).strip().lower()

def buscar_logo_en_archive(nombre_canal):
    tree = ET.parse('logos.xml')
    root = tree.getroot()
    nombres_logos = {normalizar_nombre(logo.find('name').text): logo.find('url').text for logo in root.findall('logo') if logo.find('name') is not None}
    nombre_canal_normalizado = normalizar_nombre(nombre_canal)
    closest_matches = get_close_matches(nombre_canal_normalizado, nombres_logos.keys(), n=3, cutoff=0.6)
    if closest_matches:
        for match in closest_matches:
            if nombre_canal_normalizado in match:
                return nombres_logos[match]
        return nombres_logos[closest_matches[0]]
    return None

def buscar_logo_en_url(nombre_canal):
    response = requests.get("https://raw.githubusercontent.com/Icastresana/lista1/refs/heads/main/peticiones")
    if response.status_code != 200:
        print("Error al acceder a la URL de logos")
        return None
    logos_data = response.text.split('\n')
    nombre_canal_normalizado = normalizar_nombre(nombre_canal)
    nombres_logos = {}
    for line in logos_data:
        match = re.search(r'tvg-logo="([^"]+)" .*?tvg-id="[^"]+", ([^,]+)', line)
        if match:
            logo_url = match.group(1)
            canal_name = match.group(2).strip().lower()
            nombres_logos[canal_name] = logo_url
    closest_matches = get_close_matches(nombre_canal_normalizado, nombres_logos.keys(), n=3, cutoff=0.6)
    if closest_matches:
        for match in closest_matches:
            if nombre_canal_normalizado in match:
                return nombres_logos[match]
        return nombres_logos[closest_matches[0]]
    return None

def buscar_logo(nombre_canal):
    logo_url = buscar_logo_en_archive(nombre_canal)
    if logo_url:
        return logo_url
    logo_url = buscar_logo_en_url(nombre_canal)
    if logo_url:
        return logo_url
    primera_palabra = nombre_canal.split(' ')[0]
    logo_url = buscar_logo_en_archive(primera_palabra)
    if logo_url:
        return logo_url
    logo_url = buscar_logo_en_url(primera_palabra)
    if logo_url:
        return logo_url
    return None

def limpiar_nombre_evento(nombre_evento):
    # Elimina prefijos como "NORTH AMERICA -", "SPAIN -", etc.
    return re.sub(r'^[A-Z ]+- ', '', nombre_evento)

def guardar_lista_m3u(eventos, archivo="lista.m3u"):
    eventos.sort(key=lambda x: x["hora"])
    with open(archivo, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for item in eventos:
            hora_ajustada = convertir_a_utc_mas_1(item["hora"])
            canal_id = normalizar_nombre(item["nombre"]).replace(" ", "_")
            nombre_evento = limpiar_nombre_evento(" ".join(item['nombre'].split()))
            logo_url = buscar_logo(item["canal"])
            extinf_line = (f"#EXTINF:-1 tvg-logo=\"{logo_url}\" tvg-id=\"{canal_id}\" tvg-name=\"{nombre_evento}\","
                           f"{hora_ajustada.strftime('%H:%M')} - {nombre_evento} - {item['canal']}\n")
            f.write(extinf_line)
            acestream_id = item['url'].split('acestream://')[-1]
            nuevo_enlace = f"http://127.0.0.1:6878/ace/getstream?id={acestream_id}"
            f.write(f"{nuevo_enlace}\n")

if __name__ == "__main__":
    url_diaria = obtener_url_diaria()
    if not url_diaria:
        print("No se pudo determinar la URL diaria.")
        exit(1)
    print("URL diaria:", url_diaria)
    eventos_platinsport = extraer_eventos(url_diaria)
    print("Eventos extraídos de Platinsport:", len(eventos_platinsport))
    
    if not eventos_platinsport:
        print("No se encontraron eventos eventualmente. Creando lista vacía...")
        # Crear lista M3U vacía para no fallar el workflow
        with open("lista.m3u", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
        print("Archivo lista.m3u creado (vacío).")
    else:
        guardar_lista_m3u(eventos_platinsport)
        print("Lista M3U actualizada correctamente con", len(eventos_platinsport), "eventos.")
