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

## 📋 Exportación de Listas M3U y Filtrado Inteligente

El servidor proxy genera listas M3U unificadas y optimizadas a través de los endpoints `/playlist.m3u` y `/channels.m3u`, preparadas para cualquier cliente IPTV (**TiviMate, OTT Navigator, Kodi, IPTV Smarters, VLC, GSE Smart IPTV**) o proxies de catálogo (**Threadfin, xTeVe**).

### 🔍 Parámetros de Consulta (Query Strings) Disponibles

Cargar más de 50.000 canales en un televisor, decodificador o servidor multimedia con recursos limitados satura la memoria RAM y bloquea las bases de datos. El proxy permite solicitar únicamente los canales que realmente necesitas aplicando parámetros de filtrado en la URL:

| Parámetro Query | Ejemplo de URL | Descripción y Caso de Uso |
| :--- | :--- | :--- |
| **⭐ Solo Favoritos**<br>`?fav=1`<br>`?favorites=true` | `http://<IP>:8899/playlist.m3u?fav=1` | **Recomendado para Kodi, Plex y Smart TV.** Exporta únicamente los canales marcados con estrella en el panel web. Carga en 1 segundo y evita bloqueos. |
| **🔒 Solo DASH / DRM**<br>`?type=dash` | `http://<IP>:8899/playlist.m3u?type=dash` | Exporta únicamente emisiones MPEG-DASH con o sin cifrado ClearKey (ZDF, FanCode, etc.), inyectando las etiquetas `#KODIPROP` para Kodi. |
| **⚡ Solo AceStream**<br>`?type=acestream`<br>`?type=ace` | `http://<IP>:8899/playlist.m3u?type=acestream` | Exporta únicamente canales P2P de AceStream en formato HLS proxificado. |
| **📡 Solo HLS**<br>`?type=hls` | `http://<IP>:8899/playlist.m3u?type=hls` | Exporta únicamente transmisiones HLS estándar (`.m3u8`) directas o con proxy CORS. |
| **🔍 Búsqueda por Texto**<br>`?search=<palabra>` | `http://<IP>:8899/playlist.m3u?search=spain` | Filtra canales que contengan el término en su nombre o categoría (ej. `spain`, `laliga`, `f1`, `dazn`). No distingue mayúsculas. |
| **📁 Por Categoría**<br>`?group=<grupo>` | `http://<IP>:8899/playlist.m3u?group=Sports+HD` | Filtra por el nombre exacto del grupo M3U (`group-title`). Los espacios se codifican con `+` o `%20`. |
| **📦 Por Lista / Fuente**<br>`?source_id=<id>` | `http://<IP>:8899/playlist.m3u?source_id=lista_ak47sports.m3u` | Exporta únicamente los canales procedentes de un archivo local o repositorio específico. |
| **🔢 Límite de Canales**<br>`?limit=<numero>` | `http://<IP>:8899/playlist.m3u?limit=100` | Limita la lista a los primeros *N* canales encontrados. |
| **🎬 Manifiesto Nativo**<br>`?format=manifest` | `http://<IP>:8899/playlist.m3u?format=manifest&server_id=direct` | Enlaces HLS directos al motor AceStream (`/ace/manifest.m3u8?id=...`). Traduce `localhost` a la IP LAN real para TVs. |
| **📺 MPEG-TS Directo**<br>`?format=getstream` | `http://<IP>:8899/playlist.m3u?format=getstream&server_id=direct` | Enlaces directos HTTP MPEG-TS al motor AceStream (`/ace/getstream?id=...`). |
| **🔄 MPEG-TS Proxificado**<br>`?format=ts` | `http://<IP>:8899/playlist.m3u?format=ts` | Enlaces procesados por el proxy convertidos en flujo MPEG-TS continuo (`/ace/stream/{hash}`). Ideal para Plex y xTeVe. |

