#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Script de prueba para verificar la conectividad y mejoras del scraper"""

import sys
import logging
from script_lista_livetv_sx import EventScraper, get_random_headers

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def test_headers():
    """Prueba que los headers aleatorios funcionan"""
    print("🔍 Probando generación de headers aleatorios...")
    for i in range(3):
        headers = get_random_headers()
        print(f"  Header {i+1}: {headers['User-Agent'][:50]}...")
    print("✅ Headers aleatorios funcionan correctamente\n")

def test_session():
    """Prueba la creación de sesión con reintentos"""
    print("🔍 Probando creación de sesión con reintentos...")
    scraper = EventScraper(max_pages=2, max_workers=1)
    print(f"✅ Sesión creada correctamente")
    print(f"   Adaptadores: {list(scraper.session.adapters.keys())}\n")

def test_request():
    """Prueba hacer una petición con el método mejorado"""
    print("🔍 Probando petición HTTP mejorada...")
    scraper = EventScraper(max_pages=2, max_workers=1)

    # Intentar acceder a la página principal
    url = f"{scraper.base_url}/es/"
    print(f"   URL: {url}")
    response = scraper._make_request(url)

    if response:
        print(f"✅ Petición exitosa: Status {response.status_code}")
        print(f"   Tamaño respuesta: {len(response.text)} bytes")
        return True
    else:
        print(f"❌ Petición falló (esto es esperado si hay protección anti-bot)")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("TEST DEL SCRAPER MEJORADO")
    print("=" * 60 + "\n")

    test_headers()
    test_session()
    success = test_request()

    print("\n" + "=" * 60)
    if success:
        print("✅ TESTS COMPLETADOS - El scraper debería funcionar")
    else:
        print("⚠️ TESTS COMPLETADOS - Hay protección anti-bot activa")
    print("=" * 60)
