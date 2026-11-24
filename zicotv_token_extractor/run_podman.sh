#!/usr/bin/env bash
# ==============================================================================
# Script de construcción y ejecución con Podman para ZicoTV Token Extractor
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

IMAGE_NAME="zicotv-extractor"
CONTAINER_NAME="zicotv-extractor"
PORT=8085

echo "============================================================"
echo " Construyendo contenedor Podman: ${IMAGE_NAME}"
echo "============================================================"
podman build -t ${IMAGE_NAME} -f Containerfile .

# Detener contenedor anterior si existe
if podman ps -a --format "{{.Names}}" | grep -q "^${CONTAINER_NAME}$"; then
    echo "Deteniendo contenedor anterior..."
    podman stop ${CONTAINER_NAME} || true
    podman rm ${CONTAINER_NAME} || true
fi

echo "============================================================"
echo " Iniciando servicio en puerto ${PORT}..."
echo "============================================================"
podman run -d \
    --name ${CONTAINER_NAME} \
    -p ${PORT}:8085 \
    --restart unless-stopped \
    -v "${DIR}/output:/app/output:Z" \
    ${IMAGE_NAME}

echo ""
echo "✓ Contenedor iniciado exitosamente."
echo "  - Panel Web interactivo: http://localhost:${PORT}"
echo "  - Obtener token canal 1: http://localhost:${PORT}/token/1"
echo "  - Stream directo canal 1: http://localhost:${PORT}/stream/1"
echo "  - Lista M3U completa:    http://localhost:${PORT}/playlist.m3u"
echo ""
echo "Para sacar el token desde la terminal con Podman:"
echo "  podman exec -it ${CONTAINER_NAME} python extractor.py 1"
echo "============================================================"
