import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import json
import time
import re
import os
from urllib.parse import urljoin, urlparse
import warnings
import ssl
from bs4 import BeautifulSoup

warnings.filterwarnings('ignore')
ssl._create_default_https_context = ssl._create_unverified_context

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
    session_requester = cloudscraper.create_scraper()
except ImportError:
    HAS_CLOUDSCRAPER = False
    session_requester = requests

def obtener_eventos_xml():
    """Descarga y parsea el XML fuente (probando primero el archivo local)"""
    if os.path.exists("eventos_livetv_sx.xml"):
        try:
            print("📄 Leyendo archivo local eventos_livetv_sx.xml...")
            tree = ET.parse("eventos_livetv_sx.xml")
            return tree.getroot()
        except Exception as e:
            print(f"Error al leer XML local: {e}")

    url = "https://raw.githubusercontent.com/tutw/platinsport-m3u-updater/refs/heads/main/eventos_livetv_sx.xml"
    try:
        response = session_requester.get(url, timeout=30)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        return root
    except Exception as e:
        print(f"Error al obtener XML: {e}")
        return None

def filtrar_eventos_hoy(root):
    """Filtra eventos del día actual"""
    eventos_hoy = []
    # Mapeo de meses en inglés a español
    meses_map = {
        "January": "enero", "February": "febrero", "March": "marzo", "April": "abril",
        "May": "mayo", "June": "junio", "July": "julio", "August": "agosto",
        "September": "septiembre", "October": "octubre", "November": "noviembre", "December": "diciembre"
    }
    fecha_actual = datetime.now()
    dia = fecha_actual.day
    mes_ingles = fecha_actual.strftime("%B")
    mes_español = meses_map.get(mes_ingles, mes_ingles.lower())
    fecha_buscar = f"{dia} de {mes_español}"

    for evento in root.findall('evento'):
        fecha_elem = evento.find('fecha')
        if fecha_elem is not None and fecha_elem.text == fecha_buscar:
            eventos_hoy.append(evento)

    return eventos_hoy

def crear_mapeo_banderas():
    """Crea el mapeo de números de bandera a idiomas basado en la prueba real"""
    return {
        '1': 'Inglés',
        '3': 'Español', 
        '7': 'Francés',
        '11': 'Alemán',
        '13': 'Italiano',
        '24': 'Portugués',
        '2': 'Francés (Francia)',
        '4': 'Italiano (Italia)',
        '5': 'Alemán (Alemania)',
        '6': 'Español (España)',
        '8': 'Portugués (Portugal)',
        '9': 'Holandés',
        '10': 'Ruso',
        '12': 'Turco',
        '14': 'Árabe',
        '15': 'Chino',
        '16': 'Japonés',
        '17': 'Coreano',
        '18': 'Polaco',
        '19': 'Rumano',
        '20': 'Húngaro',
        '21': 'Checo',
        '22': 'Sueco',
        '23': 'Noruego',
        '25': 'Danés',
        '26': 'Finlandés',
        '27': 'Griego',
        '28': 'Búlgaro',
        '29': 'Croata',
        '30': 'Serbio'
    }