> [!TIP]
> **Combinación de parámetros (Multi-filtro con `&`):**  
> Puedes combinar múltiples parámetros libremente para crear listas hiper-específicas:
> - **Solo canales favoritos de España:**  
>   `http://<IP>:8899/playlist.m3u?fav=1&search=spain`
> - **Solo favoritos con formato MPEG-TS (para Plex/Threadfin):**  
>   `http://<IP>:8899/playlist.m3u?fav=1&format=ts`
> - **Canales DASH ClearKey favoritos:**  
>   `http://<IP>:8899/playlist.m3u?fav=1&type=dash`
> - **Los 50 primeros canales de deportes:**  
>   `http://<IP>:8899/playlist.m3u?group=Deportes&limit=50`
> - **Canales de F1 en listas específicas:**  
>   `http://<IP>:8899/playlist.m3u?search=f1&limit=20`

---

## 🔐 Compatibilidad con Listas AK47, FanCode y Canales DASH / ClearKey DRM

El proxy incluye compatibilidad integral con listas complejas como **AK47Sports** y canales protegidos como **FanCode**, **ZDF**, etc.:

### 1. ¿Cómo funcionan los canales de FanCode en AK47 sin VPN?
* **Canales 24/7 (FANCODE 1, FANCODE 2 FHD/HD):**
  - Se emiten a través de la infraestructura global de **Amazon Prime Video / AIV OTT** (`otte.cache.aiv-cdn.net`) en formato **MPEG-DASH (`.mpd`)**.
  - No tienen bloqueo geográfico por IP europea/española, pero su vídeo está protegido mediante cifrado **ClearKey DRM**.
  - La aplicación AK47 utiliza ExoPlayer pasando las claves simétricas de ClearKey (`license_key: <KID>:<KEY>`).

### 2. Reproducción directa en la Interfaz Web (Shaka Player) y Política de Seguridad W3C (HTTP vs HTTPS)
* El proxy incorpora el motor **Shaka Player** de Google junto a **HLS.js**.
* **¿Por qué la pantalla se queda en negro en el navegador al abrirlo por HTTP?**
  - Los navegadores modernos (Google Chrome, Microsoft Edge, Firefox, Brave) siguen estrictamente la especificación oficial del W3C: **las APIs de descifrado DRM (Encrypted Media Extensions / EME) están bloqueadas por seguridad en conexiones HTTP que no sean `localhost`** (Contexto no seguro / Insecure Context).
  - Al acceder a la web mediante la IP local de tu NAS (`http://192.168.x.x:8899`), el navegador bloquea la creación del descifrador y genera el error DRM 6001 (`REQUESTED_KEY_SYSTEM_CONFIG_UNAVAILABLE`), impidiendo que el reproductor web descifre el vídeo.
  - En **Safari (macOS / iOS)**, Apple no implementa la especificación ClearKey (`org.w3.clearkey`), permitiendo únicamente FairPlay.
* **Cómo activar la reproducción en Chrome / Edge en 3 pasos:**
  1. Abre una nueva pestaña en tu navegador e ingresa a:
     ```text
     chrome://flags/#unsafely-treat-insecure-origin-as-secure
     ```
  2. En el campo de texto, introduce la URL de tu proxy en el NAS (ejemplo: `http://192.168.1.100:8899`).
  3. Cambia el desplegable de **Disabled** a **Enabled** y pulsa el botón **Relaunch** (Reiniciar). ¡El navegador desbloqueará el descifrado ClearKey y los canales se reproducirán al instante!

### 3. Reproducción en Kodi en Android TV y Clientes Nativos (TiviMate, OTT Navigator)
* Las aplicaciones nativas en Android, Android TV, Fire TV y decodificadores **no sufren las limitaciones de seguridad de los navegadores web** porque acceden directamente a los descifradores por hardware del dispositivo (ExoPlayer / MediaDrm / InputStream Adaptive).
* **Directivas oficiales de Kodi generadas por el proxy:**
  Kodi rechaza el uso de `license_key` en canales ClearKey (ya que espera una URL de servidor). Por ello, el proxy inyecta automáticamente la sintaxis estándar exigida por **InputStream Adaptive**:
  ```text
  #KODIPROP:inputstream=inputstream.adaptive
  #KODIPROP:inputstream.adaptive.manifest_type=mpd
  #KODIPROP:inputstream.adaptive.drm_legacy=org.w3.clearkey|<KID>:<KEY>
  #KODIPROP:inputstream.adaptive.drm={"org.w3.clearkey":{"keyids":{"<KID>":"<KEY>"}}}
  ```
