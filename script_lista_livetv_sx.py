#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
from bs4 import BeautifulSoup
import re
import xml.etree.ElementTree as ET
import xml.dom.minidom
from datetime import datetime, timedelta
import time
import random
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor
import logging
import urllib3
import os
import sys
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Deshabilitar advertencias de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

# Configurar logging
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("scraper.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

# Patrón de regex para encontrar los enlaces de eventos
EVENT_PATH_REGEX = r"^/es/eventinfo/(\d+(_+)?([a-zA-Z0-9_-]+)?)/?$"

# Patrones para extraer información de fecha y hora mejorados
DATE_TIME_PATTERNS = [
    r'(\d{1,2})\s+de\s+(\w+)[\s,]*(\d{1,2}):(\d{2})',
    r'(\d{1,2})\s+de\s+(\w+)[\s,]+\w+[\s,]*(\d{1,2}):(\d{2})',
    r'(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})',
    r'(\d{1,2})-(\d{1,2})-(\d{4})\s+(\d{1,2}):(\d{2})',
    r'(\d{1,2}):(\d{2})',
]

DATE_PATTERNS = [
    r'(\d{1,2})\s+de\s+(\w+)[\s,]*(\w+)?',
    r'Hoy\s*\((\d{1,2})\s+de\s+(\w+)[\s,]*(\w+)?\)',
    r'(\d{1,2})\s+(\w+)[\s,]*(\w+)?',
]

# Diccionario de meses en español
MESES_ES = {
    'enero': '01', 'febrero': '02', 'marzo': '03', 'abril': '04',
    'mayo': '05', 'junio': '06', 'julio': '07', 'agosto': '08',
    'septiembre': '09', 'octubre': '10', 'noviembre': '11', 'diciembre': '12'
}

# Lista de User-Agents rotativos para evitar detección
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15',
]

def get_random_headers():
    """Genera headers aleatorios para evitar detección"""
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }

