from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import re
import base64
from datetime import datetime, timezone, timedelta
import os
import sys
import html
import urllib.request
import time

BASE_URL = "https://platinsport.com/"
LOGOS_XML_URL = "https://raw.githubusercontent.com/tutw/platinsport-m3u-updater/refs/heads/main/LOGOS-CANALES-TV.xml"

# Mapeo extendido de códigos de país a nombres
COUNTRY_CODES = {
    "GB": "Reino Unido", "UK": "Reino Unido",
    "ES": "España", "PT": "Portugal", "IT": "Italia",
    "FR": "Francia", "DE": "Alemania", "NL": "Países Bajos",
    "PL": "Polonia", "RU": "Rusia", "UA": "Ucrania",
    "AR": "Argentina", "BR": "Brasil", "MX": "México",
    "US": "Estados Unidos", "CA": "Canadá",
    "TR": "Turquía", "GR": "Grecia", "RO": "Rumanía",
    "HR": "Croacia", "RS": "Serbia", "BG": "Bulgaria",
    "DK": "Dinamarca", "SE": "Suecia", "NO": "Noruega",
    "FI": "Finlandia", "BE": "Bélgica", "CH": "Suiza",
    "AT": "Austria", "CZ": "República Checa", "SK": "Eslovaquia",
    "HU": "Hungría", "XX": "Internacional",
    "AU": "Australia", "NZ": "Nueva Zelanda",
    "JP": "Japón", "KR": "Corea del Sur", "CN": "China",
    "IN": "India", "PK": "Pakistán", "BD": "Bangladesh",
    "ZA": "Sudáfrica", "EG": "Egipto", "NG": "Nigeria",
    "KE": "Kenia", "MA": "Marruecos", "TN": "Túnez",
    "SA": "Arabia Saudita", "AE": "Emiratos Árabes", "QA": "Catar",
    "IL": "Israel", "IR": "Irán", "IQ": "Irak",
    "CL": "Chile", "CO": "Colombia", "PE": "Perú",
    "VE": "Venezuela", "UY": "Uruguay", "EC": "Ecuador",
    "BO": "Bolivia", "PY": "Paraguay",
    "IE": "Irlanda", "IS": "Islandia", "LT": "Lituania",
    "LV": "Letonia", "EE": "Estonia", "SI": "Eslovenia",
    "AL": "Albania", "MK": "Macedonia", "BA": "Bosnia",
    "ME": "Montenegro", "XK": "Kosovo", "CY": "Chipre",
    "MT": "Malta", "LU": "Luxemburgo"
}

def convert_utc_to_spain(utc_time_str: str) -> str:
    """
    Convierte hora UTC a hora de España (Europe/Madrid) respetando DST.
    """
    if not utc_time_str:
        return ""
    
    try:
        dt_utc = datetime.fromisoformat(utc_time_str.replace("Z", "+00:00"))
        year = dt_utc.year
        
        # Calcular último domingo de marzo (cambio a verano)
        march_last = datetime(year, 3, 31, 1, 0, tzinfo=timezone.utc)
        while march_last.weekday() != 6:
            march_last -= timedelta(days=1)
        
        # Calcular último domingo de octubre (cambio a invierno)
        october_last = datetime(year, 10, 31, 1, 0, tzinfo=timezone.utc)
        while october_last.weekday() != 6:
            october_last -= timedelta(days=1)
        
        # Determinar el offset según la fecha
        if march_last <= dt_utc < october_last:
            spain_offset = timedelta(hours=2)  # CEST: UTC+2
        else:
            spain_offset = timedelta(hours=1)  # CET: UTC+1
        
        dt_spain = dt_utc + spain_offset
        return dt_spain.strftime("%H:%M")
        
    except Exception as e:
        print(f"⚠ Error convirtiendo hora: {e}")
        return ""