* **Configuración paso a paso en Kodi (Android TV):**
  1. **Activar InputStream Adaptive:** Ve a *Ajustes > Add-ons > Mis Add-ons > VideoPlayer InputStream > InputStream Adaptive* y verifica que esté **Habilitado**.
  2. **Configurar PVR IPTV Simple Client:** Ve a *Ajustes > Add-ons > Clientes PVR > PVR IPTV Simple Client > Configuración*.
     - En la pestaña **General**, establece la **Ubicación** en **Ruta remota (dirección de Internet)** *(evita la confusa ventana de "añadir ubicación de red y protocolos")*.
     - En **URL de la lista**, introduce:
       ```text
       http://<IP_DE_TU_SERVIDOR>:8899/playlist.m3u?fav=1
       ```
       *(Usa `?fav=1` para cargar solo los canales que marques con estrella en la web y evitar el lag de 50.000 canales).*
  3. **Reiniciar Kodi:** Los canales se sincronizarán y reproducirán de forma fluida y con aceleración por hardware.

### 4. Canales de Eventos FanCode (HLS con Headers)
* Los eventos temporales en HLS que incluyen parámetros de cabeceras móviles (`|User-Agent=ReactNativeVideo...&Referer=https://fancode.com/`) son procesados por el proxy inteligente `cors_hls_proxy.py`, inyectando las cabeceras requeridas tanto en los sub-manifiestos como en los segmentos de vídeo `.ts`.

---

## 📺 Integración con Plex, Jellyfin y Emby (Live TV & DVR)

El proxy incorpora una emulación completa del protocolo de sintonizadores digitales **SiliconDust HDHomeRun**. Esto permite a **Plex Media Server (Plex Live TV & DVR)**, **Jellyfin** y **Emby** detectar el contenedor como una tarjeta sintonizadora de TV física conectada a tu red local.

Existen **dos métodos** para integrar tus listas en Plex:

---

### Método 1: Sintonizador HDHomeRun Emulado (Recomendado y Directo)

En este método no necesitas instalar herramientas intermedias como xTeVe o Threadfin. Plex se conecta directamente a los endpoints HDHomeRun del contenedor.

```
┌─────────────────────────┐             ┌───────────────────────────────────┐
│       PLEX SERVER       │  HDHomeRun  │         M3U STREAM PROXY          │
│  (Live TV & Grabador)   │◄───────────►│  /discover.json                   │
│                         │   MPEG-TS   │  /lineup.json?favorites=true      │
│  Tuner: "M3U Stream Hub"│◄────────────│  /auto/v1, /auto/v2 ...           │
└─────────────────────────┘             └───────────────────────────────────┘
```

#### Paso a Paso en Plex:
1. Abre tu navegador e inicia sesión en tu **Plex Web** (`http://<IP_PLEX>:32400/web`).
2. Entra en **Ajustes ⚙️** (esquina superior derecha) ➔ en el menú izquierdo baja a **Administrar** ➔ **TV en vivo y DVR**.
3. Haz clic en el botón amarillo **CONFIGURAR SINTONIZADOR DE PLEX** (o *Añadir dispositivo*).
4. Si Plex no lo detecta automáticamente por broadcast UDP, pulsa en el enlace:  
   👉 **"¿No ve su dispositivo? Especifique la dirección IP manualmente"**.
5. Escribe la dirección del contenedor (con protocolo y puerto):
   ```text
   http://<IP_DE_TU_NAS_O_PC>:8899
   ```
   *(Ejemplo: `http://192.168.1.100:8899`)*.
6. Pulsa **Conectar**. Plex identificará el dispositivo como:
   * **Dispositivo:** `SiliconDust HDTC-2US`
   * **Nombre:** `M3U Stream Hub`
   * **Sintonizadores:** 4 (o los configurados en Ajustes).
7. Selecciona tipo de antena: **Cable** (o Antena) y continúa.
8. En el paso de **Guía de Canales (EPG)**:
   * Puedes vincular una guía EPG XMLTV si la tienes, o seleccionar la guía de canales de tu país y código postal para asociar los canales que coincidan.

#### Endpoints HDHomeRun y Parámetros URL para Plex:

