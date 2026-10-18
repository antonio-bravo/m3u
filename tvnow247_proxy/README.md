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
| **Panel Web** | `http://localhost:8888/` | Interfaz web de estado e información |
| **Lista M3U Completa** | `http://localhost:8888/channels.m3u` | Para TiviMate, OTT Navigator, IPTV Smarters, etc. |
| **Canal Específico** | `http://localhost:8888/live/{id}.m3u8` | Ej: `http://localhost:8888/live/652.m3u8` |
| **Proxy de URL Directa** | `http://localhost:8888/proxy?url=...` | Pasa la URL que tenías directamente |

### Ejemplos:

#### 1. VLC Media Player
Abre VLC y ve a:
> **Medio** ➔ **Abrir ubicación de red...**  
> Introduce: `http://localhost:8888/live/652.m3u8`  
> Haz clic en **Reproducir**.

O por línea de comandos:
```bash
vlc "http://localhost:8888/live/652.m3u8"
```

#### 2. TiviMate / OTT Navigator / IPTV Smarters
Añade una nueva lista de reproducción tipo M3U:
- **URL de la lista:** `http://TU_IP_LOCAL:8888/channels.m3u`
- La lista cargará automáticamente los más de 800 canales con sus nombres, logos y categorías.

#### 3. Probar la URL que fallaba anteriormente
Si tienes una URL como:
`https://chunk.tvnow247.today/api/proxy/playlist?token=eyJjaGFubmVsSWQiOiI2NTIiLCJ0cyI6MTc5MTkxMjkwMzE2MH0`

Simplemente pásala por el proxy:
```
http://localhost:8888/proxy?url=https://chunk.tvnow247.today/api/proxy/playlist?token=eyJjaGFubmVsSWQiOiI2NTIiLCJ0cyI6MTc5MTkxMjkwMzE2MH0
```
El proxy detectará automáticamente el `channelId` ("652"), renovará el token caducado y desofuscará los segmentos para que reproduzca al instante.

---

## ⚡ Ejecución local en Python (sin Docker)

Si prefieres ejecutarlo directamente en tu entorno Python:

```bash
cd tvnow247_proxy
pip install -r requirements.txt
python proxy_server.py
```
El servidor quedará escuchando en `http://localhost:8080`.
