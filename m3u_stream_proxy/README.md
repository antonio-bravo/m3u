# 📡 M3U Stream Hub & AceStream Proxy PRO

Un centro multimedia moderno y servidor proxy en **Docker** para gestionar, escanear y reproducir listas **M3U / M3U8** desde repositorios de **GitHub**, listas locales y enlaces directos, con soporte completo para canales **AceStream** y streams HLS.

> 📖 **¿Buscas la guía técnica completa para NAS (QNAP/Synology), Plex/Jellyfin o solución de problemas?**  
> Consulta la **[Guía de Documentación Completa (DOCUMENTACION.md)](./DOCUMENTACION.md)**.

---

## 🌟 Características Principales

1. **🐙 Escáner Inteligente de Repositorios de GitHub:**
   - Introduce la URL de cualquier repositorio público de GitHub (ej. `https://github.com/usuario/mi-repo` o `usuario/mi-repo`).
   - La aplicación inspecciona recursivamente el repositorio y localiza todos los archivos `.m3u` y `.m3u8`.
   - Selecciona los archivos que quieras mediante casillas de verificación e impórtalos como fuentes activas con un solo clic.

2. **⚡ Soporte Integral para AceStream:**
   - **Reproducción directa en el navegador:** Se conecta a tu motor AceStream local o en red (`http://host.docker.internal:6878` o `http://127.0.0.1:6878`) y genera un manifiesto HLS proxificado (`/ace/manifest/{id}.m3u8`) para reproducir canales P2P directamente en la página web.
   - **Lanzador a la app nativa:** Botón directo `⚡ Abrir AceStream` (`acestream://<hash>`) para abrir AcePlayer / AceStream Media en tu ordenador o móvil.
   - **Enlace para reproductores externos:** Acceso directo a streams MPEG-TS para VLC, MPV o Kodi.

3. **📂 Detección Automática de Listas Locales:**
   - Escanea automáticamente los archivos `.m3u` existentes en la carpeta raíz del proyecto (como `canales_acestream.m3u`, `lista_ak47sports.m3u`, `ipfs_io.m3u`, etc.).
   - Puedes activar o desactivar cualquiera de estas listas con un interruptor sin necesidad de mover archivos.

4. **🚀 Rendimiento para Listas Gigantes (50,000+ canales):**
   - Motor de parseo y streaming eficiente en memoria con indexación invertida.
   - Paginación y búsqueda instantánea (debounce) para navegar sin bloqueos por listas masivas de varios megabytes (ej. `lista_ak47sports.m3u`).

5. **🛡️ Proxy CORS y Sistema de Búfer PRO:**
   - Resuelve automáticamente los bloqueos de cabeceras CORS (`Access-Control-Allow-Origin`) de streams HLS remotos de GitHub para que el navegador los reproduzca sin errores.
   - **3 Perfiles de Búfer:** *Ultra Estable (20s)*, *Equilibrado (12s)* y *Baja Latencia (4s)*.
   - Medidor dinámico de búfer con semáforo luminoso y **Auto-recuperación activa** ante micro-cortes.
   - **🔲 Picture-in-Picture PRO & Multivisión Dual:** PiP nativo del navegador más mini-reproductor flotante arrastrable para ver un segundo canal simultáneamente, con botón `🔲` en cada tarjeta, intercambio de canales (`⇄ Swap`) y **control de audio de 3 modos (Solo Principal, Solo PiP o Ambos a la vez)** con barra de volumen independiente para graduar la mezcla de ambos audios.
   - **📡 Selector de Fuentes Alternativas (`N fuentes ▾`):** Detecta emisiones duplicadas entre listas (AceStream, AK47, DaddyLive) y permite alternar la señal o abrirla en PiP.
   - **🎧 Selector Dinámico de Pistas de Audio:** Permite cambiar al instante entre diferentes pistas de audio (idiomas, emisoras de radio o sonido ambiente) en streams HLS, DASH y canales AceStream (con parámetro `preferred_audio_language`).
   - **Amplificador de Audio Web Audio API:** Modos de volumen 100%, 150%, 200% y 300% para emisiones con volumen bajo.

6. **📋 Exportador M3U para Dispositivos Externos:**
   - Endpoint `/playlist.m3u` con URLs proxificadas para usar en **TiviMate, Kodi, OTT Navigator, IPTV Smarters o VLC**.

---

## 📦 Puesta en Marcha con Docker

### Opción 1: Con Docker Compose (Recomendado)

Desde la carpeta `m3u_stream_proxy`:

```bash
cd m3u_stream_proxy
docker compose up -d --build
```

O desde la raíz del proyecto:
```bash
docker compose -f m3u_stream_proxy/docker-compose.yml up -d --build
```

Para ver los registros en tiempo real:
```bash
docker compose -f m3u_stream_proxy/docker-compose.yml logs -f
```

Para detener el contenedor:
```bash
docker compose -f m3u_stream_proxy/docker-compose.yml down
```

---

## 📺 Cómo Utilizarlo y Guía de URLs

Una vez que el contenedor esté corriendo en el puerto **`8899`**, abre en tu navegador:

👉 **`http://localhost:8899/`**

### 📋 URLs de Exportación M3U con Filtros Inteligentes

El proxy permite filtrar dinámicamente tu lista mediante parámetros en la URL para que no satures dispositivos con recursos limitados (Kodi, Android TV, Fire TV, TiviMate, Plex):