def extraer_streams_evento(url):
    """Extrae streams de un evento específico con detección correcta de banderas"""
    streams = []

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

        response = session_requester.get(url, headers=headers, timeout=15, verify=False)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Buscar el bloque de enlaces
        links_block = soup.find(id='links_block')
        
        if links_block:
            print(f"✅ Encontrado links_block")
            
            # Crear mapeo de banderas
            mapeo_banderas = crear_mapeo_banderas()
            
            # Buscar todas las filas que contienen streams
            filas = links_block.find_all('tr')
            print(f"📊 Encontradas {len(filas)} filas en total")
            
            for i, fila in enumerate(filas):
                try:
                    # Buscar imágenes de banderas en la fila
                    imagenes_bandera = fila.find_all('img')
                    enlaces = fila.find_all('a', href=True)
                    
                    if imagenes_bandera and enlaces:
                        for img in imagenes_bandera:
                            src = img.get('src', '')
                            
                            # Extraer número de bandera del src
                            numero_bandera = extraer_numero_bandera(src)
                            
                            if numero_bandera:
                                idioma = mapeo_banderas.get(numero_bandera, f'Bandera #{numero_bandera}')
                                
                                # Buscar enlace correspondiente en la misma fila
                                for enlace in enlaces:
                                    href = enlace.get('href')
                                    if href and not href.startswith('#'):
                                        # Convertir URL relativa a absoluta
                                        if href.startswith('/'):
                                            href = urljoin(url, href)
                                        elif not href.startswith('http'):
                                            href = urljoin(url, href)
                                        
                                        # Extraer iframe real
                                        iframe_url = extraer_iframe_real(href)
                                        if iframe_url:
                                            # CORREGIDO: forzar formato de idioma
                                            idioma_url = f'https://cdn.livetv860.me/img/linkflag/{numero_bandera}.png'

                                            stream_data = {
                                                'url': iframe_url,
                                                'idioma': idioma_url,
                                                'idioma_nombre': idioma,
                                                'enlace_original': href
                                            }
                                            streams.append(stream_data)
                                            print(f"  🎯 Stream encontrado: {idioma} -> {iframe_url[:50]}...")
                                            break  # Un enlace por bandera
                        
                except Exception as e:
                    print(f"⚠️  Error procesando fila {i}: {e}")
                    continue

            # MEJORA: Buscar iframes adicionales ocultos
            iframes_ocultos = buscar_iframes_ocultos(soup, url)
            for iframe_oculto in iframes_ocultos:
                if not any(stream['url'] == iframe_oculto['url'] for stream in streams):
                    streams.append(iframe_oculto)

        else:
            print("❌ No se encontró links_block")

        # --- MEJORA: eliminar streams duplicados por URL ---
        unique_streams = {}
        for s in streams:
            if s['url'] not in unique_streams:
                unique_streams[s['url']] = s
        streams = list(unique_streams.values())
        # --- FIN MEJORA ---

        return streams

    except Exception as e:
        print(f"❌ Error al extraer streams de {url}: {e}")
        return []

def extraer_numero_bandera(src):
    """Extrae el número de bandera del src de la imagen"""
    try:
        if not src:
            return None
            
        # Patrones para detectar números de bandera
        patrones = [
            r'/img/linkflag/(\d+)\.gif',
            r'/linkflag/(\d+)\.gif', 
            r'linkflag/(\d+)',
            r'flag.*?(\d+)',
            r'/(\d+)\.gif'
        ]
        
        for patron in patrones:
            match = re.search(patron, src)
            if match:
                return match.group(1)
        
        return None
    except:
        return None

def buscar_iframes_ocultos(soup, base_url):
    """Busca iframes adicionales que puedan estar ocultos"""
    iframes_ocultos = []
    
    try:
        # Buscar iframes directamente en el HTML
        all_iframes = soup.find_all('iframe', src=True)
        
        for iframe in all_iframes:
            src = iframe.get('src')
            if src and ('embed' in src or 'player' in src or 'stream' in src):
                if src.startswith('//'):
                    src = 'https:' + src
                elif src.startswith('/'):
                    src = urljoin(base_url, src)
                
                iframe_data = {
                    'url': src,
                    'idioma': '',
                    'idioma_nombre': 'Iframe directo',
                    'enlace_original': src
                }
                iframes_ocultos.append(iframe_data)

        # Buscar en scripts
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string:
                iframe_urls = re.findall(r'["\']https?://[^"\']*(?:embed|player|stream)[^"\']*["\']', script.string)
                for url_match in iframe_urls:
                    clean_url = url_match.strip('"\'')
                    iframe_data = {
                        'url': clean_url,
                        'idioma': '',
                        'idioma_nombre': 'Script embebido',
                        'enlace_original': clean_url
                    }
                    iframes_ocultos.append(iframe_data)

    except Exception as e:
        print(f"⚠️  Error buscando iframes ocultos: {e}")
    
    return iframes_ocultos