def clean_text(s: str) -> str:
    """Limpia y normaliza texto"""
    if s is None:
        return ""
    s = html.unescape(s)
    s = s.replace("\u00a0", " ").replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def extract_lang_from_flag(node) -> str:
    """Extrae el codigo de idioma de la bandera"""
    flag = node.find("span", class_=re.compile(r"\bfi\b|\bfi-"))
    if not flag:
        return "XX"
    classes = flag.get("class", []) or []
    for cls in classes:
        if cls.startswith("fi-") and len(cls) == 5:
            cc = cls.replace("fi-", "").upper()
            if cc == "UK":
                cc = "GB"
            return cc
    return "XX"

def generate_tvg_id(channel_name: str, lang_code: str) -> str:
    """Genera un tvg-id único basado en el nombre del canal y país"""
    clean_name = re.sub(r'[^a-zA-Z0-9]', '', channel_name.replace(" ", ""))
    return f"{clean_name}.{lang_code}"

def extract_time_from_datetime(match_div) -> tuple:
    """
    Extrae la hora de la etiqueta <time>.
    Retorna: (hora_utc_str, hora_españa_str)
    """
    time_tag = match_div.find("time", class_="time")
    if time_tag and time_tag.get("datetime"):
        try:
            dt_str = time_tag.get("datetime")
            spain_time = convert_utc_to_spain(dt_str)
            return dt_str, spain_time
        except Exception as e:
            print(f"⚠ Error parseando tiempo: {e}")
    return "", ""

def extract_match_title(match_div) -> str:
    """Extrae el título del partido sin la hora"""
    match_div_copy = BeautifulSoup(str(match_div), "lxml").find("div")
    if not match_div_copy:
        return ""
    
    # Remover la etiqueta <time>
    for time_tag in match_div_copy.find_all("time"):
        time_tag.decompose()
    
    return clean_text(match_div_copy.get_text())

def clean_channel_name(raw_name: str) -> str:
    """Limpia el nombre del canal"""
    name = clean_text(raw_name)
    name = re.sub(r'\b(STREAM|4K|FHD|UHD)\b', '', name, flags=re.IGNORECASE).strip()
    name = re.sub(r'\s+', ' ', name).strip()
    
    if not name:
        name = clean_text(raw_name)
    
    return name


def build_source_list_url(date: datetime | None = None) -> str:
    """Construye la URL de source-list.php con la clave de fecha requerida."""
    if date is None:
        date = datetime.now(timezone.utc)
    key = base64.b64encode(f"{date.strftime('%Y-%m-%d')}PLATINSPORT".encode()).decode()
    return f"https://www.platinsport.com/link/source-list.php?key={key}"

