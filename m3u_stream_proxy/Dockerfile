FROM python:3.12-slim

# Evitar generación de archivos .pyc y activar salida sin buffer
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8899 \
    DATA_DIR=/app/data \
    LOCAL_PLAYLISTS_DIR=/app/local_playlists

WORKDIR /app

# Instalar dependencias mínimas del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código fuente y las plantillas
COPY m3u_parser.py github_scanner.py playlist_manager.py acestream_proxy.py cors_hls_proxy.py server.py ./
COPY templates/ ./templates/

# Crear directorios de persistencia
RUN mkdir -p /app/data /app/local_playlists

# Exponer el puerto del servidor
EXPOSE 8899

# Comprobación de salud
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8899/health || exit 1

# Ejecutar el servidor con Uvicorn
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8899} --workers ${WORKERS:-1}"]
