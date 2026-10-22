import requests
import xml.etree.ElementTree as ET
import re
# Carga bajo demanda del clasificador IA
classifier = None

def get_classifier():
    global classifier
    if classifier is None:
        print("Cargando modelo de IA para clasificar eventos no reconocidos...")
        from transformers import pipeline
        classifier = pipeline("zero-shot-classification", model="joeddav/xlm-roberta-large-xnli")
    return classifier

# Lista rica y variada de deportes en español e inglés
deportes = [
    # Español
    "fútbol", "fútbol sala", "baloncesto", "balonmano", "voleibol", "tenis", "pádel",
    "tenis de mesa", "ping pong", "bádminton", "squash", "béisbol", "softbol", "hockey",
    "hockey sobre hielo", "hockey sobre césped", "rugby", "rugby 7", "rugby league",
    "fútbol americano", "cricket", "golf", "atletismo", "maratón", "ultra maratón",
    "trail running", "natación", "waterpolo", "saltos", "nado sincronizado", "remo",
    "piragüismo", "canotaje", "surf", "windsurf", "vela", "esquí", "snowboard",
    "patinaje artístico", "patinaje velocidad", "ciclismo", "ciclismo en pista",
    "ciclismo de montaña", "mountain bike", "bmx", "motociclismo", "motoGP", "superbike",
    "automovilismo", "fórmula 1", "rally", "karting", "boxeo", "kickboxing", "muay thai",
    "mma", "judo", "karate", "taekwondo", "lucha libre", "lucha grecorromana",
    "halterofilia", "levantamiento de pesas", "powerlifting", "culturismo", "crossfit",
    "esgrima", "gimnasia artística", "gimnasia rítmica", "triatlón", "duatlón",
    "pentatlón", "biatlón", "escalada", "alpinismo", "paracaidismo", "tiro con arco",
    "tiro olímpico", "polo", "cróquet", "curling", "billar", "snooker", "dardos",
    "ajedrez", "eSports", "sumo", "petanca", "pesca deportiva", "boccia",
    "deportes adaptados", "skate", "parkour", "orientación", "canicross", "senderismo",
    "marchas", "caminar deportivo", "trineo", "bobsleigh", "luge", "skeleton",
    "netball", "floorball", "ultimate frisbee", "lacrosse", "softball",
    "campeonatos escolares", "campeonatos universitarios", "strongman", "rafting",
    "paintball", "airsoft", "motocross", "enduro", "trial", "wakeboard", "kitesurf",
    "bodyboard", "esquí acuático", "natación artística", "apnea", "salvamento y socorrismo",
    # Inglés
    "football", "soccer", "futsal", "basketball", "handball", "volleyball", "tennis", "padel",
    "table tennis", "ping pong", "badminton", "squash", "baseball", "softball", "hockey",
    "ice hockey", "field hockey", "rugby", "rugby sevens", "rugby league",
    "american football", "cricket", "golf", "athletics", "marathon", "ultra marathon",
    "trail running", "swimming", "water polo", "diving", "synchronized swimming", "rowing",
    "canoeing", "kayaking", "surfing", "windsurfing", "sailing", "skiing", "snowboarding",
    "figure skating", "speed skating", "cycling", "track cycling",
    "mountain biking", "bmx", "motorcycling", "motoGP", "superbike",
    "motorsport", "formula 1", "rally", "karting", "boxing", "kickboxing", "muay thai",
    "mma", "judo", "karate", "taekwondo", "wrestling", "greco-roman wrestling",
    "weightlifting", "powerlifting", "bodybuilding", "crossfit",
    "fencing", "artistic gymnastics", "rhythmic gymnastics", "triathlon", "duathlon",
    "pentathlon", "biathlon", "climbing", "mountaineering", "skydiving", "archery",
    "shooting", "polo", "croquet", "curling", "billiards", "snooker", "darts",
    "chess", "eSports", "sumo", "petanque", "sport fishing", "boccia",
    "adaptive sports", "skateboarding", "parkour", "orienteering", "canicross", "hiking",
    "walking", "race walking", "sled", "bobsleigh", "luge", "skeleton",
    "netball", "floorball", "ultimate frisbee", "lacrosse", "softball",
    "school championships", "university championships", "strongman", "rafting",
    "paintball", "airsoft", "motocross", "enduro", "trial", "wakeboarding", "kitesurfing",
    "bodyboarding", "waterskiing", "artistic swimming", "freediving", "lifesaving"
]

# URLs de listas a analizar
urls = [
    "https://raw.githubusercontent.com/tutw/platinsport-m3u-updater/main/lista.m3u",
    "https://raw.githubusercontent.com/tutw/platinsport-m3u-updater/main/lista_icastresana.m3u",
    "https://raw.githubusercontent.com/tutw/platinsport-m3u-updater/main/lista_sportsonlineci.xml",
    "https://raw.githubusercontent.com/tutw/platinsport-m3u-updater/main/lista_agenda_DEPORTE-LIBRE.FANS.xml"
]

