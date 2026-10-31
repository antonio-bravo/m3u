# 📡 Guía Completa de Documentación: M3U Stream Hub & AceStream Proxy PRO

**M3U Stream Hub** es una plataforma unificada y servidor proxy de alto rendimiento empaquetado en **Docker**. Diseñado para centralizar, escanear, normalizar y reproducir listas de canales **M3U / M3U8** procedentes de repositorios de **GitHub**, archivos locales y flujos P2P de **AceStream**, ofreciendo además emulación **HDHomeRun** para **Plex** y **Jellyfin**.

---

## 📑 Tabla de Contenidos

1. [Arquitectura y Funcionamiento](#-arquitectura-y-funcionamiento)
2. [Opciones de Despliegue con Docker](#-opciones-de-despliegue-con-docker)
   - [A. Despliegue en NAS (Container Station)](#a-despliegue-en-nas-container-station-recomendado)
   - [B. Despliegue en Docker Compose Estándar (Linux / Mac / Windows)](#b-despliegue-en-docker-compose-estándar)
   - [C. Variables de Entorno](#c-variables-de-entorno)
3. [Configuración de Motores AceStream](#-configuración-de-motores-acestream)
   - [Doble Motor (Con VPN y Sin VPN)](#doble-motor-con-vpn-y-sin-vpn)
   - [Problema Común en NAS: Docker Hairpin NAT](#problema-común-en-nas-docker-hairpin-nat-y-solución)
   - [Resolución Inteligente de IP para Dispositivos Externos](#resolución-inteligente-de-ip-para-dispositivos-externos)
4. [Formatos de Enlace Disponibles](#-formatos-de-enlace-disponibles)
5. [Exportación de Listas M3U para Clientes IPTV](#-exportación-de-listas-m3u-para-clientes-iptv)
6. [Compatibilidad con Listas AK47, FanCode y Canales DASH / ClearKey DRM](#-compatibilidad-con-listas-ak47-fancode-y-canales-dash--clearkey-drm)
7. [Integración con Plex y Jellyfin (HDHomeRun)](#-integración-con-plex-y-jellyfin-hdhomerun)
8. [Funcionalidades de la Interfaz Web PRO](#-funcionalidades-de-la-interfaz-web-pro)
9. [Resolución de Problemas Frecuentes (FAQ)](#-resolución-de-problemas-frecuentes-faq)

---

## 🏗 Arquitectura y Funcionamiento

```
                 ┌──────────────────────────────────────────────┐
                 │          Fuentes de Contenido M3U            │
                 │  - Repositorios GitHub (Escaneo dinámico)    │
                 │  - Listas Locales (.m3u en el host)          │
                 │  - Streams Directos / AceStream Hashes       │
                 └──────────────────────┬───────────────────────┘
                                        │
                                        ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                       M3U STREAM PROXY (Puerto 8899)                          │
│                                                                               │
│  ┌───────────────────────┐  ┌───────────────────────┐  ┌───────────────────┐  │
│  │   Parseador & Caché   │  │    Proxy CORS HLS     │  │  Proxy AceStream  │  │
│  │  (50,000+ canales en  │  │ (Evita bloqueos web   │  │ (HLS & MPEG-TS    │  │
│  │     RAM indexada)     │  │  de emisiones remotas)│  │   con Failover)   │  │
│  └───────────────────────┘  └───────────────────────┘  └───────────────────┘  │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │           Emulador HDHomeRun (Sintonizador virtual para Plex)           │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
└──────────────┬───────────────────────────────┬──────────────────────┬─────────┘
               │                               │                      │
               ▼                               ▼                      ▼
    ┌──────────────────────┐       ┌──────────────────────┐   ┌─────────────────┐
    │  Interfaz Web HTML5  │       │  Clientes IPTV (M3U) │   │  Plex/Jellyfin  │
    │  - Player HLS.js     │       │  - TiviMate, Kodi    │   │  - Live TV      │
    │  - Búfer & Boost     │       │  - VLC, OTT Nav      │   │  - Grabador DVR │
    │  - Telemetría P2P    │       │  - Smart IPTV        │   └─────────────────┘
    └──────────────────────┘       └──────────────────────┘
```

---

## 📦 Opciones de Despliegue con Docker

### A. Despliegue en NAS (Container Station) [Recomendado]

En sistemas NAS basados en Linux (como QNAP o Synology), la configuración más estable y de mayor rendimiento es utilizar **`network_mode: host`**. Esto elimina la sobrecarga del puente de red y permite que el proxy se conecte directamente con el motor AceStream en `127.0.0.1` sin que el firewall del NAS lo bloquee.

Crea una nueva aplicación en **Container Station** con este `docker-compose.yml`:

```yaml
services:
  m3u-hub:
    image: ghcr.io/antonio-bravo/m3u/m3u-stream-proxy:latest
    container_name: m3u-stream-proxy
    restart: unless-stopped
    pull_policy: always
    network_mode: host
    volumes:
      - ./data:/app/data
      - ../:/app/local_playlists:ro
    environment:
      - PORT=8899
      - PYTHONUNBUFFERED=1
      - ACESTREAM_ENGINE_URL=http://127.0.0.1:6879
      - ACESTREAM_VPN_ENGINE_URL=http://127.0.0.1:6878
      - BUFFER_OFFSET_SECONDS=16.0
      - AUTO_SYNC_HOURS=6
      - GITHUB_REPOS=https://github.com/antonio-bravo/m3u
      - GITHUB_TOKEN=
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

> [!IMPORTANT]
> **Regla de oro sobre volúmenes en NAS:**
> * Monta **únicamente** la carpeta `./data:/app/data` para persistencia de ajustes y favoritos.
> * **NUNCA montes carpetas vacías del NAS en `/app` o `/app/templates`**, ya que eso ocultaría la interfaz web del contenedor.

---

### B. Despliegue en Docker Compose Estándar

Si ejecutas Docker en un ordenador local (macOS, Linux o Windows con Docker Desktop) o en un servidor con red tipo puente (Bridge):

```yaml
services:
  m3u-hub:
    image: ghcr.io/antonio-bravo/m3u/m3u-stream-proxy:latest
    container_name: m3u-stream-proxy
    restart: unless-stopped
    pull_policy: always
    ports:
      - "8899:8899"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./data:/app/data
      - ../:/app/local_playlists:ro
    environment:
      - PORT=8899
      - PYTHONUNBUFFERED=1
      - ACESTREAM_ENGINE_URL=http://host.docker.internal:6879
      - ACESTREAM_VPN_ENGINE_URL=http://host.docker.internal:6878
      - BUFFER_OFFSET_SECONDS=16.0
      - AUTO_SYNC_HOURS=6
      - GITHUB_REPOS=https://github.com/antonio-bravo/m3u
```

---

### C. Variables de Entorno

| Variable | Valor por Defecto | Descripción |
| :--- | :--- | :--- |
| `PORT` | `8899` | Puerto HTTP en el que escucha la interfaz web y la API. |
| `ACESTREAM_ENGINE_URL` | `http://host.docker.internal:6878` | URL del motor AceStream principal (**Sin VPN**). |
| `ACESTREAM_VPN_ENGINE_URL`| *(vacío)* | URL opcional de un segundo motor AceStream enrutado mediante **VPN**. |
| `BUFFER_OFFSET_SECONDS` | `16.0` | Tiempo de retardo seguro en segundos para amortiguar micro-cortes en HLS. |
| `AUTO_SYNC_HOURS` | `6` | Intervalo en horas para sincronizar automáticamente repositorios GitHub. |
| `GITHUB_REPOS` | *(vacío)* | URL(s) o identificadores de repositorios GitHub (separados por coma, ej. `https://github.com/antonio-bravo/m3u`) para **auto-escanear, descargar en caché local e importar todas sus listas M3U al arrancar el contenedor**. |
| `M3U_URLS` | *(vacío)* | URL(s) directas de listas `.m3u` remotas (separadas por coma) para auto-importar al inicio. |
| `GITHUB_TOKEN` | *(vacío)* | Token de GitHub (opcional) para aumentar el límite de peticiones de la API a 5.000 req/hora. |
| `DATA_DIR` | `/app/data` | Directorio de persistencia interna para la base de datos de canales y ajustes. |
| `LOCAL_PLAYLISTS_DIR` | `/app/local_playlists` | Directorio donde el proxy busca archivos `.m3u` locales en el host. |

---

## ⚡ Configuración de Motores AceStream

El sistema incluye gestión inteligente de motores AceStream con monitorización de estado en tiempo real y conmutación por fallo (*failover*).

### Doble Motor (Con VPN y Sin VPN)

Es posible configurar dos instancias simultáneas de AceStream:
1. **Motor Normal (Sin VPN):** Proporciona menor latencia y mayor ancho de banda local.
2. **Motor con VPN:** Para ISPs que aplican bloqueos o inspección de paquetes P2P.

En la interfaz web (**⚙️ Ajustes & AceStream**) puedes:
- Probar la conectividad y medir la latencia en milisegundos de cada motor.
- Activar el **Failover Automático**: si el motor principal no responde, el stream pasa automáticamente al motor con VPN sin interrumpir la lista.

---

### Problema Común en NAS: Docker Hairpin NAT y Solución

En NAS como QNAP y Synology, cuando un contenedor en red tipo puente (*bridge*) intenta conectarse a la IP de la tarjeta física del NAS (`192.168.1.X`), el firewall del sistema operativo (QuFirewall) suele descartar el paquete.

* **Síntoma:** El comando `curl` o la URL de AceStream responde correctamente desde tu PC, pero dentro del proxy aparece como *"No responde"*.
* **Solución definitiva:** 
  1. Utiliza `network_mode: host` y apunta a `http://127.0.0.1:6879`.
  2. O en modo puente, utiliza `http://host.docker.internal:6879`.

---

### Resolución Inteligente de IP para Dispositivos Externos

Si configuras el motor como `127.0.0.1` o `host.docker.internal` para que el proxy trabaje internamente sin bloqueos:
* Al abrir la ventana **📋 Enlaces M3U** desde un navegador en tu red local (ej. `http://192.168.1.50:8899`), el sistema **detecta automáticamente la IP real de tu NAS** y traduce los enlaces a:
  ```text
  http://192.168.1.50:6879/ace/manifest.m3u8?id=<hash>
  ```
* De esta forma, reproductores externos como Smart TV o VLC pueden reproducir el stream directo sin apuntar erróneamente a su propio `localhost`.

---

## 🔗 Formatos de Enlace Disponibles

Para cada canal AceStream, el proxy genera y proporciona diferentes formatos según las necesidades del reproductor:

| Formato | Ejemplo de URL | Caso de Uso |
| :--- | :--- | :--- |
| **HLS Manifest Nativo** | `http://<IP_NAS>:6879/ace/manifest.m3u8?id=<hash>` | **Recomendado para Smart TV y reproductores modernos**. El motor AceStream genera segmentos HLS directamente. |
| **Stream MPEG-TS Directo** | `http://<IP_NAS>:6879/ace/getstream?id=<hash>` | Conexión directa HTTP MPEG-TS al motor sin pasar por el proxy (ideal para VLC o Kodi con conexión rápida). |
| **Stream HLS Proxificado** | `http://<IP_PROXY>:8899/live/<channel_id>.m3u8` | Stream procesado a través del proxy con búfer ajustable y bypass CORS. |
| **MPEG-TS Proxificado** | `http://<IP_PROXY>:8899/ace/stream/<hash>` | Stream transcodificado a MPEG-TS limpio por el proxy para dispositivos antiguos o sintonizadores. |
| **URI Nativa AceStream** | `acestream://<hash>` | Abre la aplicación oficial de AceStream en Windows, Android o Mac. |

---

## 📋 Exportación de Listas M3U para Clientes IPTV

Puedes importar las listas generadas por el proxy en cualquier cliente IPTV (**TiviMate, OTT Navigator, IPTV Smarters, VLC, Kodi**):

### 1. Listas Generales Proxificadas (HLS)
* **Lista Completa (Todos los canales):**
  ```text
  http://<IP_PROXY>:8899/playlist.m3u
  ```
* **Lista Solo Canales AceStream:**
  ```text
  http://<IP_PROXY>:8899/playlist.m3u?type=acestream
  ```
* **Lista Filtrada por Categoría o Grupo:**
  ```text
  http://<IP_PROXY>:8899/playlist.m3u?group=Deportes
  ```

### 2. Listas Directas al Motor AceStream
* **Lista Formato HLS Manifest (Motor AceStream directo):**
  ```text
  http://<IP_PROXY>:8899/playlist.m3u?format=manifest&server_id=direct
  ```
* **Lista Formato MPEG-TS Directo (`/ace/getstream`):**
  ```text
  http://<IP_PROXY>:8899/playlist.m3u?format=getstream&server_id=direct
  ```

---

## 🔐 Compatibilidad con Listas AK47, FanCode y Canales DASH / ClearKey DRM

El proxy incluye compatibilidad integral con listas complejas como **AK47Sports** y canales protegidos como **FanCode**:

### ¿Cómo funcionan los canales de FanCode en AK47 sin VPN?
1. **Canales 24/7 (FANCODE 1, FANCODE 2 FHD/HD):**
   - Se emiten a través de la infraestructura global de **Amazon Prime Video / AIV OTT** (`otte.cache.aiv-cdn.net`) en formato **MPEG-DASH (`.mpd`)**.
   - No tienen bloqueo geográfico por IP europea/española, pero su vídeo está protegido mediante cifrado **ClearKey DRM**.
   - La aplicación AK47 utiliza ExoPlayer pasando las claves simétricas de ClearKey (`license_key: <KID>:<KEY>`).

2. **Reproducción directa en la Interfaz Web (Shaka Player) y Política de Seguridad W3C (HTTP vs HTTPS):**
   - El proxy incorpora el motor **Shaka Player** de Google junto a **HLS.js**.
   - **¿Por qué la pantalla se queda en negro en el navegador al abrirlo por HTTP?**
     - Los navegadores modernos (Google Chrome, Microsoft Edge, Firefox, Brave) siguen estrictamente la especificación oficial del W3C: **las APIs de descifrado DRM (Encrypted Media Extensions / EME) están bloqueadas por seguridad en conexiones HTTP que no sean `localhost`** (Contexto no seguro / Insecure Context).
     - Al acceder a la web mediante la IP local de tu NAS (`http://192.168.x.x:8899`), el navegador bloquea la creación del descifrador y genera el error DRM 6001 (`REQUESTED_KEY_SYSTEM_CONFIG_UNAVAILABLE`), impidiendo que el reproductor web descifre el vídeo.
     - En **Safari (macOS / iOS)**, Apple no implementa la especificación ClearKey (`org.w3.clearkey`), permitiendo únicamente FairPlay.
   - **Cómo activar la reproducción en Chrome / Edge en 3 pasos:**
     1. Abre una nueva pestaña en tu navegador e ingresa a:
        ```text
        chrome://flags/#unsafely-treat-insecure-origin-as-secure
        ```
     2. En el campo de texto, introduce la URL de tu proxy en el NAS (ejemplo: `http://192.168.1.100:8899`).
     3. Cambia el desplegable de **Disabled** a **Enabled** y pulsa el botón **Relaunch** (Reiniciar). ¡El navegador desbloqueará el descifrado ClearKey y los canales se reproducirán al instante!

3. **Reproducción en Aplicaciones Nativas (TiviMate, Kodi, OTT Navigator):**
   - Las aplicaciones nativas en Android, Android TV, Fire TV y decodificadores **no tienen las limitaciones de seguridad de los navegadores web**.
   - Acceden directamente a los descifradores por hardware de Android o Kodi.
   - Al importar la lista M3U unificada (`http://<IP_NAS>:8899/playlist.m3u`), el proxy entrega las directivas intactas:
     ```text
     #KODIPROP:inputstream.adaptive.license_type=clearkey
     #KODIPROP:inputstream.adaptive.license_key=<KID>:<KEY>
     ```
   - Esto garantiza reproducción fluida y aceleración por hardware en televisión sin necesidad de tocar ajustes de navegador.

4. **Canales de Eventos FanCode (HLS con Headers):**
   - Los eventos temporales en HLS que incluyen parámetros de cabeceras móviles (`|User-Agent=ReactNativeVideo...&Referer=https://fancode.com/`) son procesados por el proxy inteligente `cors_hls_proxy.py`, inyectando las cabeceras requeridas tanto en los sub-manifiestos como en los segmentos de vídeo `.ts`.

---

## 📺 Integración con Plex y Jellyfin (HDHomeRun)

El proxy incluye un emulador de sintonizador **SiliconDust HDHomeRun** virtual. Esto permite a **Plex Live TV & DVR** y **Jellyfin Live TV** detectar el proxy como una sintonizadora de televisión física en la red local.

### Pasos para configurar en Plex:
1. Abre Plex y ve a **Ajustes > Administrar > TV en vivo y DVR**.
2. Haz clic en **Añadir dispositivo**.
3. Si no lo detecta automáticamente por broadcast UDP, introduce manualmente la dirección:
   ```text
   http://<IP_NAS>:8899
   ```
4. Plex reconocerá el dispositivo como **M3U Stream Hub** con 4 sintonizadores virtuales.
5. Selecciona la guía de canales o carga el archivo XMLTV si dispones de EPG.

### Endpoints del Emulador:
- Información del dispositivo: `http://<IP_PROXY>:8899/discover.json`
- Parrilla de canales: `http://<IP_PROXY>:8899/lineup.json`
- Estado del sintonizador: `http://<IP_PROXY>:8899/lineup_status.json`

---

## 🎛 Funcionalidades de la Interfaz Web PRO

Accede desde tu navegador a `http://<IP_PROXY>:8899`:

1. **Reproductor HLS.js Optimizado:**
   - Reproducción sin interrupciones tanto de transmisiones HLS remotas como de canales P2P de AceStream.
   - Modo Picture-in-Picture (PiP) para mantener la ventana flotante.
2. **Perfiles de Búfer Dinámicos:**
   - 🛡️ **Ultra Estable (20s):** Máxima protección contra congelamientos y fluctuaciones de red P2P.
   - ⚡ **Equilibrado (12s):** Configuración estándar recomendada.
   - 🚀 **Baja Latencia (4s):** Reduce el retraso respecto a la emisión en directo.
3. **Amplificación de Audio (Web Audio API):**
   - Para emisiones con volumen de grabación bajo, amplifica la señal al **150%**, **200%** o **300%** sin distorsión.
4. **Telemetría P2P en Vivo (HUD):**
   - Muestra en tiempo real: número de *peers* conectados, velocidad de descarga en KB/s o MB/s y calidad de la señal del motor AceStream.
5. **Escáner de Repositorios GitHub:**
   - Introduce cualquier enlace de GitHub (ej. `usuario/repositorio`) para rastrear automáticamente todos los archivos `.m3u` y añadirlos como fuentes sincronizadas periódicamente.

---

## ❓ Resolución de Problemas Frecuentes (FAQ)

### 1. Error `{"detail":"index.html no encontrado"}`
* **Causa:** En la configuración de volúmenes de Docker, se montó una carpeta vacía del host sobre `/app` o `/app/templates`.
* **Solución:** Revisa tu `docker-compose.yml` y elimina la línea `- ./templates:/app/templates:ro`. El proxy cuenta además con una plantilla de rescate embebida en memoria que previene este error en las versiones actuales.

### 2. El motor AceStream dice "No responde"
* **Causa:** El firewall del sistema operativo (QuFirewall en QNAP) bloquea las peticiones desde el contenedor hacia la IP LAN física del NAS.
* **Solución:** Cambia el contenedor a `network_mode: host` y utiliza `http://127.0.0.1:6879`, o en modo puente utiliza `http://host.docker.internal:6879`.

### 3. La barra de información del canal se solapa con los botones
* **Solución:** Actualiza a la última versión de la imagen Docker (`ghcr.io/antonio-bravo/m3u/m3u-stream-proxy:latest`). La barra de información cuenta con diseño responsivo que reubica automáticamente los botones en una segunda línea para proteger el título y las etiquetas.

### 4. ¿Cómo actualizar la imagen en NAS Container Station?
1. Entra a **Container Station > Imágenes**.
2. Pulsa en **Extraer (Pull)** e introduce `ghcr.io/antonio-bravo/m3u/m3u-stream-proxy:latest`.
3. Reinicia tu aplicación o contenedor para aplicar los cambios.

### 5. Los canales DASH / ClearKey (FanCode, etc.) se quedan en pantalla negra en el navegador
* **Causa:** Política de seguridad estándar del W3C para navegadores web (**Insecure Context**). Las APIs de descifrado **EME (Encrypted Media Extensions)** y **ClearKey** están estrictamente bloqueadas en conexiones `http://` que no sean `localhost`. Al acceder desde tu ordenador a la IP del NAS (`http://192.168.x.x:8899`), el navegador bloquea la clave de descifrado y el vídeo no puede mostrar imagen.
* **Solución en Google Chrome / Microsoft Edge / Brave (en 4 pasos):**
  1. En una nueva pestaña del navegador, escribe en la barra de direcciones:
     ```text
     chrome://flags/#unsafely-treat-insecure-origin-as-secure
     ```
     *(En Microsoft Edge escribe: `edge://flags/#unsafely-treat-insecure-origin-as-secure`)*
  2. En la casilla de texto debajo de **Insecure origins treated as secure**, introduce la dirección completa de tu NAS con protocolo `http://` y puerto `:8899`:
     ```text
     http://<IP_DE_TU_NAS>:8899
     ```
     *(Ejemplo: `http://192.168.1.100:8899`)*
  3. Cambia el menú desplegable que dice **Disabled** a **Enabled**.
  4. Pulsa el botón azul **Relaunch** (Reiniciar) que aparece en la esquina inferior derecha.
  5. Al reabrirse el navegador, entra de nuevo a la interfaz web y pulsa en cualquier canal de FanCode o DASH: ¡se reproducirá de inmediato con vídeo y audio!

> [!NOTE]
> **¿Por qué en TiviMate, Kodi o la app de AK47 no hace falta configurar nada?**
> Porque los reproductores nativos de Android y decodificadores acceden directamente al chip de descifrado por hardware del dispositivo (ExoPlayer / MediaDrm), saltándose las restricciones de los navegadores web. Para verlos en tu televisor, simplemente importa `http://<IP_NAS>:8899/playlist.m3u` en TiviMate o Kodi.

> [!WARNING]
> **Safari en macOS e iOS:** Apple no implementa el estándar ClearKey (`org.w3.clearkey`) en Safari (solo soporta FairPlay). Para reproducir canales ClearKey en Mac o iPhone/iPad a través de la web, utiliza Google Chrome o una aplicación nativa como IINA o VLC.

