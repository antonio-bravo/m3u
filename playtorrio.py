#!/usr/bin/env python3
"""
PlayTorrio Sports Events M3U Updater - VERSIÓN CON EXTRACCIÓN DE LIGA
Extrae TODOS los eventos deportivos con logos, nombres de canal, países Y LIGA/COMPETICIÓN
"""
import asyncio
import aiohttp
import json
import re
from datetime import datetime, timezone
from typing import List, Dict
import pytz

# APIs de PlayTorrio
PLAYTORRIO_WORKER = 'https://ntvstream-scraper.aymanisthedude1.workers.dev'
CDNLIVE_API = f'{PLAYTORRIO_WORKER}/cdnlive'
MATCHES_API = f'{PLAYTORRIO_WORKER}/matches'

# Headers EXACTOS para que funcione la API de matches
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Referer': 'https://iptv.playtorrio.xyz/',
    'Origin': 'https://iptv.playtorrio.xyz',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'cross-site',
}

# Mapeo de códigos de país a nombres
COUNTRY_NAMES = {
    'us': '🇺🇸 USA',
    'gb': '🇬🇧 UK',
    'ca': '🇨🇦 Canada',
    'au': '🇦🇺 Australia',
    'nz': '🇳🇿 New Zealand',
    'za': '🇿🇦 South Africa',
    'fr': '🇫🇷 France',
    'de': '🇩🇪 Germany',
    'es': '🇪🇸 Spain',
    'it': '🇮🇹 Italy',
    'pt': '🇵🇹 Portugal',
    'nl': '🇳🇱 Netherlands',
    'be': '🇧🇪 Belgium',
    'ch': '🇨🇭 Switzerland',
    'at': '🇦🇹 Austria',
    'se': '🇸🇪 Sweden',
    'no': '🇳🇴 Norway',
    'dk': '🇩🇰 Denmark',
    'fi': '🇫🇮 Finland',
    'pl': '🇵🇱 Poland',
    'cz': '🇨🇿 Czech Republic',
    'sk': '🇸🇰 Slovakia',
    'hu': '🇭🇺 Hungary',
    'ro': '🇷🇴 Romania',
    'bg': '🇧🇬 Bulgaria',
    'gr': '🇬🇷 Greece',
    'tr': '🇹🇷 Turkey',
    'ru': '🇷🇺 Russia',
    'ua': '🇺🇦 Ukraine',
    'rs': '🇷🇸 Serbia',
    'hr': '🇭🇷 Croatia',
    'si': '🇸🇮 Slovenia',
    'ba': '🇧🇦 Bosnia',
    'me': '🇲🇪 Montenegro',
    'mk': '🇲🇰 North Macedonia',
    'al': '🇦🇱 Albania',
    'br': '🇧🇷 Brazil',
    'ar': '🇦🇷 Argentina',
    'mx': '🇲🇽 Mexico',
    'cl': '🇨🇱 Chile',
    'co': '🇨🇴 Colombia',
    'pe': '🇵🇪 Peru',
    'uy': '🇺🇾 Uruguay',
    've': '🇻🇪 Venezuela',
    'ec': '🇪🇨 Ecuador',
    'sa': '🇸🇦 Saudi Arabia',
    'ae': '🇦🇪 UAE',
    'qa': '🇶🇦 Qatar',
    'kw': '🇰🇼 Kuwait',
    'om': '🇴🇲 Oman',
    'bh': '🇧🇭 Bahrain',
    'il': '🇮🇱 Israel',
    'eg': '🇪🇬 Egypt',
    'ma': '🇲🇦 Morocco',
    'tn': '🇹🇳 Tunisia',
    'dz': '🇩🇿 Algeria',
    'in': '🇮🇳 India',
    'pk': '🇵🇰 Pakistan',
    'bd': '🇧🇩 Bangladesh',
    'lk': '🇱🇰 Sri Lanka',
    'np': '🇳🇵 Nepal',
    'th': '🇹🇭 Thailand',
    'vn': '🇻🇳 Vietnam',
    'ph': '🇵🇭 Philippines',
    'id': '🇮🇩 Indonesia',
    'my': '🇲🇾 Malaysia',
    'sg': '🇸🇬 Singapore',
    'jp': '🇯🇵 Japan',
    'kr': '🇰🇷 South Korea',
    'cn': '🇨🇳 China',
    'hk': '🇭🇰 Hong Kong',
    'tw': '🇹🇼 Taiwan',
}

