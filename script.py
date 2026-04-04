import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from difflib import get_close_matches
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

def obtener_url_diaria():
    """Retorna la URL base de Platinsport"""
    return "https://www.platinsport.com"

def extraer_eventos(url):
    options = Options()
    # En GitHub Actions usa headless, localmente usa modo normal para debugging
    import os
    if os.getenv('CI') or os.getenv('GITHUB_ACTIONS'):
        options.add_argument('--headless')
    
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-plugins')
    options.add_argument('user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36')
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    eventos = []
    
    try:
        print(f"Cargando URL: {url}")
        driver.get(url)
        print("Página cargada...")
        
        # Wait for JavaScript to load
        time.sleep(2)
        
        # Accept cookies
        try:
            accept_button = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'I ACCEPT')]"))
            )
            accept_button.click()
            print("Cookies aceptadas")
            time.sleep(1)
        except:
            pass
        
        # Find PLAY buttons using different selectors
        play_buttons = []
        try:
            print("Buscando botones PLAY...")
            soup_temp = BeautifulSoup(driver.page_source, "html.parser")
            play_buttons = soup_temp.find_all('a', string='PLAY')
            print(f"Found {len(play_buttons)} PLAY buttons via parsing")
            
            if play_buttons:
                # Use Selenium to find and click
                driver.execute_script("arguments[0].scrollIntoView(true);", driver.find_elements(By.XPATH, "//a[text()='PLAY']")[0])
                time.sleep(1)
                driver.find_elements(By.XPATH, "//a[text()='PLAY']")[0].click()
                print("Haciendo clic en PLAY...")
                time.sleep(3)
        except Exception as e:
            print(f"Error al hacer clic en PLAY: {e}")
        
        # Try to find popup window
        handles = driver.window_handles
        print(f"Ventanas abiertas: {len(handles)}")
        
        if len(handles) > 1:
            # Switch to new window
            original_handle = handles[0]
            for handle in handles:
                if handle != original_handle:
                    driver.switch_to.window(handle)
                    print(f"Cambiado a ventana: {handle}")
                    break
            time.sleep(2)
        
        # Get page content
        soup = BeautifulSoup(driver.page_source, "html.parser")
        
        # Parse the soup
        print("Buscando contenedor .myDiv1...")
        contenedor = soup.find("div", class_="myDiv1")
        if not contenedor:
            print("No se encontró el contenedor de eventos (.myDiv1)")
            driver.quit()
            return eventos
        liga_actual = None
        elements = list(contenedor.children)
        i = 0
        while i < len(elements):
            element = elements[i]
            if hasattr(element, "name") and element.name == 'p':
                liga_actual = element.get_text(strip=True)
            elif hasattr(element, "name") and element.name == 'time':
                time_val = element.get("datetime", "").strip()
                try:
                    hora_evento = datetime.fromisoformat(time_val.replace("Z", "")).time()
                except Exception:
                    try:
                        hora_evento = datetime.strptime(time_val, "%H:%M").time()
                    except Exception:
                        hora_evento = datetime.strptime("23:59", "%H:%M").time()
                hora_evento = convertir_a_utc_mas_1(hora_evento)
                event_text = ""
                canales = []
                j = i + 1
                while j < len(elements):
                    sib = elements[j]
                    if hasattr(sib, "name") and (sib.name == "time" or sib.name == "p"):
                        break
                    if hasattr(sib, "name") and sib.name == "a" and "acestream://" in sib.get("href", ""):
                        canales.append(sib)
                    elif hasattr(sib, "name"):
                        event_text += sib.get_text(" ", strip=True) + " "
                    elif isinstance(sib, str):
                        event_text += sib.strip() + " "
                    j += 1
                event_text = event_text.strip()
                event_text = " ".join(event_text.split())
                event_text = eliminar_repeticiones_live_stream(event_text)
                for a_tag in canales:
                    canal_text = a_tag.get_text(" ", strip=True)
                    eventos.append({
                        "hora": hora_evento,
                        "nombre": f"{liga_actual} - {event_text}" if event_text else f"{liga_actual} - Evento Desconocido",
                        "canal": canal_text,
                        "url": a_tag["href"]
                    })
                i = j - 1
            i += 1
    except Exception as e:
        print(f"Error al extraer eventos: {e}")
    driver.quit()
    return eventos

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
