# 📡 M3U Stream Hub & AceStream Proxy PRO

Un centro multimedia moderno y servidor proxy en **Docker** para gestionar, escanear y reproducir listas **M3U / M3U8** desde repositorios de **GitHub**, listas locales y enlaces directos, con soporte completo para canales **AceStream** y streams HLS.

Inspirado en la tecnología y el diseño de `tvnow247_proxy`, este contenedor permite unificar todas tus fuentes IPTV en una interfaz web de alta fidelidad y exportar una lista M3U unificada para tus reproductores externos.

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

## 📺 Cómo Utilizarlo

Una vez que el contenedor esté corriendo en el puerto **`8899`**, abre en tu navegador:

👉 **`http://localhost:8899/`**

| Recurso | URL | Descripción |
| :--- | :--- | :--- |
| **Reproductor Web PRO** | `http://localhost:8899/` | Interfaz completa con reproductor, explorador y buscador |
| **Lista M3U Unificada** | `http://localhost:8899/playlist.m3u` | Lista completa para TiviMate, OTT Navigator, VLC |
| **Lista Solo AceStream** | `http://localhost:8899/playlist.m3u?type=acestream` | Solo los canales de tipo AceStream |
| **Catálogo de Canales (API)** | `http://localhost:8899/api/channels` | Paginado y con filtros |
| **Estado AceStream Engine** | `http://localhost:8899/api/acestream/check` | Comprobación de salud del motor AceStream |

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