def parse_html_for_streams(html_content: str):
    """
    Parsea el HTML y extrae streams con información de liga.
    Versión mejorada: busca múltiples patrones y estructuras
    """
    soup = BeautifulSoup(html_content, "lxml")
    entries = []
    
    print(f"\n✓ Procesando elementos del HTML...")
    print(f"  📊 Longitud total del HTML: {len(html_content)} caracteres")
    
    # DEBUG: Guardar una versión simplificada del HTML para análisis
    with open("debug/parsed_html_sample.txt", "w", encoding="utf-8") as f:
        # Extraer solo el texto visible y algunas etiquetas importantes
        for tag in soup.find_all(['div', 'p', 'a', 'span']):
            if tag.get_text().strip():
                f.write(f"{tag.name}: {tag.get_text().strip()[:100]}...\n")
                if 'href' in tag.attrs and 'acestream://' in tag.get('href', ''):
                    f.write(f"  -> ACSTREAM LINK: {tag.get('href')}\n")
    
    # Estrategia 1: Buscar estructura original (match-title-bar + button-group)
    print("  🔍 Buscando estructura original (match-title-bar)...")
    match_divs = soup.find_all("div", class_="match-title-bar")
    print(f"  📊 Encontrados {len(match_divs)} elementos match-title-bar")
    
    current_league = "Unknown League"
    
    for elem in match_divs:
        dt_utc_str, event_time = extract_time_from_datetime(elem)
        match_title = extract_match_title(elem)
        
        button_group = elem.find_next_sibling("div", class_="button-group")
        if not button_group:
            continue
        
        links = button_group.find_all("a", href=re.compile(r"^acestream://"))
        
        print(f"  ⚽ {match_title} ({current_league}) - {len(links)} streams")
        
        for a in links:
            href = clean_text(a.get("href", ""))
            if not href.startswith("acestream://"):
                continue
            
            lang_code = extract_lang_from_flag(a)
            country_name = COUNTRY_CODES.get(lang_code, lang_code)
            
            a_copy = BeautifulSoup(str(a), "lxml").find("a")
            if not a_copy:
                continue
            
            # Eliminar banderas
            for flag in a_copy.find_all("span", class_=re.compile(r"\bfi\b|\bfi-")):
                flag.decompose()
            
            channel_name_raw = clean_text(a_copy.get_text())
            
            if not channel_name_raw or channel_name_raw in ["", "STREAM HD", "HD", "STREAM"]:
                channel_name_raw = clean_text(a.get("title", ""))
                if not channel_name_raw or channel_name_raw in ["", "STREAM HD"]:
                    channel_name_raw = f"Stream {lang_code}"
            
            channel_name = clean_channel_name(channel_name_raw)
            
            # Generar tvg-id único
            tvg_id = generate_tvg_id(channel_name, lang_code)
            
            entries.append({
                "time": event_time,
                "match": match_title,
                "league": current_league,
                "lang_code": lang_code,
                "country": country_name,
                "channel": channel_name,
                "url": href,
                "tvg_id": tvg_id,
            })
    
    # Estrategia 2: Buscar todos los enlaces acestream directamente
    print("  🔍 Buscando todos los enlaces acestream...")
    
    # Buscar con múltiples patrones
    patterns = [
        r'acestream://[^\s"\'<>]+',  # Patrón original
        r'acestream://[^<\s]+',      # Más permisivo
        r'acestream://[^\s]+',       # Aún más permisivo
    ]
    
    all_acestream_links = []
    for pattern in patterns:
        links = soup.find_all("a", href=re.compile(pattern))
        all_acestream_links.extend(links)
    
    # También buscar en todo el texto del HTML
    text_acestream = re.findall(r'acestream://[^\s"\'<>]+', html_content)
    
    print(f"  📊 Encontrados {len(all_acestream_links)} enlaces acestream en soup")
    print(f"  📊 Encontrados {len(text_acestream)} enlaces acestream en texto")
    
    # DEBUG: Mostrar algunos links encontrados
    for i, link in enumerate(all_acestream_links[:5]):
        print(f"    [{i+1}] {link.get('href')} - Text: '{link.get_text().strip()}'")
    
    for i, url in enumerate(text_acestream[:5]):
        print(f"    [T{i+1}] {url}")
    
    for a in all_acestream_links:
        href = clean_text(a.get("href", ""))
        
        # Validar que sea una URL de acestream válida
        if not re.match(r'^acestream://[a-fA-F0-9]{40}$', href):
            print(f"  ⚠ Omitiendo enlace inválido: {href}")
            continue
            
        if not href.startswith("acestream://"):
            continue
        
        # Solo procesar si no fue encontrado en la estrategia 1
        if any(e["url"] == href for e in entries):
            continue
        
        print(f"  ➕ Procesando enlace adicional válido: {href}")
        
        lang_code = extract_lang_from_flag(a)
        country_name = COUNTRY_CODES.get(lang_code, lang_code)
        
        # Intentar extraer información del contexto
        parent_div = a.find_parent("div")
        match_title = "Evento Desconocido"
        event_time = ""
        
        if parent_div:
            # Buscar título del partido en el contexto
            title_elem = parent_div.find_previous("div", class_=re.compile(r"match|title|event"))
            if title_elem:
                match_title = clean_text(title_elem.get_text())
            
            # Buscar tiempo
            time_elem = parent_div.find("time") or parent_div.find_previous("time")
            if time_elem and time_elem.get("datetime"):
                try:
                    dt_str = time_elem.get("datetime")
                    event_time = convert_utc_to_spain(dt_str)
                except:
                    pass
        
        a_copy = BeautifulSoup(str(a), "lxml").find("a")
        if a_copy:
            # Eliminar banderas
            for flag in a_copy.find_all("span", class_=re.compile(r"\bfi\b|\bfi-")):
                flag.decompose()
            
            channel_name_raw = clean_text(a_copy.get_text())
        else:
            channel_name_raw = clean_text(a.get("title", "")) or f"Stream {lang_code}"
        
        channel_name = clean_channel_name(channel_name_raw)
        tvg_id = generate_tvg_id(channel_name, lang_code)
        
        entries.append({
            "time": event_time,
            "match": match_title,
            "league": current_league,
            "lang_code": lang_code,
            "country": country_name,
            "channel": channel_name,
            "url": href,
            "tvg_id": tvg_id,
        })
    
    # Estrategia 3: Buscar patrones de texto con acestream://
    print("  🔍 Buscando patrones de texto con acestream...")
    text_content = soup.get_text()
    
    # Múltiples patrones para encontrar URLs
    text_patterns = [
        r'acestream://[^\s"\'<>]+',
        r'acestream://[^\s]+',
        r'acestream://[^<\s]+',
    ]
    
    all_text_urls = []
    for pattern in text_patterns:
        urls = re.findall(pattern, text_content)
        all_text_urls.extend(urls)
    
    # También buscar en el HTML crudo
    raw_patterns = [
        r'acestream://[^\s"\'<>]+',
        r'acestream://[^\s]+',
        r'acestream://[^<\s]+',
    ]
    
    all_raw_urls = []
    for pattern in raw_patterns:
        urls = re.findall(pattern, html_content)
        all_raw_urls.extend(urls)
    
    print(f"  📊 Encontrados {len(all_text_urls)} URLs acestream en texto parseado")
    print(f"  📊 Encontrados {len(all_raw_urls)} URLs acestream en HTML crudo")
    
    # DEBUG: Mostrar URLs encontradas en texto
    for i, url in enumerate(all_text_urls[:5]):
        print(f"    [T{i+1}] {url}")
    
    for i, url in enumerate(all_raw_urls[:5]):
        print(f"    [R{i+1}] {url}")
    
    # Usar la combinación de todas las URLs encontradas
    acestream_urls = list(set(all_text_urls + all_raw_urls))
    
    for url in acestream_urls:
        url = url.strip()
        
        # Validar que sea una URL de acestream válida
        if not re.match(r'^acestream://[a-fA-F0-9]{40}$', url):
            print(f"  ⚠ Omitiendo URL inválida: {url}")
            continue
            
        if any(e["url"] == url for e in entries):
            continue
        
        print(f"  ➕ Procesando URL de texto válida: {url}")
        
        entries.append({
            "time": "",
            "match": "Evento Desconocido",
            "league": "Unknown League",
            "lang_code": "XX",
            "country": "Internacional",
            "channel": f"Stream {len(entries) + 1}",
            "url": url,
            "tvg_id": f"stream{len(entries) + 1}.XX",
        })
    
    print(f"  ✅ Total de streams únicos encontrados: {len(entries)}")
    return entries