| Recurso / Filtro | Ejemplo de URL | Caso de Uso |
| :--- | :--- | :--- |
| **⭐ Solo Favoritos (Recomendado)** | `http://<IP>:8899/playlist.m3u?fav=1` | **Ideal para Kodi, Plex y Smart TV.** Carga solo los canales marcados con estrella en la web en 1 segundo. |
| **🔒 Solo DASH / ClearKey DRM** | `http://<IP>:8899/playlist.m3u?type=dash` | Canales MPEG-DASH con etiquetas `#KODIPROP` oficiales (ZDF, FanCode, etc.). |
| **⚡ Solo AceStream** | `http://<IP>:8899/playlist.m3u?type=acestream` | Solo canales P2P de AceStream en HLS proxificado. |
| **🔍 Búsqueda por Texto** | `http://<IP>:8899/playlist.m3u?search=spain` | Canales que contengan el término en su nombre o grupo (ej. `spain`, `laliga`, `f1`). |
| **📁 Por Categoría** | `http://<IP>:8899/playlist.m3u?group=Sports+HD` | Solo canales de un grupo específico. |
| **🔢 Límite de Canales** | `http://<IP>:8899/playlist.m3u?limit=50` | Limita la lista a los primeros *N* canales. |
| **📺 Formato MPEG-TS para Plex/xTeVe** | `http://<IP>:8899/playlist.m3u?fav=1&format=ts` | Flujo MPEG-TS continuo ideal para sintonizadores y transcodificadores. |
| **🎬 AceStream Manifest Directo** | `http://<IP>:8899/playlist.m3u?format=manifest&server_id=direct` | Manifiesto nativo del motor AceStream traduciendo la IP LAN para Smart TV. |
| **🌐 Lista Completa Unificada** | `http://<IP>:8899/playlist.m3u` | Lista completa con todas las fuentes habilitadas. |

> [!TIP]
> **Combinación de filtros:** Puedes encadenar parámetros con `&`:  
> `http://<IP>:8899/playlist.m3u?fav=1&search=laliga` (favoritos que contengan LaLiga).

---

## 📺 Integración con Plex Live TV & DVR (Sintonizador Virtual HDHomeRun)

El contenedor emula un sintonizador **SiliconDust HDHomeRun**. Plex lo detecta como una tarjeta sintonizadora de TV física:

1. En Plex Web, ve a **Ajustes ⚙️ > Administrar > TV en vivo y DVR**.
2. Pulsa en **Añadir dispositivo** (o *Configurar sintonizador*).
3. Si no aparece automáticamente, pulsa en **"¿No ve su dispositivo? Especifique la dirección IP manualmente"** e introduce:
   ```text
   http://<IP_DE_TU_SERVIDOR>:8899
   ```
4. Plex detectará el sintonizador **M3U Stream Hub** con sus sintonizadores virtuales.
5. **Endpoints HDHomeRun disponibles:**
   - **Información del dispositivo:** `http://<IP>:8899/discover.json`
   - **Parrilla de canales para Plex:** `http://<IP>:8899/lineup.json?favorites=true`
   - **Flujo de sintonía:** `http://<IP>:8899/auto/v1`, `/auto/v2`... (MPEG-TS continuo para AceStream).
6. También puedes integrarlo mediante proxies como **Threadfin** o **xTeVe** apuntando a `http://<IP>:8899/playlist.m3u?fav=1&format=ts`.

---

## 📡 Endpoints de la API REST

| Endpoint | Método | Descripción |
| :--- | :--- | :--- |
| `/health` | `GET` | Estado de salud, uptime y canales cargados. |
| `/api/channels` | `GET` | Catálogo de canales paginado (`?search=`, `?favorites=1`, `?group=`). |
| `/api/favorites` | `GET` | Lista de IDs de canales marcados como favoritos. |
| `/api/favorites/{id}` | `POST` | Alternar estado favorito de un canal. |
| `/api/groups` | `GET` | Listado de grupos y cantidad de canales. |
| `/api/acestream/check` | `GET` | Medición de latencia y estado de los motores AceStream. |
| `/api/hdhomerun/status` | `GET` | Estado del emulador HDHomeRun y canales mapeados para Plex. |

---

## ⚙️ Configuración del Motor AceStream

Si tienes instalado AceStream en tu sistema operativo (Mac, Windows, Linux) o corriendo en otro contenedor Docker:

1. El motor AceStream escucha por defecto en el puerto `6878`.
2. Dentro de Docker Compose, el contenedor se comunica con el host mediante:
   ```env
   ACESTREAM_ENGINE_URL=http://host.docker.internal:6878
   ```
3. Si ejecutas el proxy de forma local sin Docker, la URL es:
   ```env
   ACESTREAM_ENGINE_URL=http://127.0.0.1:6878
   ```
4. Puedes probar y cambiar la URL en cualquier momento desde la interfaz web en **`⚙️ Ajustes & AceStream`** pulsando el botón **`🔌 Probar`**.

---

## ⚡ Ejecución Local con Python (sin Docker)

Si prefieres ejecutarlo directamente en tu entorno Python:

```bash
cd m3u_stream_proxy
pip install -r requirements.txt
python server.py
```
El servidor quedará escuchando en `http://localhost:8899`.