| Endpoint | Ejemplo de URL | Función en Plex / Jellyfin |
| :--- | :--- | :--- |
| **Descriptor del Sintonizador** | `http://<IP>:8899/discover.json` | Proporciona a Plex los metadatos del dispositivo, número de sintonizadores y URL de la parrilla. |
| **Parrilla de Canales** | `http://<IP>:8899/lineup.json` | Lista JSON con los números de guía (`GuideNumber`), nombres y enlaces de sintonía. |
| **⭐ Parrilla Solo Favoritos** | `http://<IP>:8899/lineup.json?favorites=true` | **Imprescindible:** Limita la parrilla a los favoritos de la web para evitar que Plex se colapse con miles de canales. |
| **📁 Parrilla por Grupo** | `http://<IP>:8899/lineup.json?group=Deportes` | Entrega a Plex únicamente los canales de esa categoría. |
| **🔢 Parrilla con Límite** | `http://<IP>:8899/lineup.json?limit=100` | Limita la cantidad de canales que ve el sintonizador de Plex. |
| **Estado del Sintonizador** | `http://<IP>:8899/lineup_status.json` | Simula el estado de escaneo de canales físicos de SiliconDust. |
| **Auto-descubrimiento UPnP** | `http://<IP>:8899/device.xml` | Anuncio UPnP/SSDP para que Plex lo autodetecte en la misma subred. |
| **Emisión del Canal** | `http://<IP>:8899/auto/v1` | URL donde Plex sintoniza el canal 1. Para AceStream, entrega un flujo continuo MPEG-TS limpio. |

#### Ajustes del Sintonizador en la Web del Proxy (`⚙️ Ajustes & AceStream`):
Desde la interfaz web del proxy puedes personalizar cómo interactúa Plex:
* **Número de Sintonizadores Virtuales (1 a 32):** Define cuántos usuarios de Plex pueden ver o grabar canales simultáneamente (por defecto 4).
* **Modo de Parrilla:**
  * `⭐ Solo Favoritos (Recomendado)`: Solo envía a Plex los canales que marques con estrella.
  * `⚡ Solo AceStream`: Solo canales P2P.
  * `🌐 Todos los Canales`: Con límite máximo ajustable.
* **Límite Máximo de Canales:** Configurable de 10 a 2000 canales (por defecto 300) para asegurar la máxima fluidez en Plex.

---

### Método 2: Vía Proxies de Canales (Threadfin, xTeVe, Tunarr)

Si utilizas **Threadfin** (sucesor moderno de xTeVe) o **xTeVe** para unificar múltiples proveedores IPTV, filtrar listas y mapear guías XMLTV avanzadas antes de entregarlas a Plex:

1. En Threadfin / xTeVe, crea una nueva fuente **M3U**.
2. En la casilla **M3U Playlist URL**, introduce el enlace filtrado del contenedor:
   ```text
   http://<IP_DE_TU_SERVIDOR>:8899/playlist.m3u?fav=1&format=ts
   ```
   > **💡 ¿Por qué `&format=ts`?**  
   > El parámetro `format=ts` le indica al proxy que sirva los canales AceStream en formato MPEG-TS continuo. Plex Media Server tiene soporte nativo perfecto para contenedores MPEG-TS, evitando problemas de transcodificación asociados a listas HLS segmentadas.
3. Asigna tu archivo XMLTV de guía de programación (EPG).
4. Threadfin / xTeVe expondrá su propio sintonizador virtual que conectarás en Plex en el puerto 34400.

---

### 🌐 Endpoints Directos de Reproducción y API REST

Además de las listas M3U y la emulación HDHomeRun, el contenedor expone endpoints individuales y una API REST completa:

#### Streams Individuales:
* **Stream HLS Proxificado:** `http://<IP>:8899/live/{channel_id}.m3u8`
* **Stream DASH MPD Directo:** `http://<IP>:8899/live/{channel_id}.mpd`
* **Stream MPEG-TS Continuo:** `http://<IP>:8899/live/{channel_id}.ts`
* **Manifiesto AceStream HLS:** `http://<IP>:8899/ace/manifest/{acestream_id}.m3u8`
* **Stream AceStream MPEG-TS:** `http://<IP>:8899/ace/stream/{acestream_id}`
* **Manifiesto AceStream HLS con Idioma Preferido:** `http://<IP>:8899/ace/manifest/{acestream_id}.m3u8?preferred_audio_language=spa`

