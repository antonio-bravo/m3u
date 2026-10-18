# 🚀 TVNow247 IPTV Deobfuscator & HLS Proxy

Este servicio es un middleware en **Docker** que permite reproducir canales de **TVNow247** en cualquier reproductor multimedia (VLC, TiviMate, OTT Navigator, Kodi, Jellyfin, etc.).

---

## 🎯 ¿Qué problema resuelve?

Los servidores de streaming de TVNow247 entregan listas HLS cuyos segmentos de video están camuflados como imágenes (formatos **WebP** y **PNG**) alojadas en la CDN de TikTok.
En el formato WebP actual, cada archivo contiene una imagen mínima y la carga real de video MPEG-TS empaquetada en el chunk de metadatos `EXIF`. En versiones anteriores, se utilizaban imágenes PNG con cabecera `TIKTIKPX` y compresión GZIP.

Este contenedor:
1. **Intercepta las listas M3U8** y resuelve o regenera los tokens de acceso dinámicos.
2. **Descarga cada fragmento camuflado** (WebP/PNG) desde la CDN de TikTok.
3. **Desofusca el payload** (extrae el flujo MPEG-TS del contenedor WebP/EXIF o PNG al vuelo en sub-milisegundos).
4. **Entrega paquetes de video MPEG-TS limpios y estándar** (`video/mp2t`) a tu reproductor.

---

## 📦 Puesta en marcha con Docker

### Opción 1: Con Docker Compose (Recomendado)

Desde la carpeta `tvnow247_proxy`:

```bash
cd tvnow247_proxy
docker compose up -d --build
```

O desde la raíz del proyecto:
```bash
docker compose -f tvnow247_proxy/docker-compose.yml up -d --build
```

Para ver los registros en tiempo real:
```bash
docker compose -f tvnow247_proxy/docker-compose.yml logs -f
```

Para detener el servicio:
```bash
docker compose -f tvnow247_proxy/docker-compose.yml down
```

---

### Opción 2: Con Docker CLI directo

```bash
cd tvnow247_proxy
docker build -t tvnow247-proxy .
docker run -d --name tvnow247-proxy -p 8080:8080 --restart unless-stopped tvnow247-proxy
```

---

## 📺 Cómo usarlo en tus reproductores

Una vez que el contenedor esté corriendo en el puerto `8080`:

| Recurso | URL | Uso |
| :--- | :--- | :--- |
| **Reproductor Web PRO** | `http://localhost:8888/` | Reproductor web moderno con medidor de búfer y explorador |
| **Canal Directo Web** | `http://localhost:8888/player/{id}` | Ej: `http://localhost:8888/player/303` (reproduce directamente) |
| **Lista M3U Completa** | `http://localhost:8888/channels.m3u` | Para TiviMate, OTT Navigator, IPTV Smarters, etc. |
| **Canal Específico HLS** | `http://localhost:8888/live/{id}.m3u8` | Con ventana ampliada y TIME-OFFSET |
| **Canal con Offset Personalizado** | `http://localhost:8888/live/{id}.m3u8?offset=20` | Ajusta los segundos de colchón de búfer (ej. 20s) |
| **Proxy de URL Directa** | `http://localhost:8888/proxy?url=...` | Pasa la URL que tenías directamente |
| **API de Canales (JSON)** | `http://localhost:8888/api/channels` | Catálogo de canales con logos y grupos |
| **Estado y Rendimiento** | `http://localhost:8888/api/status` | Métricas de aciertos de caché, prefetches y salud |

---

## ⚡ Mejoras del Sistema PRO de Búfer

1. **Ventana Deslizante Extendida (Sliding Window):**  
   Los servidores de TVNow247 solo ofrecen 3 o 4 fragmentos (~12 segundos de vídeo total). El proxy mantiene un historial contiguo de hasta **8 fragmentos (~30 segundos)**, evitando que el reproductor agote los datos si hay un microcorte.

2. **Inyección de `TIME-OFFSET` (RFC 8216):**  
   Inyecta la directiva `#EXT-X-START:TIME-OFFSET=-12.0,PRECISE=YES` (o el valor indicado en `?offset=...`). Esto obliga al reproductor a situarse 12-20 segundos por detrás del directo, acumulando de inmediato un colchón de seguridad de 3 o 4 segmentos en el navegador.

3. **Precarga Predictiva en RAM (Background Prefetching):**  
   Al solicitar una lista M3U8, el proxy lanza en segundo plano la descarga y desofuscación de los siguientes segmentos hacia una caché LRU en RAM. Cuando el reproductor solicita el archivo `.ts`, el proxy responde en **< 1 ms** con carga instantánea.

4. **Soporte HTTP Byte-Range y ETag:**  
   Permite peticiones parciales `206 Partial Content` y cabeceras de inmutabilidad `Cache-Control: public, max-age=86400, immutable`.

5. **Reproductor Web Integrado de Alta Fidelidad:**  
   - Medidor de salud de búfer en tiempo real con indicador luminoso dinámico.
   - 3 perfiles de búfer seleccionables con un clic: *Ultra Estable (20s)*, *Equilibrado (12s)* y *Baja Latencia (4s)*.
   - Refuerzo de audio Web Audio API (100%, 150%, 200%) para canales con emisión baja.
   - HUD con estadísticas técnicas en pantalla (Resolución, Bitrate, Búfer exacto en segundos, Hits de caché).
   - Barra lateral con búsqueda instantánea y filtros por país entre más de 800 canales.

---

## ⚡ Ejecución local en Python (sin Docker)

Si prefieres ejecutarlo directamente en tu entorno Python:

```bash
cd tvnow247_proxy
pip install -r requirements.txt
python proxy_server.py
```
El servidor quedará escuchando en `http://localhost:8080`.
