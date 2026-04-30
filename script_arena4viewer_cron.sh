#!/bin/bash
# =============================================================================
# Arena4Viewer Cron Job
# =============================================================================
# Este script se ejecuta cada hora para generar la lista de canales
# 
# Para configurar el cron job:
#   crontab -e
# 
# Añadir esta línea para ejecutar cada hora:
#   0 * * * * /ruta/completa/a/script_arena4viewer_cron.sh >> /ruta/completa/a/arena4viewer_cron.log 2>&1
#
# =============================================================================

# Configuración
SCRIPT_DIR="/Users/antonio-bravo/git/m3u"
PYTHON_BIN="/usr/bin/python3"
LOG_FILE="$SCRIPT_DIR/arena4viewer_cron.log"

# Cambiar al directorio del script
cd "$SCRIPT_DIR"

# Función de logging
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Iniciar
log "=========================================="
log "Iniciando Arena4Viewer Cron Job"
log "=========================================="

# Ejecutar el script de Python
if "$PYTHON_BIN" "$SCRIPT_DIR/script_arena4viewer.py" >> "$LOG_FILE" 2>&1; then
    log "✓ Ejecución completada exitosamente"
else
    log "⚠ La ejecución tuvo errores (pero el ciclo continuó)"
fi

log "=========================================="
log "Fin del Cron Job"
log "=========================================="