#### Endpoints de la API REST (para integraciones con Home Assistant o scripts):
* `GET /health` ➔ Chequeo de salud del contenedor (`status: ok`, canales activos, uptime).
* `GET /api/channels` ➔ Catálogo paginado con filtros (`?search=`, `?group=`, `?favorites=1`, `?page=1`).
* `GET /api/channels/{channel_id}/sources` ➔ Devuelve todas las transmisiones y fuentes alternativas encontradas en otras listas para el mismo canal.
* `GET /api/favorites` ➔ Array JSON con los IDs de todos los canales favoritos.
* `POST /api/favorites/{channel_id}` ➔ Marca o desmarca un canal como favorito.
* `GET /api/groups` ➔ Lista de todas las categorías disponibles y conteo de canales.
* `GET /api/acestream/check` ➔ Estado y latencia en milisegundos de los motores AceStream (Normal y VPN).
* `GET /api/hdhomerun/status` ➔ Configuración actual del emulador HDHomeRun para Plex.

---

## 🎛 Funcionalidades de la Interfaz Web PRO

Accede desde tu navegador a `http://<IP_PROXY>:8899`:

1. **Picture-in-Picture (PiP) Dual y Multivisión Flotante:**
   - **PiP Nativo del Navegador:** Botón `🔲 PiP` para desacoplar el video en una ventana flotante del sistema operativo (compatible con Chrome, Firefox, Safari WebKit, Android y macOS).
   - **Mini-Reproductor In-App Arrastrable:** Ventana flotante integrada en la propia página para ver un segundo canal simultáneamente mientras navegas por la lista de canales.
   - **Conmutador de Foco de Audio (1-Click Swap):** Botón `🔇 / 🔊 Audio` en la cabecera del PiP para alternar el sonido entre el reproductor principal y el mini-reproductor en 1 clic.
   - **Intercambio Rápido (`⇄ Swap`):** Pasa al instante el canal que estás viendo en PiP a la pantalla principal y viceversa sin recargar el navegador.
   - **Botón `🔲` en Tarjetas de Canales:** Lanza cualquier canal de la lista directamente al mini-reproductor PiP sin interrumpir el canal principal.

2. **Selector de Fuentes Alternativas Multi-Proveedor (`📡 N fuentes ▾`):**
   - Cuando un canal cuenta con múltiples emisiones entre las listas cargadas (ej. enlace directo M3U, stream en AceStream P2P o mirror en DaddyLive), aparece una insignia con el conteo de fuentes.
   - Al pulsar se despliega el menú con todas las opciones detectadas, permitiendo reproducir la alternativa en el reproductor principal (`▶ Ver`) o abrirla en la ventana flotante (`🔲 PiP`) para comparar señales o ver dos cámaras a la vez.

3. **Selector Inteligente de Pistas de Audio (Multi-audio / Idiomas / Radio):**
   - Detección automática en tiempo real de pistas de audio alternativas en emisiones **HLS, DASH y AceStream**.
   - Para canales **AceStream P2P**, permite solicitar idiomas directamente al motor AceStream (`preferred_audio_language=spa|eng`) aunque el manifiesto original sea de rendición única.
   - Menú interactivo con selector dinámico para cambiar entre diferentes idiomas (Español, Inglés, etc.), comentaristas de radio deportiva o sonido ambiente del estadio sin cortes en la imagen.
   - Indicador de número de pistas con badges de canales (Stereo 2.0, 5.1 Surround) y monitorización en el HUD de estadísticas.

4. **Perfiles de Búfer Dinámicos:**
   - 🛡️ **Ultra Estable (20s):** Máxima protección contra congelamientos y fluctuaciones de red P2P.
   - ⚡ **Equilibrado (12s):** Configuración estándar recomendada.
   - 🚀 **Baja Latencia (4s):** Reduce el retraso respecto a la emisión en directo.

5. **Amplificación de Audio (Web Audio API):**
   - Para emisiones con volumen de grabación bajo, amplifica la señal al **150%**, **200%** o **300%** sin distorsión.

6. **Telemetría P2P en Vivo (HUD):**
   - Muestra en tiempo real: número de *peers* conectados, velocidad de descarga en KB/s o MB/s, pista de audio activa y calidad de la señal del motor AceStream.

