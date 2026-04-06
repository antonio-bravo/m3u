import requests
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

BASE_URL = 'https://rojadirectaenvivohd.com'
DATA_URL = 'https://pltvhd.com/diaries.json'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
}

# Verificar si el sitio está disponible antes de proceder

def check_site_availability(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        return response.status_code == 200
    except requests.RequestException:
        return False

print(f"Verificando disponibilidad del sitio: {BASE_URL}")
if not check_site_availability(BASE_URL):
    print(f"Error: El sitio {BASE_URL} no está disponible o no responde.")
    print("Posibles causas:")
    print("- El sitio web está caído")
    print("- Problemas de conectividad")
    print("- El dominio ha cambiado")
    exit(1)

try:
    response = requests.get(DATA_URL, headers=HEADERS, timeout=20)
    response.raise_for_status()
    json_data = response.json()
except Exception as e:
    print(f"Error crítico: {e}")
    exit(1)

if not json_data or 'data' not in json_data:
    print("Error: No se recibieron datos válidos desde la API.")
    exit(1)

# Procesar el contenido

events = []
for entry in json_data['data']:
    attributes = entry.get('attributes', {})
    description = attributes.get('diary_description', '') or ''
    description = description.strip()
    if not description:
        continue

    if ':' in description:
        league, teams = description.split(':', 1)
    else:
        league, teams = description, ''

    league = league.strip()
    teams = ' '.join(teams.strip().split())

    if not teams:
        teams = league

    date = attributes.get('date_diary', '').strip()
    time_str = attributes.get('diary_hour', '').strip()
    if time_str and len(time_str) >= 5:
        time_str = time_str[:5]

    if not date or not time_str:
        print(f"Evento ignorado por fecha/hora inválida: {description}")
        continue

    embeds = (attributes.get('embeds') or {}).get('data') or []
    if not embeds:
        print(f"Evento sin embeds: {description}")
        continue

    event = {
        'datetime': f"{date} {time_str}",
        'league': league,
        'teams': teams,
        'channels': []
    }

    for idx, embed in enumerate(embeds, start=1):
        embed_attrs = embed.get('attributes', {})
        embed_name = (embed_attrs.get('embed_name') or '').strip()
        embed_iframe = (embed_attrs.get('embed_iframe') or '').strip()
        if not embed_name or not embed_iframe:
            continue

        event['channels'].append({
            'channel_name': embed_name,
            'channel_id': f"{entry.get('id')}-{idx}",
            'url': urljoin(BASE_URL, embed_iframe)
        })

    if event['channels']:
        events.append(event)

if not events:
    print("Error: No se encontraron eventos válidos en los datos recibidos.")
    exit(1)

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