# Diccionario de reglas directas para competiciones y deportes frecuentes
KEYWORDS = {
    'balonmano': [
        'ehf', 'balonmano', 'handball', 'liga asobal'
    ],
    'fórmula 1': [
        'f1', 'formula 1', 'fórmula 1', 'formula one', 'f1 academy'
    ],
    'motociclismo': [
        'motogp', 'moto2', 'moto3', 'superbike', 'motociclismo', 'supercross'
    ],
    'baloncesto': [
        'nba', 'euroliga', 'euroleague', 'eurocup', 'acb', 'baloncesto', 'basketball', 'basket', 'wnba', 'liga endesa'
    ],
    'tenis': [
        'atp', 'wta', 'roland garros', 'wimbledon', 'us open', 'australian open', 'tenis', 'tennis', 'davis cup', 'copa davis'
    ],
    'ciclismo': [
        'tour de francia', 'giro ditalia', 'giro de italia', 'vuelta a españa', 'ciclismo', 'cycling'
    ],
    'pádel': [
        'padel', 'pádel', 'premier padel'
    ],
    'mma / boxeo': [
        'ufc', 'mma', 'bellator', 'boxeo', 'boxing', 'fight night'
    ],
    'golf': [
        'pga', 'liv golf', 'ryder cup', 'golf', 'championship', 'ladies championship'
    ],
    'snooker / billar': [
        'abierto de irlanda del norte', 'snooker', 'billar'
    ],
    'fútbol americano': [
        'nfl', 'american football'
    ],
    'béisbol': [
        'mlb', 'béisbol', 'baseball'
    ],
    'hockey': [
        'nhl', 'hockey sobre hielo', 'ice hockey'
    ],
    'rugby': [
        'rugby', 'six nations', 'seis naciones'
    ],
    'fútbol': [
        'fifa', 'champions league', 'champions', 'europa league', 'conference', 'laliga', 'la liga',
        'premier league', 'serie a', 'bundesliga', 'ligue 1', 'copa del rey', 'fútbol', 'futbol',
        'soccer', 'fc ', 'cf ', 'calcio', 'eredivisie', 'libertadores', 'sudamericana', 'mls', 'fa cup',
        'conmebol', 'concacaf', 'uefa'
    ]
}

def detect_by_keyword(text):
    t = text.lower()
    for sport, words in KEYWORDS.items():
        for w in words:
            if re.search(r'(?i)\b' + re.escape(w) + r'\b', t):
                return sport
    return None

cache_deportes = {}

def detectar_deporte_ia(nombre_evento):
    if not nombre_evento:
        return "desconocido"

    if nombre_evento in cache_deportes:
        return cache_deportes[nombre_evento]

    # 1. Reglas directas por palabras clave (ultrarrápidas e instantáneas)
    deporte_directo = detect_by_keyword(nombre_evento)
    if deporte_directo:
        cache_deportes[nombre_evento] = deporte_directo
        return deporte_directo

    # 2. Si no coincide con ninguna palabra clave, usar IA zero-shot
    try:
        clf = get_classifier()
        resultado = clf(nombre_evento, deportes)
        deporte_ia = resultado["labels"][0]
        cache_deportes[nombre_evento] = deporte_ia
        return deporte_ia
    except Exception as e:
        print(f"Error clasificando con IA '{nombre_evento}': {e}")
        cache_deportes[nombre_evento] = "desconocido"
        return "desconocido"

def parse_m3u(content):
    eventos = []
    for line in content.splitlines():
        if line.startswith("#EXTINF"):
            m = re.search(r',(.+)$', line)
            if m:
                nombre_evento = m.group(1).strip()
                eventos.append(nombre_evento)
    return eventos

def parse_xml(content):
    eventos = []
    try:
        root = ET.fromstring(content)
        for channel in root.findall(".//channel"):
            nombre_evento = None
            for tag in ["display-name", "name"]:
                elem = channel.find(tag)
                if elem is not None and elem.text:
                    nombre_evento = elem.text.strip()
                    break
            if nombre_evento:
                eventos.append(nombre_evento)
    except Exception:
        pass
    return eventos

resultados = []

for url in urls:
    print(f"Procesando {url}")
    r = requests.get(url)
    if r.status_code == 200:
        content = r.text
        if url.endswith('.m3u'):
            eventos = parse_m3u(content)
        elif url.endswith('.xml'):
            eventos = parse_xml(content)
        else:
            continue
        for evento in eventos:
            deporte = detectar_deporte_ia(evento)
            print(f"Evento: {evento} | Deporte: {deporte}")
            resultados.append((evento, deporte))
    else:
        print(f"No se pudo obtener {url}")

# Crea el XML de salida
root = ET.Element("eventos")
for nombre, deporte in resultados:
    evento_elem = ET.SubElement(root, "evento")
    ET.SubElement(evento_elem, "nombre").text = nombre
    ET.SubElement(evento_elem, "deporte").text = deporte

tree = ET.ElementTree(root)
tree.write("deportes-detectados.xml", encoding="utf-8", xml_declaration=True)
print("Archivo deportes-detectados.xml generado correctamente.")