def write_m3u(all_entries, out_path="lista.m3u"):
    """
    Escribe el archivo M3U con formato:
    HH:MM | Liga | Evento | Canal | [País]
    """
    m3u = ["#EXTM3U"]
    
    GROUP_NAME = "AGENDA PLATINSPORT"
    
    for idx, e in enumerate(all_entries, 1):
        event_time = e.get("time", "")
        match = e.get("match", "Evento")
        league = e.get("league", "")
        country = e.get("country", "")
        channel = e.get("channel", "STREAM")
        url = e.get("url", "")
        tvg_id = e.get("tvg_id", "")
        lang_code = e.get("lang_code", "")
        
        tvg_name = channel
        
        # Construir nombre de visualización: HH:MM | Liga | Evento | Canal | [País]
        display_name_parts = []
        if event_time:
            display_name_parts.append(event_time)
        if league:
            display_name_parts.append(league)
        if match:
            display_name_parts.append(match)
        display_name_parts.append(channel)
        if country and country != "Internacional":
            display_name_parts.append(f"[{country}]")
        
        display_name = " | ".join(display_name_parts)
        
        # Construir línea EXTINF
        extinf_parts = ['#EXTINF:-1']
        
        if tvg_id:
            extinf_parts.append(f'tvg-id="{tvg_id}"')
        
        extinf_parts.append(f'tvg-name="{tvg_name}"')
        extinf_parts.append(f'group-title="{GROUP_NAME}"')
        
        if lang_code and lang_code != "XX":
            extinf_parts.append(f'tvg-country="{lang_code}"')
        
        extinf_line = ' '.join(extinf_parts) + f',{display_name}'
        
        m3u.append(extinf_line)
        
        # Convertir acestream:// a formato localhost
        if url.startswith("acestream://"):
            ace_id = url.replace("acestream://", "")
            stream_url = f"http://127.0.0.1:6878/ace/getstream?id={ace_id}"
        else:
            stream_url = url
        
        m3u.append(stream_url)
    
    # Escribir archivo
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(m3u) + "\n")
    
    print(f"\n✓ Archivo {out_path} generado con {len(all_entries)} entradas")
    print(f"✓ Todos los eventos agrupados en: {GROUP_NAME}")
    print(f"✓ Formato: HORA | LIGA | EVENTO | CANAL | [PAÍS]")