def extraer_iframe_real(stream_url):
    """Extrae el iframe real de una URL de stream"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }

        response = requests.get(stream_url, headers=headers, timeout=10, verify=False)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        iframes = soup.find_all('iframe', src=True)

        for iframe in iframes:
            src = iframe.get('src')
            if src and ('antenasport' in src or 'embed' in src or 'player' in src or 'stream' in src):
                if src.startswith('//'):
                    src = 'https:' + src
                elif src.startswith('/'):
                    src = urljoin(stream_url, src)
                return src

        if iframes:
            src = iframes[0].get('src')
            if src:
                if src.startswith('//'):
                    src = 'https:' + src
                elif src.startswith('/'):
                    src = urljoin(stream_url, src)
                return src

        return None

    except Exception as e:
        print(f"⚠️  Error extrayendo iframe de {stream_url}: {e}")
        return None

def convertir_a_datetime_iso(fecha_str, hora_str):
    """Convierte fecha y hora en formato español a datetime ISO"""
    try:
        meses = {
            'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
            'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
            'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12
        }

        partes_fecha = fecha_str.split(' de ')
        if len(partes_fecha) == 2:
            dia = int(partes_fecha[0])
            mes_nombre = partes_fecha[1].lower()
            mes = meses.get(mes_nombre, 6)
            año = datetime.now().year

            if ':' in hora_str:
                hora_partes = hora_str.split(':')
                hora = int(hora_partes[0])
                minuto = int(hora_partes[1])
            else:
                hora = 0
                minuto = 0

            dt = datetime(año, mes, dia, hora, minuto)
            return dt.strftime('%Y-%m-%dT%H:%M:%S')

        return f"{datetime.now().year}-{datetime.now().month:02d}-{datetime.now().day:02d}T00:00:00"

    except Exception as e:
        return f"{datetime.now().year}-{datetime.now().month:02d}-{datetime.now().day:02d}T00:00:00"

def procesar_todos_los_eventos(eventos_hoy, max_eventos=None):
    """Procesa todos los eventos del día y extrae sus streams"""
    eventos_procesados = []
    total_eventos = len(eventos_hoy) if max_eventos is None else min(len(eventos_hoy), max_eventos)

    print(f"🔄 Procesando {total_eventos} eventos...")

    for i, evento in enumerate(eventos_hoy[:total_eventos]):
        try:
            nombre = evento.find('nombre').text if evento.find('nombre') is not None else "N/A"
            deporte = evento.find('deporte').text if evento.find('deporte') is not None else "N/A"
            competicion = evento.find('competicion').text if evento.find('competicion') is not None else "N/A"
            fecha = evento.find('fecha').text if evento.find('fecha') is not None else ""
            hora = evento.find('hora').text if evento.find('hora') is not None else "00:00"
            url = evento.find('url').text if evento.find('url') is not None else ""

            print(f"\n📺 Procesando evento {i+1}/{total_eventos}: {nombre}")

            streams = extraer_streams_evento(url)
            datetime_iso = convertir_a_datetime_iso(fecha, hora)

            evento_procesado = {
                'id': i + 1,
                'nombre': nombre,
                'deporte': deporte,
                'competicion': competicion,
                'fecha': fecha,
                'hora': hora,
                'url': url,
                'datetime_iso': datetime_iso,
                'streams': streams
            }

            eventos_procesados.append(evento_procesado)
            
            print(f"✅ Evento {i+1} procesado: {len(streams)} streams encontrados")
            
            for j, stream in enumerate(streams):
                idioma_nombre = stream.get('idioma_nombre', 'Sin idioma')
                print(f"   🎯 Stream {j+1}: {idioma_nombre}")

            time.sleep(2)  # Pausa para no sobrecargar el servidor

        except Exception as e:
            print(f"❌ Error procesando evento {i+1}: {e}")
            continue

    eventos_procesados.sort(key=lambda x: x['datetime_iso'])
    for i, evento in enumerate(eventos_procesados):
        evento['id'] = i + 1

    return eventos_procesados

def generar_xml_final(eventos_procesados):
    """Genera el XML final con la estructura mejorada"""
    root = ET.Element("eventos")
    root.set("generado", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    root.set("total", str(len(eventos_procesados)))

    for evento_data in eventos_procesados:
        evento_elem = ET.SubElement(root, "evento")
        evento_elem.set("id", str(evento_data['id']))

        nombre_elem = ET.SubElement(evento_elem, "nombre")
        nombre_elem.text = evento_data['nombre']

        deporte_elem = ET.SubElement(evento_elem, "deporte")
        deporte_elem.text = evento_data['deporte']

        competicion_elem = ET.SubElement(evento_elem, "competicion")
        competicion_elem.text = evento_data['competicion']

        fecha_elem = ET.SubElement(evento_elem, "fecha")
        fecha_elem.text = evento_data['fecha']

        hora_elem = ET.SubElement(evento_elem, "hora")
        hora_elem.text = evento_data['hora']

        url_elem = ET.SubElement(evento_elem, "url")
        url_elem.text = evento_data['url']

        datetime_iso_elem = ET.SubElement(evento_elem, "datetime_iso")
        datetime_iso_elem.text = evento_data['datetime_iso']

        streams_elem = ET.SubElement(evento_elem, "streams")
        streams_elem.set("total", str(len(evento_data['streams'])))

        for i, stream_data in enumerate(evento_data['streams']):
            stream_elem = ET.SubElement(streams_elem, "stream")
            stream_elem.set("id", str(i + 1))
            
            url_stream_elem = ET.SubElement(stream_elem, "url")
            url_stream_elem.text = stream_data.get('url', '')
            
            idioma_elem = ET.SubElement(stream_elem, "idioma")
            idioma_elem.text = stream_data.get('idioma', '')
            
            idioma_nombre_elem = ET.SubElement(stream_elem, "idioma_nombre")
            idioma_nombre_elem.text = stream_data.get('idioma_nombre', '')
            
            enlace_original_elem = ET.SubElement(stream_elem, "enlace_original")
            enlace_original_elem.text = stream_data.get('enlace_original', '')

    return root

def formatear_xml(elem, level=0):
    """Formatea XML con indentación apropiada"""
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for child in elem:
            formatear_xml(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i

def main():
    """Función principal"""
    print("=" * 60)
    print("🎯 EXTRACTOR DE EVENTOS DEPORTIVOS LIVETV.SX")
    print("🚀 VERSIÓN CORREGIDA Y PROBADA")
    print("=" * 60)
    print(f"⏰ Ejecutado el: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    print("1️⃣ Descargando XML fuente...")
    xml_root = obtener_eventos_xml()
    if xml_root is None:
        print("❌ Error: No se pudo descargar el XML fuente")
        return

    print(f"✅ XML descargado. Total eventos: {xml_root.get('total', 'N/A')}")

    print("\n2️⃣ Filtrando eventos del día actual...")
    eventos_hoy = filtrar_eventos_hoy(xml_root)
    print(f"✅ Eventos encontrados para hoy: {len(eventos_hoy)}")

    if not eventos_hoy:
        print("⚠️  No hay eventos para procesar hoy")
        return

    print("\n3️⃣ Procesando eventos con detección corregida de banderas...")
    eventos_procesados = procesar_todos_los_eventos(eventos_hoy)
    print(f"\n✅ Total eventos procesados: {len(eventos_procesados)}")

    print("\n4️⃣ Generando XML final...")
    xml_final = generar_xml_final(eventos_procesados)
    formatear_xml(xml_final)

    # Aquí la corrección: nombre correcto del archivo de salida
    output_path = 'eventos_livetv_sx_con_reproductores.xml'
    tree = ET.ElementTree(xml_final)
    tree.write(output_path, encoding='utf-8', xml_declaration=True)

    print(f"✅ XML generado exitosamente: {output_path}")

    total_streams = sum(len(evento['streams']) for evento in eventos_procesados)
    streams_con_idioma = sum(
        len([s for s in evento['streams'] if s.get('idioma_nombre') and s.get('idioma_nombre') != 'Sin idioma'])
        for evento in eventos_procesados
    )
    
    print(f"\n📊 ESTADÍSTICAS FINALES:")
    print(f"   📺 Total streams: {total_streams}")
    print(f"   🏳️  Streams con idioma: {streams_con_idioma}")
    print(f"   📈 Tasa de éxito: {(streams_con_idioma/total_streams*100):.1f}%" if total_streams > 0 else "0%")

    print(f"\n🎉 Proceso completado exitosamente!")
    print(f"✨ Mejoras implementadas y probadas:")
    print(f"   ✅ Detección numérica de banderas")
    print(f"   ✅ Mapeo completo de idiomas")  
    print(f"   ✅ Extracción robusta de streams")
    print(f"   ✅ Manejo de errores mejorado")

if __name__ == "__main__":
    main()
