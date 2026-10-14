FROM python:3.12-slim

# Evitar generación de archivos .pyc y activar salida sin buffer
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

# Instalar dependencias del sistema mínimas necesarias
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código de la aplicación
COPY deobfuscator.py proxy_server.py ./

# Exponer el puerto del servidor
EXPOSE 8080

# Comprobación de salud del contenedor
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Ejecutar el servidor con Uvicorn (1 worker por defecto para optimizar RAM en capas gratuitas como Koyeb)
CMD ["sh", "-c", "uvicorn proxy_server:app --host 0.0.0.0 --port ${PORT:-8080} --workers ${WORKERS:-1}"]

