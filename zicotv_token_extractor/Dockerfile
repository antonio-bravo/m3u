FROM python:3.12-alpine

LABEL maintainer="Antigravity"
LABEL description="ZicoTV Token Extractor & HLS Redirect Service para Podman / Docker"

WORKDIR /app

# Instalar dependencias del sistema mínimas
RUN apk add --no-cache curl ca-certificates

# Copiar requirements e instalar
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar aplicación
COPY extractor.py .

# Crear directorio de salida para listas M3U exportadas
RUN mkdir -p /app/output

# Puerto por defecto del microservicio
EXPOSE 8085

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8085/health || exit 1

# Comando por defecto: arrancar servidor FastAPI en puerto 8085
CMD ["uvicorn", "extractor:app", "--host", "0.0.0.0", "--port", "8085"]