7. **Escáner de Repositorios GitHub:**
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

### 6. Micro-cortes, congelamiento o falta de audio en canales DASH / ClearKey en la web
* **Causas identificadas:**
  1. **Retardo de CDN:** Canales como ZDF requieren un retardo de publicación de 24 segundos en la CDN, mientras que canales como FanCode traen configurados apenas 2 segundos. Forzar un retardo erróneo causaba peticiones a fragmentos no publicados y congelamiento.
  2. **Interferencia de Web Audio:** Al arrancar la página, el navegador silenciaba el audio si se enrutaba por Web Audio API debido a restricciones DRM de la especificación W3C.
  3. **Timeline gigante (600.000 horas):** Shaka Player por defecto asignaba un valor $2^{31}-1$ a la duración en emisiones con base de tiempo fija.
* **Solución aplicada:**
  1. Preprocesador dinámico de manifiestos (`manifestPreprocessor` y `manifestPreprocessorTXml`): detecta automáticamente si el canal necesita un retardo mayor (preserva los 24s de ZDF) o si necesita elevarse a 18s seguros (FanCode/Otte).
  2. Audio nativo directo sin filtros intermediarios en el arranque, garantizando que el audio oficial del canal suene a través de los altavoces.
  3. Se activó `infiniteLiveStreamDuration: true`, mostrando una línea de tiempo limpia y el indicador nativo de directo (**LIVE**).
  4. La auto-recuperación ahora realiza micro-ajustes estrictamente dentro del búfer descargado, sin saltar al vacío ni perder las claves DRM.

### 7. En Kodi sale el error *"One or more items failed to play. Check the log for more information"*
* **Causa:** 
  1. La lista no especificaba `#KODIPROP:inputstream=inputstream.adaptive`, por lo que Kodi intentaba abrir el stream con ffmpeg (que no soporta descifrado CENC ClearKey).
  2. O bien se utilizaba la etiqueta `inputstream.adaptive.license_key=...`, la cual en Kodi está reservada exclusivamente para URLs de servidores y provoca un fallo crítico al recibir claves simétricas de ClearKey.
* **Solución:**
  * El proxy ahora inyecta automáticamente la sintaxis oficial requerida por InputStream Adaptive:
    ```text
    #KODIPROP:inputstream=inputstream.adaptive
    #KODIPROP:inputstream.adaptive.manifest_type=mpd
    #KODIPROP:inputstream.adaptive.drm_legacy=org.w3.clearkey|<KID>:<KEY>
    #KODIPROP:inputstream.adaptive.drm={"org.w3.clearkey":{"keyids":{"<KID>":"<KEY>"}}}
    ```
  * Asegúrate de que el add-on **InputStream Adaptive** esté en estado **Habilitado** en *Ajustes > Add-ons > Mis Add-ons > VideoPlayer InputStream > InputStream Adaptive*.

### 8. Kodi se congela o tarda minutos en arrancar al cargar la lista (más de 50.000 canales)
* **Causa:** Las listas mundiales contienen decenas de miles de canales de todos los países. Kodi guarda cada canal en una base de datos local SQLite en la memoria flash de Android TV, saturando la RAM del televisor.
* **Soluciones recomendadas:**
  1. **Usar el filtro de favoritos (La más rápida):** Marca tus canales preferidos con una estrella en la web del proxy y en Kodi pon:
     ```text
     http://<IP_SERVIDOR>:8899/playlist.m3u?fav=1
     ```
     Kodi cargará únicamente esos canales en 1 segundo.
  2. **Usar el add-on IPTV Merge de SlyGuy:** Permite añadir la lista completa pero desmarcar con un solo clic los países o grupos que no quieras ver antes de transferirlos a IPTV Simple Client.
  3. **Usar filtros de búsqueda por URL:** Por ejemplo, `http://<IP_SERVIDOR>:8899/playlist.m3u?search=spain` o `?type=dash`.

> [!WARNING]
> **Safari en macOS e iOS:** Apple no implementa el estándar ClearKey (`org.w3.clearkey`) en Safari (solo soporta FairPlay). Para reproducir canales ClearKey en Mac o iPhone/iPad a través de la web, utiliza Google Chrome o una aplicación nativa como IINA o VLC.

