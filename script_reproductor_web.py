import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime
import xml.etree.ElementTree as ET
import sys

BASE_URLS = [
    'https://pirlotv.la',
    'https://www.pirlotv.la',
    'https://pirlotv.fr',
    'https://pirlotv.me',
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
}

def safe_get(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=12)
        if response.status_code == 200 and 'SEIZED' not in response.text and 'Seized' not in response.text:
            return response
    except requests.RequestException:
        pass
    return None

def fetch_events_from_pirlotv(base_url):
    response = safe_get(base_url)
    if not response:
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    events = []
    today_str = datetime.now().strftime('%Y-%m-%d')

    menu = soup.find('ul', class_='menu')
    if not menu:
        return []

    items = menu.find_all('li', recursive=False)
    for idx, li in enumerate(items, start=1):
        event_link = li.find('a', recursive=False)
        if not event_link:
            continue

        time_span = event_link.find('span', class_='t')
        time_str = time_span.get_text(strip=True) if time_span else ''
        if time_span:
            time_span.decompose()

        full_title = event_link.get_text(strip=True)
        full_title = ' '.join(full_title.split())

        if not full_title:
            continue

        if ':' in full_title:
            league, teams = full_title.split(':', 1)
        else:
            league, teams = full_title, full_title

        league = league.strip()
        teams = teams.strip()

        channels = []
        sub_ul = li.find('ul')
        if sub_ul:
            for c_idx, sub_li in enumerate(sub_ul.find_all('li'), start=1):
                ch_a = sub_li.find('a')
                if ch_a and ch_a.get('href'):
                    ch_name = ch_a.get_text(strip=True)
                    ch_href = ch_a.get('href')
                    channels.append({
                        'channel_name': ch_name,
                        'channel_id': f"{idx}-{c_idx}",
                        'url': urljoin(base_url, ch_href)
                    })

        if channels:
            events.append({
                'datetime': f"{today_str} {time_str}".strip(),
                'league': league,
                'teams': teams,
                'channels': channels
            })

    return events

print("Verificando fuentes disponibles...")
events = []
working_base_url = None

for base_url in BASE_URLS:
    print(f"Probando: {base_url}")
    events = fetch_events_from_pirlotv(base_url)
    if events:
        working_base_url = base_url
        print(f"Éxito: {len(events)} eventos encontrados en {base_url}")
        break

if not events:
    print("Error crítico: No se encontraron eventos válidos en ninguna de las fuentes probadas.")
    sys.exit(1)

# Generar XML

root = ET.Element('events')
for event in events:
    event_elem = ET.SubElement(root, 'event')
    ET.SubElement(event_elem, 'datetime').text = event['datetime']
    ET.SubElement(event_elem, 'league').text = event['league']
    ET.SubElement(event_elem, 'teams').text = event['teams']

    channels_elem = ET.SubElement(event_elem, 'channels')
    for channel in event['channels']:
        channel_elem = ET.SubElement(channels_elem, 'channel')
        ET.SubElement(channel_elem, 'channel_name').text = channel['channel_name']
        ET.SubElement(channel_elem, 'channel_id').text = str(channel['channel_id'])
        ET.SubElement(channel_elem, 'url').text = channel['url']


def indent(elem, level=0):
    i = "\n" + "  " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for subelem in elem:
            indent(subelem, level + 1)
        if not subelem.tail or not elem.tail.strip():
            subelem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i

indent(root)
tree = ET.ElementTree(root)
tree.write('lista_reproductor_web.xml', encoding='utf-8', xml_declaration=True)

# Generar M3U
with open('lista_reproductor_web.m3u', 'w', encoding='utf-8') as f:
    f.write('#EXTM3U\n')
    for event in events:
        for channel in event['channels']:
            f.write(
                f'#EXTINF:-1,{event["datetime"]} - {event["league"]} - {event["teams"]} - {channel["channel_name"]}\n'
                f'{channel["url"]}\n'
            )

print("Archivos lista_reproductor_web.xml y lista_reproductor_web.m3u actualizados correctamente.")