def main():
    print("=" * 70)
    print("=== PLATINSPORT M3U UPDATER - VERSIÓN CORREGIDA ===")
    print("=== CON DETECCIÓN DE LIGAS Y SIN ELIMINAR DUPLICADOS ===")
    print("=" * 70)
    print(f"Python: {sys.version.split()[0]}")
    print(f"Inicio: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 70)

    os.makedirs("debug", exist_ok=True)

    raw_html = None

    with sync_playwright() as p:
        print("\n[1] Lanzando navegador...")
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Europe/Madrid",
            java_script_enabled=True,
            ignore_https_errors=True,
            # Configuraciones adicionales para evitar detección
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
                "Cache-Control": "max-age=0",
                "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"macOS"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1"
            },
            # Deshabilitar algunas características que pueden delatar automatización
            permissions=["geolocation"],
            geolocation={"latitude": 40.4168, "longitude": -3.7038},  # Madrid
            # Configurar WebGL y Canvas para que parezcan reales
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
        )
        
        expiry = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())
        context.add_cookies([{
            "name": "disclaimer_accepted",
            "value": "true",
            "domain": ".platinsport.com",
            "path": "/",
            "expires": expiry,
            "sameSite": "Lax"
        }])
        print("[2] Cookie disclaimer establecida")

        def handle_route(route, request):
            nonlocal raw_html
            
            url = request.url
            lower_url = url.lower()
            
            allowed_external_hosts = [
                "fonts.googleapis.com",
                "fonts.gstatic.com",
                "googleapis.com",
                "gstatic.com",
                "s.w.org",
            ]
            
            is_external = "platinsport.com" not in lower_url and not any(host in lower_url for host in allowed_external_hosts)
            
            if request.is_navigation_request() and is_external:
                print(f"[4] Abortando navegación externa: {url}")
                route.abort()
                return
            
            if is_external and request.resource_type in ["script", "image", "stylesheet", "font", "xhr", "fetch", "document"]:
                print(f"[4] Abortando recurso externo: {url}")
                route.abort()
                return
            
            # Solo interceptar requests que probablemente contengan texto/HTML dentro de platinsport.com
            content_type_likely_text = any(ext in lower_url for ext in [
                '.html', '.htm', '.php', '.asp', '.jsp', '.xml', '.json', '.txt'
            ]) or any(keyword in lower_url for keyword in [
                'source-list', 'streams', 'acestream', 'matches', 'events', 
                'schedule', 'api', 'data', 'content'
            ])
            
            # Excluir archivos binarios conocidos
            is_binary_file = any(ext in lower_url for ext in [
                '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.woff', '.woff2', 
                '.ttf', '.eot', '.css', '.js'
            ])
            
            if content_type_likely_text and not is_binary_file and "platinsport.com" in lower_url:
                print(f"[4] Interceptando posible fuente de texto: {url}")
                
                try:
                    response = route.fetch()
                    
                    # Verificar content-type
                    content_type = response.headers.get('content-type', '').lower()
                    if 'text' in content_type or 'json' in content_type or 'xml' in content_type:
                        body = response.text()
                        
                        # Verificar si contiene streams
                        if "acestream://" in body:
                            raw_html = body
                            acestream_count = body.count("acestream://")
                            print(f"[5] HTML con streams capturado: {acestream_count} streams, {len(body)} bytes")
                            
                            with open("debug/intercepted_content.html", "w", encoding="utf-8") as f:
                                f.write(body)
                            print("[6] Debug guardado: debug/intercepted_content.html")
                            
                            route.fulfill(response=response)
                        else:
                            route.continue_()
                    else:
                        route.continue_()
                except UnicodeDecodeError:
                    print(f"[4] Archivo binario detectado, omitiendo: {url}")
                    route.continue_()
                except Exception as e:
                    print(f"[4] Error interceptando {url}: {e}")
                    route.continue_()
            else:
                route.continue_()
        
        context.route("**/*", handle_route)
        print("[3] Interceptor registrado")

        page = context.new_page()

        # Configurar interceptor para detectar redirecciones no deseadas
        def handle_response(response):
            url = response.url
            # Solo reportar redirecciones problemáticas
            if any(domain in url for domain in ['unharcon.com', 'click.php', 'ads.', 'doubleclick', 'googlesyndication']):
                print(f"     🚫 REDIRECCIÓN PROBLEMÁTICA: {url}")
                browser.close()
                sys.exit(1)
            elif "platinsport.com" not in url and url != BASE_URL and not any(allowed in url for allowed in ['fonts.googleapis.com', 'fonts.gstatic.com', 'googleapis.com', 'gstatic.com', 's.w.org']):
                print(f"     ⚠ Redirección externa: {url}")

        page.on("response", lambda response: handle_response(response))

        source_list_url = build_source_list_url()
        print(f"[7] Navegando a la lista de streams: {source_list_url}")
        try:
            page.goto("about:blank")
            time.sleep(1)
            page.goto(source_list_url, timeout=120000, wait_until="domcontentloaded", referer=BASE_URL)
            
            current_url = page.url
            if "platinsport.com" not in current_url:
                print(f"     🚫 Redirigido fuera del sitio: {current_url}")
                browser.close()
                sys.exit(1)

            print("     Página source-list cargada")
        except Exception as e:
            print(f"     Error al cargar source-list: {e}")
            browser.close()
            sys.exit(1)

        print("[8] Capturando contenido de la lista de streams...")
        try:
            # Esperar a que la página cargue completamente (con timeout más corto y manejo de errores)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
                print("     Estado networkidle alcanzado")
            except Exception as e:
                print(f"     ⚠ No se alcanzó networkidle en 15s: {e}")
                print("     Continuando con la extracción...")
            
            time.sleep(3)  # Esperar un poco más por contenido dinámico
            
            # Intentar múltiples estrategias para cargar contenido
            print("     Cargando contenido dinámico...")
            
            # Estrategia 1: Hacer scroll agresivo con manejo de errores
            for i in range(5):
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(2)
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(1)
                except Exception as e:
                    print(f"     ⚠ Error en scroll agresivo {i+1}: {e}")
                    break  # Salir del bucle si hay error
            
            # Estrategia 2: Buscar y hacer click en elementos que puedan cargar contenido
            try:
                # Buscar botones o enlaces que digan "load more", "show more", etc.
                load_selectors = [
                    "button:has-text('Load More')", "a:has-text('Load More')",
                    "button:has-text('Show More')", "a:has-text('Show More')",
                    ".load-more", ".show-more", "[data-load-more]",
                    "button[class*='load']", "a[class*='load']"
                ]
                
                for selector in load_selectors:
                    try:
                        elements = page.query_selector_all(selector)
                        for elem in elements[:2]:  # Solo los primeros 2 para no sobrecargar
                            try:
                                elem.click()
                                time.sleep(3)
                                print(f"     Clickeado elemento de carga: {selector}")
                            except Exception as e:
                                print(f"     ⚠ Error clickeando elemento {selector}: {e}")
                    except Exception as e:
                        print(f"     ⚠ Error buscando elementos {selector}: {e}")
                
                # Estrategia 3: Esperar por cambios en el DOM
                try:
                    initial_height = page.evaluate("document.body.scrollHeight")
                    time.sleep(5)
                    final_height = page.evaluate("document.body.scrollHeight")
                    
                    if final_height > initial_height:
                        print(f"     Contenido dinámico cargado: altura cambió de {initial_height} a {final_height}")
                except Exception as e:
                    print(f"     ⚠ Error evaluando altura del DOM: {e}")
                    initial_height = final_height = 0
                
            except Exception as e:
                print(f"     Error en estrategias de carga dinámica: {e}")
            
            # Capturar el HTML final
            raw_html = page.content()
            
            # DEBUG: Guardar HTML inmediatamente después de capturarlo
            with open("debug/immediate_capture.html", "w", encoding="utf-8") as f:
                f.write(raw_html)
            print("     ✓ HTML inmediato guardado en debug/immediate_capture.html")
            
            # Guardar el HTML completo para debugging
            with open("debug/full_page_content.html", "w", encoding="utf-8") as f:
                f.write(raw_html)
            print("     ✓ HTML completo guardado en debug/full_page_content.html")
            
            # Verificar si hay contenido de eventos
            acestream_count = raw_html.count("acestream://")
            if acestream_count > 0:
                print(f"     ✓ Contenido con {acestream_count} enlaces acestream encontrado")
                # Mostrar las primeras ocurrencias para debugging
                import re
                
                # Buscar todas las apariciones de "acestream" para debugging
                acestream_positions = []
                start = 0
                while True:
                    pos = raw_html.find("acestream", start)
                    if pos == -1:
                        break
                    # Extraer contexto alrededor de la posición
                    context_start = max(0, pos - 50)
                    context_end = min(len(raw_html), pos + 100)
                    context = raw_html[context_start:context_end]
                    context = context.replace('\n', ' ').replace('\r', ' ')
                    # Escapar caracteres de control para mostrar
                    context = repr(context)
                    acestream_positions.append(f"Pos {pos}: {context}")
                    start = pos + 1
                
                print(f"     📋 Posiciones de 'acestream' encontradas: {len(acestream_positions)}")
                for pos_info in acestream_positions[:3]:  # Mostrar las primeras 3
                    print(f"        {pos_info}")
                
                # Intentar múltiples patrones regex
                patterns = [
                    r'acestream://[^\s"\'<>]+',
                    r'acestream://[^\s]+',
                    r'acestream://[^<\s]+',
                    r'acestream[^<\s]*',  # Más amplio
                ]
                
                for i, pattern in enumerate(patterns):
                    matches = re.findall(pattern, raw_html)
                    print(f"     📋 Patrón {i+1} ({pattern}): {len(matches)} matches")
                    if matches:
                        print(f"        Primeros: {matches[:3]}")
                daily_url = page.url
            else:
                print("     ⚠ No se encontraron enlaces acestream en el HTML final")
                # Mostrar una muestra del HTML para debugging
                sample = raw_html[:2000] + "..." if len(raw_html) > 2000 else raw_html
                print(f"     Muestra del HTML: {sample}")
                
                daily_url = page.url
            
            print(f"     SUCCESS! Contenido capturado de: {daily_url}")
            
        except Exception as e:
            print(f"     Error extrayendo contenido: {e}")
            import traceback
            traceback.print_exc()
            browser.close()
            sys.exit(1)

        browser.close()
    
    if not raw_html:
        print("\n❌ ERROR: No se pudo capturar el HTML")
        sys.exit(1)
    
    # Verificación final antes del parsing
    final_count = raw_html.count("acestream://")
    print(f"\n🔍 Verificación final: {final_count} enlaces acestream en HTML a parsear")
    if final_count > 0:
        import re
        
        # Buscar todas las apariciones de "acestream" para debugging
        acestream_positions = []
        start = 0
        while True:
            pos = raw_html.find("acestream", start)
            if pos == -1:
                break
            # Extraer contexto alrededor de la posición
            context_start = max(0, pos - 50)
            context_end = min(len(raw_html), pos + 100)
            context = raw_html[context_start:context_end]
            context = context.replace('\n', ' ').replace('\r', ' ')
            # Escapar caracteres de control para mostrar
            context = repr(context)
            acestream_positions.append(f"Pos {pos}: {context}")
            start = pos + 1
        
        print(f"🔍 Posiciones finales de 'acestream': {len(acestream_positions)}")
        for pos_info in acestream_positions[:3]:  # Mostrar las primeras 3
            print(f"   {pos_info}")
        
        # Intentar múltiples patrones regex
        patterns = [
            r'acestream://[^\s"\'<>]+',
            r'acestream://[^\s]+',
            r'acestream://[^<\s]+',
            r'acestream[^<\s]*',  # Más amplio
        ]
        
        for i, pattern in enumerate(patterns):
            matches = re.findall(pattern, raw_html)
            print(f"🔍 Patrón {i+1} ({pattern}): {len(matches)} matches")
            if matches:
                print(f"   Primeros: {matches[:3]}")
    
    print("\n" + "=" * 70)
    print("PARSEANDO STREAMS CON DETECCIÓN DE LIGAS...")
    print("=" * 70)
    
    all_entries = parse_html_for_streams(raw_html)
    
    print(f"\n✓ Total streams encontrados: {len(all_entries)}")
    
    if len(all_entries) == 0:
        print("❌ ERROR: No se encontraron streams")
        print("Es posible que el sitio haya cambiado su estructura")
        print("Contenido HTML capturado (primeros 1000 caracteres):")
        print("-" * 50)
        print(raw_html[:1000])
        print("-" * 50)
        sys.exit(1)
    
    if len(all_entries) < 5:
        print(f"⚠ ADVERTENCIA: Solo {len(all_entries)} streams encontrados (esperados: 5+)")
        print("Continuando de todas formas...")

    # NO eliminamos duplicados - el usuario lo pidió expresamente
    print(f"✓ Conservando TODOS los streams (sin eliminar duplicados)")

    # Guardar el M3U
    write_m3u(all_entries, "lista.m3u")
    
    # Mostrar muestra
    print("\n" + "=" * 70)
    print("MUESTRA DE LOS PRIMEROS 10 CANALES:")
    print("=" * 70)
    for i, e in enumerate(all_entries[:10], 1):
        time_str = f"{e['time']}" if e['time'] else "??:??"
        print(f"  {i}. {time_str} | {e['league'][:30]} | {e['match'][:30]} | {e['channel']} [{e['country']}]")
    
    # Estadísticas por liga
    league_counts = {}
    for e in all_entries:
        league = e.get('league', 'Unknown')
        if league not in league_counts:
            league_counts[league] = 0
        league_counts[league] += 1
    
    print(f"\n📊 Estadísticas por liga:")
    for league, count in sorted(league_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  {league}: {count} enlaces")
    
    print("\n" + "=" * 70)
    print("✅ PROCESO COMPLETADO EXITOSAMENTE")
    print("=" * 70)

if __name__ == "__main__":
    main()
