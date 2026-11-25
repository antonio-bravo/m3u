# 🔑 ZicoTV Token Extractor (Podman & Docker)

Microservicio ligero en contenedor para **extraer en tiempo real el token activo (con duración configurable: 20 min, 1h, 3h máx)** de cualquier canal de ZicoTV, redirigir emisiones directamente a tu reproductor (VLC, TiviMate, Kodi) y generar listas M3U.

La imagen se compila automáticamente para **`linux/amd64` y `linux/arm64`** y se publica en **GitHub Container Registry (GHCR)**.

---

## 🚀 Despliegue Rápido con Podman / Docker

### Opción 1: Descargar directamente la imagen desde GHCR
```bash
podman run -d \
  --name zicotv-extractor \
  -p 8085:8085 \
  --restart unless-stopped \
  -e DEFAULT_TOKEN_DURATION=3h \
  ghcr.io/antonio-bravo/m3u/zicotv-extractor:latest
```

### Opción 2: Compilar y ejecutar localmente con el script (1 clic)
```bash
cd zicotv_token_extractor
chmod +x run_podman.sh
./run_podman.sh
```

### Opción 3: Con Podman Compose o Docker Compose
```bash
podman-compose up -d
# o
docker compose up -d
```

---

## 🔒 Autenticación Opcional (Usuario y Contraseña)

Por defecto, el contenedor funciona **sin contraseña** (acceso abierto igual que siempre). Si quieres proteger el panel web y los endpoints con **HTTP Basic Auth**, solo tienes que definir las variables de entorno `AUTH_USER` y `AUTH_PASS`:

- **Con archivo `.env` (recomendado con Compose):**
  ```bash
  cd zicotv_token_extractor
  cp .env.example .env
  ```
  Edita `.env` y rellena `AUTH_USER` y `AUTH_PASS` (si los dejas vacíos o borras el `.env`, vuelve a funcionar sin contraseña).
- **En `docker run` / `podman run`:**
  ```bash
  docker run -d \
    --name zicotv-extractor \
    -p 8085:8085 \
    --env-file zicotv_token_extractor/.env \
    ghcr.io/antonio-bravo/m3u/zicotv-extractor:latest
  ```

> **Notas:**
> - El endpoint `/health` permanece accesible sin contraseña para que el `HEALTHCHECK` del contenedor siga funcionando.
> - En reproductores como VLC, Kodi o TiviMate puedes pasar las credenciales en la URL: `http://mi_usuario:mi_contraseña@localhost:8085/playlist.m3u?mode=redirect`.
> - Si eliminas o dejas vacías `AUTH_USER` / `AUTH_PASS`, vuelve a funcionar sin autenticación.

---

## ⏱️ Control de Duración del Token (20 min, 1h, 3h máx)

ZicoTV admite dos modalidades de token nativas upstream:
* **`3h` (180 min, 10800s - Máximo):** Modo HLS nativo. Los tokens duran 3 horas completas.
* **`20m` (20 min, 1200s - Estándar Web):** Modo reproductor web estándar.

Puedes indicar la duración que desees de cuatro formas:

1. **Desde el Panel Web (`http://localhost:8085`):**
   Usa la barra de selección superior: `[ 3 Horas (Máx) ]` `[ 1 Hora ]` `[ 20 Minutos ]`.
2. **Por parámetro en la API:**
   ```bash
   # Token de 20 minutos
   curl "http://localhost:8085/token/1?duration=20m"

   # Token de 3 horas (máximo)
   curl "http://localhost:8085/token/1?duration=3h"

   # Token de 1 hora
   curl "http://localhost:8085/token/1?duration=1h"
   ```
3. **Por parámetro en el Stream Redirect 302:**
   ```bash
   # URL para VLC / TiviMate con token de 3 horas
   http://localhost:8085/stream/1?duration=3h

   # URL para VLC con token de 20 minutos
   http://localhost:8085/stream/1?duration=20m
   ```
4. **Por variable de entorno en el contenedor:**
   ```bash
   -e DEFAULT_TOKEN_DURATION=20m
   # o
   -e DEFAULT_TOKEN_DURATION=3h
   ```

---

## 🖥️ Endpoints del Microservicio (`http://localhost:8085`)

| Endpoint | Descripción | Ejemplo de Uso |
| :--- | :--- | :--- |
| **`/`** | **Panel Web Interactivo** con selector de duración, buscador y botones de copia con 1 clic. | Abrir en navegador `http://localhost:8085` |
| **`/token/{canal_id}`** | Devuelve el **token activo**, expiración, TTL y fuentes en JSON. | `curl http://localhost:8085/token/1?duration=3h` |
| **`/stream/{canal_id}`** | **Redirección HTTP 302 directa** al enlace HLS con token activo. No caduca en el reproductor. | Poner en VLC / TiviMate: `http://localhost:8085/stream/1` |
| **`/playlist.m3u`** | Genera la lista M3U completa con los enlaces de todos los canales. | `http://localhost:8085/playlist.m3u?mode=redirect&duration=3h` |
| **`/channels`** | Lista todos los canales y eventos deportivos en vivo del día. | `curl http://localhost:8085/channels` |

---

## ⌨️ Uso desde la Línea de Comandos (CLI)

También puedes extraer el token de un canal directamente desde la terminal indicando la duración deseada:

```bash
# Token de 3 horas (por defecto) para canal 1 (DAZN LaLiga)
podman exec -it zicotv-extractor python extractor.py 1 -d 3h

# Token de 20 minutos para canal 1
podman exec -it zicotv-extractor python extractor.py 1 -d 20m

# O sin tener el contenedor arrancado (ejecución efímera):
podman run --rm ghcr.io/antonio-bravo/m3u/zicotv-extractor:latest python extractor.py 1 -d 3h
```

**Salida de ejemplo:**
```text
============================================================
Extrayendo Token para Canal 1 (Duración: 3h)...
============================================================
Canal:      [1] D@ZN L@Lig@
CDN:        CDN 6 Draco
Duración:   3h (10800s)
Fuentes:    7 CDNs disponibles

TOKEN ACTIVO:
1b331788715f*************************************************************-1795529793-

URL HLS COMPLETA:
https://cdn6.zicotv.cc/dazn-liga-1/index.m3u8?token=1b331788715f*************************************************************-1795529793-
============================================================
```

---

## 📺 Canales Frecuentes
- **1:** D@ZN L@Lig@
- **2:** D@ZN 1 Sp@in
- **3:** D@ZN 2 Spain
- **6:** M0vist@r Lig@ de C@mp30n3s
- **9:** M0vist@r #V@mos
- **11:** D@ZN F1 Sp@in
- **12:** L@Lig@ TV Hyperm0ti0n
- **15:** D@ZN L@Lig@ 2
- **19:** M0vist@r D3port3s
- **25:** NB@ 1
- **28:** M0vist@r P/ys+
