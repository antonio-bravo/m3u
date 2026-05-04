#!/usr/bin/env python3
"""
Script para descargar canales de IPFS y generar un archivo .m3u
Descarga el JSON desde: https://ipfs.io/ipns/k51qzi5uqu5di462t7j4vu4akwfhvtjhy88qbupktvoacqfqe9uforjvhyi4wr/hashes.json
Genera: ipfs_io.m3u
"""

import requests
import json
from datetime import datetime
import os

def descargar_json_ipfs():
    """Descargar el JSON con los hashes de canales desde IPFS"""
    url = "https://ipfs.io/ipns/k51qzi5uqu5di462t7j4vu4akwfhvtjhy88qbupktvoacqfqe9uforjvhyi4wr/hashes.json"
    
    try:
        print(f"Descargando JSON desde IPFS...")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        print(f"✓ JSON descargado correctamente")
        print(f"  - Total de canales: {data.get('count', len(data.get('hashes', [])))}")
        print(f"  - Fecha de generación: {data.get('generated', 'N/A')}")
        
        return data
    except requests.exceptions.RequestException as e:
        print(f"✗ Error al descargar el JSON: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"✗ Error al parsear el JSON: {e}")
        return None

def generar_m3u(data):
    """Generar archivo .m3u con los canales del JSON"""
    if not data or 'hashes' not in data:
        print("✗ Datos inválidos o vacíos")
        return False
    
    hashes = data.get('hashes', [])
    if not hashes:
        print("✗ No hay canales en el JSON")
        return False
    
    # Crear contenido del M3U
    contenido = "#EXTM3U\n"
    contenido += f"# IPFS Acestream Channels - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    contenido += f"# Total Channels: {len(hashes)}\n"
    contenido += f"# Generated: {data.get('generated', 'N/A')}\n\n"
    
    # Agrupar canales por grupo (category)
    grupos = {}
    for canal in hashes:
        grupo = canal.get('group', 'Sin Categoría')
        if grupo not in grupos:
            grupos[grupo] = []
        grupos[grupo].append(canal)
    
    # Agregar canales al contenido, organizados por grupo
    contador = 0
    for grupo in sorted(grupos.keys()):
        contenido += f"# -------- {grupo} --------\n"
        for canal in grupos[grupo]:
            title = canal.get('title', 'Canal sin nombre')
            hash_acestream = canal.get('hash', '')
            logo = canal.get('logo', '')
            tvg_id = canal.get('tvg_id', title)
            
            # Crear entrada EXTINF
            contenido += f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{grupo}",{title}\n'
            # Usar acestream:// con el hash
            contenido += f"http://127.0.0.1:6878/ace/getstream?id={hash_acestream}\n"
            contenido += "\n"
            contador += 1
    
    # Guardar archivo
    archivo_salida = "ipfs_io.m3u"
    try:
        with open(archivo_salida, 'w', encoding='utf-8') as f:
            f.write(contenido)
        
        file_size = os.path.getsize(archivo_salida)
        print(f"✓ Archivo '{archivo_salida}' generado correctamente")
        print(f"  - Canales incluidos: {contador}")
        print(f"  - Tamaño: {file_size:,} bytes")
        
        return True
    except IOError as e:
        print(f"✗ Error al guardar el archivo: {e}")
        return False

def main():
    """Función principal"""
    print("=" * 60)
    print("Script de descarga de canales IPFS Acestream")
    print("=" * 60)
    print()
    
    # Descargar JSON
    data = descargar_json_ipfs()
    if not data:
        print("\nNo se pudo completar la operación.")
        return False
    
    print()
    
    # Generar M3U
    if generar_m3u(data):
        print("\n✓ Proceso completado exitosamente")
        return True
    else:
        print("\n✗ Proceso falló")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