class PlayTorrioEventsExtractor:
    def __init__(self):
        self.events = []
        self.session = None
        self.request_count = 0
    
    async def init_session(self):
        """Inicializar sesión HTTP"""
        connector = aiohttp.TCPConnector(limit=5)
        timeout = aiohttp.ClientTimeout(total=60)
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers=HEADERS
        )
    
    async def close_session(self):
        """Cerrar sesión HTTP"""
        if self.session:
            await self.session.close()
    
    async def fetch_with_retry(self, url: str, max_retries: int = 3) -> dict:
        """Fetch con reintentos y delay progresivo"""
        self.request_count += 1
        
        # Delay progresivo para evitar rate limiting
        if self.request_count > 1:
            delay = min(10 + (self.request_count * 2), 30)
            print(f"⏳ Delay de {delay}s para evitar rate limiting...")
            await asyncio.sleep(delay)
        
        for attempt in range(max_retries):
            try:
                async with self.session.get(url) as response:
                    if response.status == 429:
                        wait_time = 15 + (attempt * 5)
                        print(f"⏳ Rate limit - esperando {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"❌ HTTP {response.status} para {url}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(5)
            except Exception as e:
                print(f"⚠️  Error en intento {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
        return {}
    
    def timestamp_to_spain_time(self, timestamp: int) -> str:
        """Convertir timestamp de milisegundos a hora de España"""
        try:
            dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
            spain_tz = pytz.timezone('Europe/Madrid')
            spain_time = dt.astimezone(spain_tz)
            return spain_time.strftime('%H:%M')
        except:
            return "00:00"
    
    def get_country_name(self, country_code: str) -> str:
        """Obtener nombre del país desde el código"""
        if not country_code:
            return "Internacional"
        return COUNTRY_NAMES.get(country_code.lower(), country_code.upper())
    
    def get_logo_url(self, item: dict) -> str:
        """Extraer URL del logo del evento"""
        # 1. Intentar poster
        if 'poster' in item and item['poster']:
            poster = item['poster']
            if poster.startswith('/api/images/proxy/'):
                return f"https://ntvstream-scraper.aymanisthedude1.workers.dev{poster}"
            elif poster.startswith('http'):
                return poster
        
        # 2. Intentar team badges
        if 'teams' in item:
            teams = item['teams']
            if 'home' in teams and 'badge' in teams['home']:
                badge = teams['home']['badge']
                if badge and not badge.startswith('http'):
                    return f"https://ntvstream-scraper.aymanisthedude1.workers.dev/api/images/proxy/{badge}.webp"
                elif badge:
                    return badge
        
        # 3. Intentar homeTeamIMG
        if 'homeTeamIMG' in item and item['homeTeamIMG']:
            img = item['homeTeamIMG']
            if img != 'https://api.cdn-live.tv/api/v1/team/logo.png':
                return img
        
        # 4. Intentar awayTeamIMG
        if 'awayTeamIMG' in item and item['awayTeamIMG']:
            img = item['awayTeamIMG']
            if img != 'https://api.cdn-live.tv/api/v1/team/logo.png':
                return img
        
        # 5. Logo por defecto
        return 'https://iptv.playtorrio.xyz/logo.ico'
    
    def get_league_name(self, item: dict) -> str:
        """Obtener nombre de la liga/competición"""
        # Primero intentar tournament
        if 'tournament' in item and item['tournament']:
            return item['tournament']
        
        # Luego category
        category_map = {
            'soccer': 'Fútbol',
            'football': 'Fútbol Americano',
            'basketball': 'Baloncesto',
            'hockey': 'Hockey',
            'baseball': 'Béisbol',
            'tennis': 'Tenis',
            'fight': 'Lucha/UFC',
            'mma': 'Lucha/UFC',
            'boxing': 'Boxeo',
            'rugby': 'Rugby',
            'cricket': 'Cricket',
            'golf': 'Golf',
            'motorsport': 'Automovilismo',
            'other': 'Otros',
        }
        
        category = item.get('category', '').lower()
        return category_map.get(category, category.title() or 'Deportes')
    
    async def extract_cdnlive_events(self) -> List[Dict]:
        """Extraer eventos de CDN Live"""
        print("\n📡 Extrayendo eventos de CDN Live...")
        data = await self.fetch_with_retry(CDNLIVE_API)
        
        if not data.get('success') or not data.get('live'):
            print("❌ No se pudieron obtener eventos de CDN Live")
            return []
        
        events = []
        for item in data['live']:
            try:
                timestamp = item.get('date', 0)
                time_spain = self.timestamp_to_spain_time(timestamp)
                
                event = {
                    'title': item.get('title', ''),
                    'league': self.get_league_name(item),
                    'time': time_spain,
                    'timestamp': timestamp,
                    'logo': self.get_logo_url(item),
                    'sources': [],
                    'live': item.get('live', False),
                }
                
                # Extraer sources de CDN Live
                if 'sources' in item and isinstance(item['sources'], list):
                    for source in item['sources']:
                        if isinstance(source, dict):
                            # CDN Live puede usar 'url', 'playerUrl' o 'embedUrl'
                            url = source.get('url') or source.get('playerUrl') or source.get('embedUrl')
                            
                            if url:
                                # Extraer información del canal
                                channel_name = source.get('channelName') or source.get('channel', '')
                                channel_code = source.get('channelCode') or source.get('country', '')
                                channel_logo = source.get('image') or source.get('logo', '')
                                
                                # Para CDN Live, SIEMPRE extraer de la URL porque 'source' es solo "cdnlive"
                                if 'cdn-live.tv' in url:
                                    import re
                                    # Extraer name y code de la URL: ?name=xxx&code=yyy
                                    name_match = re.search(r'[?&]name=([^&]+)', url)
                                    code_match = re.search(r'[?&]code=([^&]+)', url)
                                    if name_match:
                                        channel_name = name_match.group(1).replace('+', ' ').title()
                                    if code_match:
                                        channel_code = code_match.group(1)
                                
                                if not channel_name:
                                    channel_name = 'Stream'
                                
                                # Formatear nombre con país
                                country_name = self.get_country_name(channel_code)
                                display_name = f"{channel_name} {country_name}".strip()
                                
                                event['sources'].append({
                                    'name': display_name,
                                    'channel': channel_name,
                                    'country': country_name,
                                    'logo': channel_logo,
                                    'url': url
                                })
                
                if event['sources']:
                    events.append(event)
                    print(f"✅ {event['title']} ({event['time']}) - Liga: {event['league']} - {len(event['sources'])} sources")
            except Exception as e:
                print(f"⚠️  Error procesando evento CDN: {e}")
        
        return events
    
    async def extract_all_sources_events(self) -> List[Dict]:
        """Extraer eventos de All Sources (matches API)"""
        print("\n📡 Extrayendo eventos de All Sources...")
        data = await self.fetch_with_retry(MATCHES_API)
        
        if not data.get('success') or not data.get('live'):
            print("❌ No se pudieron obtener eventos de All Sources")
            return []
        
        events = []
        for item in data['live']:
            try:
                timestamp = item.get('date', 0)
                time_spain = self.timestamp_to_spain_time(timestamp)
                
                event = {
                    'title': item.get('title', ''),
                    'league': self.get_league_name(item),
                    'time': time_spain,
                    'timestamp': timestamp,
                    'logo': self.get_logo_url(item),
                    'sources': [],
                    'live': item.get('live', False),
                }
                
                # Extraer sources - All Sources usa estructura completa
                if 'sources' in item and isinstance(item['sources'], list):
                    for source in item['sources']:
                        if isinstance(source, dict) and 'url' in source:
                            # AQUÍ ESTÁ LA CORRECCIÓN PRINCIPAL
                            channel_name = source.get('channelName', source.get('source', 'Stream'))
                            channel_code = source.get('channelCode', '')
                            channel_logo = source.get('image', source.get('logo', ''))
                            url = source['url']
                            
                            # Formatear nombre con país
                            country_name = self.get_country_name(channel_code)
                            display_name = f"{channel_name} {country_name}".strip()
                            
                            event['sources'].append({
                                'name': display_name,
                                'channel': channel_name,
                                'country': country_name,
                                'logo': channel_logo,
                                'url': url
                            })
                
                if event['sources']:
                    events.append(event)
                    print(f"✅ {event['title']} ({event['time']}) - Liga: {event['league']} - {len(event['sources'])} sources")
            except Exception as e:
                print(f"⚠️  Error procesando evento All Sources: {e}")
        
        return events
    
    def merge_events(self, events_list: List[List[Dict]]) -> List[Dict]:
        """Combinar eventos evitando duplicados"""
        merged = {}
        
        for events in events_list:
            for event in events:
                key = f"{event['title']}|{event['timestamp']}"
                
                if key not in merged:
                    merged[key] = event
                else:
                    # Combinar sources evitando duplicados
                    existing_urls = {s['url'] for s in merged[key]['sources']}
                    for source in event['sources']:
                        if source['url'] not in existing_urls:
                            merged[key]['sources'].append(source)
                    
                    # Mantener el estado live si alguno lo tiene
                    if event.get('live'):
                        merged[key]['live'] = True
        
        return list(merged.values())
    
    async def extract_all_events(self):
        """Extraer todos los eventos deportivos"""
        print("=" * 80)
        print("🚀 PLAYTORRIO SPORTS EVENTS EXTRACTOR - VERSIÓN CON LIGA")
        print("=" * 80)
        
        await self.init_session()
        
        try:
            # Extraer primero de All Sources (matches), que es la fuente principal que sigue funcionando.
            all_events = await self.extract_all_sources_events()

            # Intentar CDN Live solo como fallback adicional si la API principal no devuelve eventos
            cdn_events = []
            if not all_events:
                cdn_events = await self.extract_cdnlive_events()
            
            print(f"\n{'=' * 80}")
            print(f"📊 RESULTADOS PARCIALES:")
            print(f"   CDN Live: {len(cdn_events)} eventos")
            print(f"   All Sources: {len(all_events)} eventos")
            print(f"{'=' * 80}")
            
            # Combinar y eliminar duplicados
            self.events = self.merge_events([cdn_events, all_events])
            
            # Filtrar eventos en vivo
            live_events = [e for e in self.events if e.get('live', False)]
            
            print(f"\n{'=' * 80}")
            print(f"✅ TOTAL: {len(self.events)} eventos deportivos únicos extraídos")
            print(f"🔴 EN VIVO: {len(live_events)} eventos")
            print(f"{'=' * 80}")
            
        finally:
            await self.close_session()
    
    def generate_m3u(self, output_file: str = 'playtorrio.m3u'):
        """Generar archivo M3U con información completa (HORA - LIGA - NOMBRE)"""
        print(f"\n📝 Generando archivo M3U: {output_file}")
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write('#EXTM3U\n')
            f.write(f'# PlayTorrio Sports Events - {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
            f.write(f'# Total Events: {len(self.events)}\n')
            
            # Contar eventos en vivo
            live_count = sum(1 for e in self.events if e.get('live', False))
            f.write(f'# Live Events: {live_count}\n\n')
            
            # Ordenar: primero eventos en vivo, luego por timestamp
            sorted_events = sorted(self.events, key=lambda x: (not x.get('live', False), x['timestamp']))
            
            for event in sorted_events:
                title = event['title'].replace('"', "'").strip()
                league = event['league'].replace('"', "'").strip()
                time = event['time']
                event_logo = event['logo']
                is_live = event.get('live', False)
                
                for source in event['sources']:
                    # Usar el logo del canal si existe, sino el del evento
                    tvg_logo = source.get('logo') or event_logo
                    
                    # Nombre del canal con país
                    channel_display = source['name']
                    
                    # Añadir emoji de LIVE si está en vivo
                    live_indicator = " 🔴" if is_live else ""
                    
                    # FORMATO NUEVO: [HORA] LIGA - NOMBRE DEL EVENTO
                    full_name = f"[{time}] {league} - {title}{live_indicator}"
                    if len(event['sources']) > 1:
                        full_name += f" - {channel_display}"
                    
                    f.write(f'#EXTINF:-1 tvg-logo="{tvg_logo}" group-title="{league}",{full_name}\n')
                    f.write(f'{source["url"]}\n\n')
        
        print(f"✅ Archivo M3U generado: {output_file}")
        
        total_streams = sum(len(e['sources']) for e in self.events)
        print(f"📊 Total streams: {total_streams}")
        
        # Estadísticas por liga
        leagues = {}
        for e in self.events:
            leagues[e['league']] = leagues.get(e['league'], 0) + 1
        
        print(f"\n📊 Eventos por liga:")
        for league, count in sorted(leagues.items(), key=lambda x: x[1], reverse=True):
            print(f"   {league}: {count} eventos")
    
    def generate_json(self, output_file: str = 'playtorrio_events.json'):
        """Generar archivo JSON"""
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({
                'generated_at': datetime.now().isoformat(),
                'total_events': len(self.events),
                'live_events': sum(1 for e in self.events if e.get('live', False)),
                'events': self.events
            }, f, indent=2, ensure_ascii=False)
        
        print(f"📝 Archivo JSON generado: {output_file}")

async def main():
    extractor = PlayTorrioEventsExtractor()
    await extractor.extract_all_events()
    
    if extractor.events:
        extractor.generate_m3u('playtorrio.m3u')
        extractor.generate_json('playtorrio_events.json')
        
        print(f"\n{'=' * 80}")
        print("✅ EXTRACCIÓN COMPLETADA CON ÉXITO")
        print(f"{'=' * 80}\n")
    else:
        print("\n❌ No se extrajeron eventos")
        exit(1)

if __name__ == '__main__':
    asyncio.run(main())
