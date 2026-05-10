import sys
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from xml.dom import minidom
import difflib
from urllib.parse import urljoin

BASE_URLS = [
    'https://deporte-libre.click',
    'https://www.deporte-libre.click',
    'https://deportelibre.click',
    'https://www.deportelibre.click',
    'https://deporte-libre.com',
    'https://www.deporte-libre.com',
    'http://deportelibre.com',
    'http://deporte-libre.com',
]
main_path = '/canales-24-7.php'
logos_url = 'https://raw.githubusercontent.com/tutw/platinsport-m3u-updater/refs/heads/main/logos.xml'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}

# Función interna para hacer requests seguros
def safe_get(url):
    try:
        response = requests.get(url, timeout=12, headers=HEADERS)
        response.raise_for_status()
        return response
    except requests.exceptions.SSLError:
        try:
            response = requests.get(url, timeout=12, headers=HEADERS, verify=False)
            response.raise_for_status()
            return response
        except Exception as err:
            print(f"Warning: SSL error for {url}: {err}")
            return None
    except requests.RequestException as err:
        print(f"Warning: no se pudo cargar {url}: {err}")
        return None

# Función para obtener el contenido HTML de una URL
def get_html(url):
    print(f"Fetching URL: {url}")
    response = safe_get(url)
    if not response:
        raise requests.RequestException(f"No se pudo obtener {url}")
    return response.text

def find_working_base_url():
    for base_url in BASE_URLS:
        try:
            test_url = urljoin(base_url, main_path)
            html = get_html(test_url)
            if '/stream/' in html:
                return base_url
        except Exception:
            continue
    return None

# Función para scrapear la página principal y obtener los nombres de los canales y sus URLs
def get_channel_list(base_url):
    html = get_html(urljoin(base_url, main_path))
    soup = BeautifulSoup(html, 'html.parser')
    
    channel_list = []
    for a_tag in soup.find_all('a'):
        channel_name = a_tag.text.strip()
        channel_url = a_tag.get('href')
        if channel_name and channel_url and channel_url.startswith('/stream/'):
            channel_list.append((channel_name, urljoin(base_url, channel_url)))
    
    print(f"Found {len(channel_list)} channels")
    return channel_list

# Función para obtener los enlaces de streaming de cada canal
def get_streaming_urls(channel_url, base_url):
    html = get_html(channel_url)
    soup = BeautifulSoup(html, 'html.parser')
    
    streaming_urls = []
    for a_tag in soup.find_all('a', {'class': 'btn btn-md'}):
        streaming_url = a_tag.get('href')
        if streaming_url and streaming_url.startswith('/'):
            streaming_url = urljoin(base_url, streaming_url)
        if streaming_url:
            streaming_urls.append(streaming_url)
    
    # También buscar URLs en los iframes
    for iframe in soup.find_all('iframe'):
        iframe_url = iframe.get('src')
        if iframe_url:
            streaming_urls.append(iframe_url)
    
    return streaming_urls

# Función para cargar los logos desde logos.xml
def load_logos(logos_url):
    logos_xml = get_html(logos_url)
    root = ET.fromstring(logos_xml)
    logos = {}
    for logo in root.findall('logo'):
        name = logo.find('name').text
        url = logo.find('url').text
        logos[name] = url
    return logos

# Función para encontrar el logo más parecido al nombre del canal
def find_logo(channel_name, logos):
    closest_matches = difflib.get_close_matches(channel_name.lower(), logos.keys(), n=1, cutoff=0.6)
    if closest_matches:
        return logos[closest_matches[0]]
    return None

# Función para guardar los resultados en un archivo XML
def save_to_xml(channel_data, output_path):
    root = ET.Element('channels')
    for channel_name, data in channel_data.items():
        channel_element = ET.SubElement(root, 'channel', name=channel_name)
        for url in data['urls']:
            ET.SubElement(channel_element, 'url').text = url
        if data.get('logo'):
            ET.SubElement(channel_element, 'logo').text = data['logo']
    
    # Convertir el árbol XML a una cadena
    xml_str = ET.tostring(root, encoding='utf-8')
    # "Prettify" la cadena XML
    parsed_xml = minidom.parseString(xml_str)
    pretty_xml_str = parsed_xml.toprettyxml(indent="  ")

    # Escribir la cadena "prettify" en el archivo
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(pretty_xml_str)

# Scrapeamos la lista de canales
print("Starting to scrape the channel list")
working_base_url = find_working_base_url()
if not working_base_url:
    print("No se encontró un host DEPORTE-LIBRE válido. Saltando actualización de canales.")
    sys.exit(0)
channel_list = get_channel_list(working_base_url)

# Cargamos los logos
logos = load_logos(logos_url)

# Obtenemos los enlaces de streaming para cada canal y el logo correspondiente
channel_data = {}
for channel_name, channel_url in channel_list:
    try:
        streaming_urls = get_streaming_urls(channel_url, working_base_url)
        logo_url = find_logo(channel_name, logos)
        channel_data[channel_name] = {'urls': streaming_urls, 'logo': logo_url}
    except requests.RequestException as e:
        print(f"Error fetching streaming URLs for channel {channel_name}: {e}")

# Guardamos los resultados en un archivo XML con un nombre fijo
output_path = 'lista_canales_DEPORTE-LIBRE.FANS.xml'
save_to_xml(channel_data, output_path)

print(f'Resultados guardados en {output_path}')