class EventScraper:
    DEFAULT_MIRRORS = [
        "https://livetv.sx",
        "https://livetvsx.ru",
        "https://livetv808.me",
        "https://livetv846.me",
        "https://cdn.livetv808.me",
        "https://livetv.net"
    ]

    def __init__(self, base_url="https://livetv.sx", max_pages=200, max_workers=5):
        self.base_url = base_url
        self.max_pages = max_pages
        self.max_workers = max_workers
        self.all_events = []
        self.session = self._create_session()
        self.current_date_context = None
        self.sports_mapping = {}  # Mapeo dinámico de deportes
        self.sports_urls = {}     # URLs de deportes extraídas
        self.request_count = 0
        self.last_request_time = 0

    def _create_session(self):
        """Crea una sesión con reintentos y soporte de cloudscraper si está disponible"""
        if HAS_CLOUDSCRAPER:
            logging.info("🛡️ Utilizando cloudscraper para evasión de protección Cloudflare")
            session = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'desktop': True
                }
            )
        else:
            session = requests.Session()

        # Configurar reintentos automáticos (sin 503 en status_forcelist para poder gestionarlo manualmente)
        retry_strategy = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )

        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=20
        )

        session.mount("http://", adapter)
        session.mount("https://", adapter)

        return session

    def find_working_base_url(self):
        """Comprueba y selecciona un dominio espejo activo si la base principal falla"""
        candidates = [self.base_url] + [m for m in self.DEFAULT_MIRRORS if m != self.base_url]
        logging.info("🔎 Comprobando disponibilidad de dominios y espejos de LiveTV...")
        for candidate in candidates:
            test_url = f"{candidate}/es/"
            try:
                resp = self.session.get(test_url, verify=False, timeout=10)
                if resp and resp.status_code == 200 and len(resp.text) > 500:
                    logging.info(f"✅ Espejo activo seleccionado: {candidate}")
                    self.base_url = candidate
                    return True
                else:
                    status = resp.status_code if resp else 'No response'
                    logging.warning(f"Respuesta no válida de {candidate}: status {status}")
            except Exception as e:
                logging.warning(f"No se pudo conectar con {candidate}: {e}")
        return False

    def _make_request(self, url, max_retries=3):
        """Realiza una petición con headers rotativos y manejo de errores"""
        for attempt in range(max_retries):
            try:
                # Rotar headers en cada petición
                if not HAS_CLOUDSCRAPER:
                    self.session.headers.update(get_random_headers())

                # Añadir delay progresivo entre peticiones
                current_time = time.time()
                time_since_last = current_time - self.last_request_time
                if time_since_last < 2:
                    sleep_time = random.uniform(2, 4)
                    time.sleep(sleep_time)

                self.last_request_time = time.time()
                self.request_count += 1

                # Realizar la petición
                response = self.session.get(
                    url,
                    verify=False,
                    timeout=30,
                    allow_redirects=True
                )

                # Si es 503, esperar más tiempo y reintentar
                if response.status_code == 503:
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 10
                        logging.warning(f"Error 503 en {url}, esperando {wait_time}s antes de reintentar...")
                        time.sleep(wait_time)
                        continue
                    else:
                        logging.error(f"Error 503 persistente en {url} después de {max_retries} intentos")
                        return None

                if response.status_code == 200:
                    return response
                else:
                    logging.warning(f"Status code {response.status_code} para {url}")
                    if attempt < max_retries - 1:
                        time.sleep(random.uniform(3, 6))
                        continue

            except Exception as e:
                logging.warning(f"Error en petición (intento {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(random.uniform(5, 10))
                    continue
                else:
                    logging.error(f"Fallo definitivo al acceder a {url}")
                    return None

        return None

    def extract_sports_mapping(self):
        """
        Extrae dinámicamente el mapeo de deportes desde la página principal
        usando el XPath específico proporcionado
        """
        url = f"{self.base_url}/es/"
        logging.info(f"Extrayendo mapeo de deportes desde: {url}")

        try:
            response = self._make_request(url)
            if not response:
                logging.error(f"No se pudo acceder a la página principal")
                logging.info("Intentando con estrategia de fallback...")
                self.fallback_sports_detection()
                return len(self.sports_mapping) > 0

            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Implementar diferentes estrategias para encontrar los enlaces de deportes
            sport_links = []
            
            # Estrategia 1: Buscar en el área específica mencionada en el XPath
            # /html/body/table/tbody/tr/td[2]/table/tbody/tr[4]/td/table/tbody/tr/td[2]/table/tbody/tr/td/table/tbody/tr[2]/td/table/tbody/tr/td/table/tbody/tr/td/table[1]/tbody/tr/td[1]
            
            # Buscar tabla principal
            main_tables = soup.find_all('table')
            for table in main_tables:
                # Buscar enlaces que contengan "allupcomingsports"
                links = table.find_all('a', href=re.compile(r'/es/allupcomingsports/\d+/?'))
                sport_links.extend(links)
            
            # Estrategia 2: Buscar directamente por patrón de URL
            if not sport_links:
                sport_links = soup.find_all('a', href=re.compile(r'/es/allupcomingsports/\d+/?'))
            
            # Estrategia 3: Buscar en el sidebar o menú de navegación
            if not sport_links:
                sidebar_elements = soup.find_all(['div', 'td'], class_=re.compile(r'(sidebar|menu|nav)', re.I))
                for element in sidebar_elements:
                    links = element.find_all('a', href=re.compile(r'/es/allupcomingsports/\d+/?'))
                    sport_links.extend(links)
            
            # Procesar los enlaces encontrados
            for link in sport_links:
                try:
                    href = link.get('href', '')
                    text = link.get_text(strip=True)
                    
                    # Extraer número de página de la URL
                    match = re.search(r'/es/allupcomingsports/(\d+)/?', href)
                    if match:
                        page_num = int(match.group(1))
                        
                        # Limpiar y validar el texto del deporte
                        if text and len(text.strip()) > 0:
                            sport_name = text.strip()
                            
                            # Filtrar textos que no parecen nombres de deportes
                            if not re.match(r'^\d+$', sport_name) and len(sport_name) > 1:
                                self.sports_mapping[page_num] = sport_name
                                self.sports_urls[page_num] = urljoin(self.base_url, href)
                                logging.debug(f"Deporte encontrado: {page_num} -> {sport_name}")
                
                except Exception as e:
                    logging.debug(f"Error procesando enlace de deporte: {e}")
                    continue
            
            # Si no se encontraron deportes, usar estrategia de fallback
            if not self.sports_mapping:
                logging.warning("No se pudieron extraer deportes automáticamente. Usando estrategia de fallback.")
                self.fallback_sports_detection()
                return len(self.sports_mapping) > 0
            
            logging.info(f"✅ Extraídos {len(self.sports_mapping)} deportes dinámicamente:")
            for page_num, sport_name in sorted(self.sports_mapping.items()):
                logging.info(f"   {page_num}: {sport_name}")
            
            return True
            
        except Exception as e:
            logging.error(f"Error extrayendo mapeo de deportes: {e}")
            self.fallback_sports_detection()
            return len(self.sports_mapping) > 0

    def fallback_sports_detection(self):
        """
        Estrategia de fallback: intentar extraer deportes probando URLs secuenciales
        """
        logging.info("Ejecutando estrategia de fallback para detectar deportes...")

        # Probar las primeras 20 páginas para detectar deportes válidos
        for page_num in range(1, 21):
            try:
                url = f"{self.base_url}/es/allupcomingsports/{page_num}/"
                response = self._make_request(url)

                if response and response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')

                    # Buscar indicadores del nombre del deporte en la página
                    title_elements = soup.find_all(['title', 'h1', 'h2', 'h3'])
                    for element in title_elements:
                        text = element.get_text().strip()
                        # Buscar patrones que indiquen nombre de deporte
                        sport_match = re.search(r'(Fútbol|Hockey|Baloncesto|Tenis|Voleibol|Boxeo|Automovilismo|Futsal|Balonmano|Rugby|Béisbol|Fútbol americano|Billar|Dardos|Badminton|Ciclismo|Críquet)', text, re.IGNORECASE)
                        if sport_match:
                            sport_name = sport_match.group(1)
                            self.sports_mapping[page_num] = sport_name
                            self.sports_urls[page_num] = url
                            logging.debug(f"Deporte detectado por fallback: {page_num} -> {sport_name}")
                            break

                    # Si no se encontró en títulos, usar nombre genérico
                    if page_num not in self.sports_mapping:
                        self.sports_mapping[page_num] = f"Deporte_{page_num}"
                        self.sports_urls[page_num] = url

                # Pequeña pausa para evitar bloqueos
                time.sleep(random.uniform(1, 3))

            except Exception as e:
                logging.debug(f"Error en fallback para página {page_num}: {e}")
                continue

    def extract_date_from_context(self, soup):
        """Extrae la fecha del contexto de la página"""
        try:
            date_headers = soup.find_all(text=re.compile(r'(Hoy|Mañana|\d+\s+de\s+\w+)'))
            
            for header in date_headers:
                header_text = header.strip()
                
                for pattern in DATE_PATTERNS:
                    match = re.search(pattern, header_text, re.IGNORECASE)
                    if match:
                        groups = match.groups()
                        
                        if "hoy" in header_text.lower():
                            if len(groups) >= 2:
                                dia, mes = groups[0], groups[1]
                                mes_num = MESES_ES.get(mes.lower(), mes)
                                return f"{dia} de {mes}"
                        else:
                            if len(groups) >= 2:
                                dia, mes = groups[0], groups[1]
                                return f"{dia} de {mes}"
            
            today = datetime.now()
            mes_nombre = list(MESES_ES.keys())[int(today.strftime('%m')) - 1]
            return f"{today.day} de {mes_nombre}"
            
        except Exception as e:
            logging.debug(f"Error al extraer fecha del contexto: {e}")
            today = datetime.now()
            mes_nombre = list(MESES_ES.keys())[int(today.strftime('%m')) - 1]
            return f"{today.day} de {mes_nombre}"

    def parse_date_time(self, text, page_soup=None):
        """Función mejorada para extraer fecha y hora del texto"""
        if not text:
            fecha_contexto = self.extract_date_from_context(page_soup) if page_soup else "No especificado"
            return fecha_contexto, "No especificado"
        
        text = text.strip()
        
        for pattern in DATE_TIME_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                groups = match.groups()
                
                if len(groups) == 4:
                    dia, mes, hora, minuto = groups
                    mes_lower = mes.lower()
                    if mes_lower in MESES_ES:
                        fecha = f"{dia} de {mes}"
                        tiempo = f"{hora.zfill(2)}:{minuto}"
                        return fecha, tiempo
                elif len(groups) == 5:
                    dia, mes, año, hora, minuto = groups
                    fecha = f"{dia.zfill(2)}/{mes.zfill(2)}/{año}"
                    tiempo = f"{hora.zfill(2)}:{minuto}"
                    return fecha, tiempo
                elif len(groups) == 2:
                    hora, minuto = groups
                    tiempo = f"{hora.zfill(2)}:{minuto}"
                    fecha_contexto = self.extract_date_from_context(page_soup) if page_soup else "No especificado"
                    return fecha_contexto, tiempo
        
        hora_match = re.search(r'(\d{1,2}):(\d{2})', text)
        if hora_match:
            hora, minuto = hora_match.groups()
            tiempo = f"{hora.zfill(2)}:{minuto}"
            fecha_contexto = self.extract_date_from_context(page_soup) if page_soup else "No especificado"
            return fecha_contexto, tiempo
        
        for pattern in DATE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                groups = match.groups()
                if len(groups) >= 2:
                    dia, mes = groups[0], groups[1]
                    if mes.lower() in MESES_ES:
                        fecha = f"{dia} de {mes}"
                        return fecha, "No especificado"
        
        fecha_contexto = self.extract_date_from_context(page_soup) if page_soup else "No especificado"
        return fecha_contexto, "No especificado"

    def extract_sport_and_competition(self, event_container, soup, page_num):
        """Función mejorada para extraer deporte y competición usando mapeo dinámico"""
        # Usar el mapeo dinámico extraído
        sport = self.sports_mapping.get(page_num, f"Deporte_{page_num}")
        
        competition = "No especificado"
        
        try:
            if event_container:
                event_text = event_container.get_text()
                
                competition_patterns = [
                    r'\(([^)]+)\)',
                    r'(\w+\.\s*\w+(?:\s+\w+)*)',
                    r'(Copa\s+[^,\n]+)',
                    r'(Liga\s+[^,\n]+)',
                    r'(Championship\s+[^,\n]+)',
                    r'(Premier\s+[^,\n]+)',
                    r'(Champions\s+[^,\n]+)',
                    r'(\w+\s+Division)',
                    r'(\w+\.\s*\w+)',
                ]
                
                for pattern in competition_patterns:
                    matches = re.findall(pattern, event_text, re.IGNORECASE)
                    if matches:
                        for match in matches:
                            if len(match.strip()) > 3 and not re.match(r'^\d+:\d+$', match.strip()):
                                competition = match.strip()
                                break
                        if competition != "No especificado":
                            break
            
        except Exception as e:
            logging.debug(f"Error al extraer deporte y competición: {e}")
        
        return sport, competition

    def extract_events_from_page(self, page_num):
        """Extrae eventos de una página específica usando el mapeo dinámico"""
        # Usar URL del mapeo dinámico si está disponible
        if page_num in self.sports_urls:
            url = self.sports_urls[page_num]
        else:
            url = f"{self.base_url}/es/allupcomingsports/{page_num}/"

        sport_name = self.sports_mapping.get(page_num, f"Deporte_{page_num}")
        logging.info(f"Procesando página {page_num} ({sport_name}): {url}")

        try:
            time.sleep(random.uniform(2, 4))

            response = self._make_request(url)
            if not response:
                logging.warning(f"No se pudo acceder a la página {page_num}")
                return []

            soup = BeautifulSoup(response.text, 'html.parser')
            events = []

            self.current_date_context = self.extract_date_from_context(soup)

            event_links = soup.find_all('a', href=re.compile(EVENT_PATH_REGEX))

            if not event_links:
                event_rows = soup.find_all('tr', class_=['evdesc', 'evdesc_LIVE'])
                for row in event_rows:
                    links = row.find_all('a', href=re.compile(EVENT_PATH_REGEX))
                    event_links.extend(links)

            for link in event_links:
                try:
                    href = link['href']
                    event_container = link.find_parent('tr') or link.parent

                    event_name = link.get_text(strip=True)
                    if not event_name or len(event_name) < 3:
                        continue

                    sport, competition = self.extract_sport_and_competition(event_container, soup, page_num)

                    date_time_text = ""

                    if event_container:
                        parent_row = event_container
                        all_text = parent_row.get_text()
                        date_time_text = all_text

                    fecha, hora = self.parse_date_time(date_time_text, soup)

                    full_url = urljoin(self.base_url, href)

                    event = {
                        "nombre": event_name,
                        "deporte": sport,
                        "competicion": competition,
                        "fecha": fecha,
                        "hora": hora,
                        "url": full_url
                    }

                    events.append(event)
                    logging.debug(f"Evento extraído: {event_name} - {sport} - {fecha} {hora}")

                except Exception as e:
                    logging.error(f"Error al procesar evento en página {page_num}: {e}")
                    continue

            logging.info(f"Extraídos {len(events)} eventos de la página {page_num} ({sport_name})")
            return events

        except Exception as e:
            logging.error(f"Error general en página {page_num}: {e}")
            return []

    def create_xml(self, events, output_file="eventos_livetv_sx.xml"):
        root = ET.Element("eventos")
        root.set("generado", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        root.set("total", str(len(events)))

        seen_urls = set()
        unique_count = 0

        for event in events:
            if event["url"] in seen_urls:
                continue

            seen_urls.add(event["url"])
            unique_count += 1

            evento_elem = ET.SubElement(root, "evento")

            nombre_elem = ET.SubElement(evento_elem, "nombre")
            nombre_elem.text = event["nombre"]

            deporte_elem = ET.SubElement(evento_elem, "deporte")
            deporte_elem.text = event["deporte"]

            competicion_elem = ET.SubElement(evento_elem, "competicion")
            competicion_elem.text = event["competicion"]

            fecha_elem = ET.SubElement(evento_elem, "fecha")
            fecha_elem.text = event["fecha"]

            hora_elem = ET.SubElement(evento_elem, "hora")
            hora_elem.text = event["hora"]

            url_elem = ET.SubElement(evento_elem, "url")
            url_elem.text = event["url"]

        root.set("total", str(unique_count))

        rough_string = ET.tostring(root, encoding="utf-8")
        reparsed = xml.dom.minidom.parseString(rough_string)
        pretty_xml = reparsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")

        with open(output_file, "w", encoding="utf-8") as file:
            file.write(pretty_xml)

        return unique_count

    def run(self):
        start_time = datetime.now()
        logging.info(f"Iniciando scraping de eventos deportivos en {start_time}")

        try:
            # Comprobar conectividad y seleccionar el mejor dominio/espejo
            self.find_working_base_url()

            # PASO 1: Extraer mapeo dinámico de deportes
            logging.info("🔍 Paso 1: Extrayendo mapeo dinámico de deportes...")
            if not self.extract_sports_mapping():
                logging.warning("⚠️ No se pudo extraer el mapeo completo de deportes, usando fallback")

            # Si después del fallback no hay deportes, salir
            if not self.sports_mapping:
                logging.error("❌ Error crítico: No se pudo extraer ningún deporte")
                return False

            logging.info(f"✅ Se detectaron {len(self.sports_mapping)} categorías de deportes")

            # PASO 2: Procesar páginas de deportes encontrados
            pages_to_process = list(self.sports_mapping.keys())
            if self.max_pages < len(pages_to_process):
                pages_to_process = pages_to_process[:self.max_pages]

            logging.info(f"🔍 Paso 2: Procesando {len(pages_to_process)} páginas de deportes...")

            # Procesar primera página para verificar conectividad
            if pages_to_process:
                test_events = self.extract_events_from_page(pages_to_process[0])
                if test_events:
                    self.all_events.extend(test_events)
                    logging.info("✅ Conexión verificada exitosamente")
                else:
                    logging.warning("⚠️ No se pudieron extraer eventos de la primera página. Continuando...")

                # Procesar el resto de páginas de forma secuencial para evitar bloqueos
                # En GitHub Actions, usar menos workers reduce la probabilidad de ser bloqueado
                if len(pages_to_process) > 1:
                    # Modo secuencial más seguro
                    for page_num in pages_to_process[1:]:
                        try:
                            page_events = self.extract_events_from_page(page_num)
                            self.all_events.extend(page_events)
                            # Delay mayor entre páginas en GitHub Actions
                            time.sleep(random.uniform(3, 5))
                        except Exception as exc:
                            logging.error(f"Página {page_num} generó una excepción: {exc}")

            logging.info(f"Total de eventos extraídos antes de eliminar duplicados: {len(self.all_events)}")

            if not self.all_events:
                logging.error("❌ No se extrajo ningún evento. Verifique la conectividad y la estructura del sitio web.")
                return False

            # PASO 3: Crear el archivo XML
            unique_count = self.create_xml(self.all_events)
            logging.info(f"✅ Total de eventos únicos guardados en XML: {unique_count}")

            # Mostrar resumen por deporte
            sport_summary = {}
            for event in self.all_events:
                sport = event["deporte"]
                sport_summary[sport] = sport_summary.get(sport, 0) + 1

            logging.info("📊 Resumen por deporte:")
            for sport, count in sorted(sport_summary.items()):
                logging.info(f"   {sport}: {count} eventos")

            end_time = datetime.now()
            duration = end_time - start_time
            logging.info(f"🎯 Proceso completado en {duration.total_seconds():.2f} segundos")
            logging.info(f"📈 Total de peticiones realizadas: {self.request_count}")
            return True

        except Exception as e:
            logging.error(f"💥 Error crítico durante la ejecución: {e}")
            return False

# Script principal
if __name__ == "__main__":
    try:
        import argparse
        parser = argparse.ArgumentParser(description='Scraper mejorado de eventos deportivos con extracción dinámica')
        parser.add_argument('--pages', type=int, default=20, help='Número máximo de páginas a procesar (default: 20)')
        parser.add_argument('--workers', type=int, default=3, help='Número máximo de trabajadores concurrentes (default: 3)')
        parser.add_argument('--output', type=str, default="eventos_livetv_sx.xml", help='Nombre del archivo XML de salida')
        parser.add_argument('--debug', action='store_true', help='Activar logging de debug')
        args = parser.parse_args()

        if args.debug:
            logging.getLogger().setLevel(logging.DEBUG)

        logging.info("🚀 Iniciando scraper con extracción dinámica de deportes...")

        # Crear y ejecutar el scraper
        scraper = EventScraper(max_pages=args.pages, max_workers=args.workers)
        success = scraper.run()

        if success:
            print(f"✅ Scraping completado exitosamente. Resultados guardados en {args.output}")
        else:
            print("❌ El scraping falló. Revise los logs para más detalles.")

        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        logging.info("⏹️ Proceso interrumpido por el usuario")
        sys.exit(1)
    except Exception as e:
        logging.critical(f"💥 Error fatal: {e}")
        sys.exit(1)
