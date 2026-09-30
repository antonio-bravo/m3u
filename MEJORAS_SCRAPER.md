# Mejoras del Scraper LiveTV.sx

## Problema Original
El workflow de GitHub Actions fallaba con error 503 al intentar extraer eventos deportivos de LiveTV.sx. Este error indica que el sitio tiene protección anti-scraping (probablemente Cloudflare) que detecta y bloquea tráfico automatizado.

## Mejoras Implementadas

### 1. Sistema de Reintentos Robusto
- **HTTPAdapter con Retry Strategy**: Configuración automática de reintentos para códigos de error 429, 500, 502, 503, 504
- **Backoff exponencial**: Tiempo de espera que aumenta progresivamente (2x) entre reintentos
- **Máximo 5 intentos automáticos** por cada petición fallida

### 2. Rotación de User-Agents
- **Lista de 6 User-Agents diferentes**: Simula navegadores reales (Chrome, Firefox, Safari, Edge)
- **Rotación en cada petición**: Cada request usa un User-Agent aleatorio
- **Headers completos y realistas**: Accept, Accept-Language, Sec-Fetch-* headers

### 3. Control de Tasa de Peticiones (Rate Limiting)
- **Delay mínimo de 2-4 segundos** entre peticiones
- **Delay adicional para error 503**: Espera progresiva (10s, 20s, 30s) antes de reintentar
- **Modo secuencial en lugar de paralelo**: Reduce la carga y probabilidad de bloqueo
- **Delays aleatorios**: Usa `random.uniform()` para simular comportamiento humano

### 4. Estrategia de Fallback Mejorada
- **Si falla la extracción principal**: Activa automáticamente estrategia alternativa
- **No falla completamente**: Intenta extraer al menos algunos deportes
- **Continúa con datos parciales**: Si algunos deportes fallan, procesa los que sí funcionaron

### 5. Manejo de Errores Mejorado
- **Método `_make_request()` centralizado**: Todo el manejo de errores en un solo lugar
- **Logging detallado**: Registra cada intento, error y tiempo de espera
- **Contador de peticiones**: Tracking del número total de requests realizados
- **Control de tiempo entre peticiones**: Evita hacer requests demasiado rápidos

### 6. Configuración del Workflow
- **Límite de páginas**: Reducido a 10 en lugar de procesar todas
- **Un solo worker**: Procesamiento completamente secuencial
- **Continue-on-error**: El workflow no falla completamente si hay errores parciales
- **Mensajes informativos**: Mejor feedback sobre el estado del proceso

## Cambios en el Código

### Antes
```python
response = self.session.get(url, verify=False, timeout=30)
if response.status_code != 200:
    logging.error(f"Error {response.status_code}")
    return False
```

### Después
```python
response = self._make_request(url, max_retries=3)
if not response:
    logging.warning("Intentando con estrategia de fallback...")
    self.fallback_sports_detection()
    return len(self.sports_mapping) > 0
```

## Parámetros Recomendados

### Para uso local (desarrollo)
```bash
python script_lista_livetv_sx.py --pages 5 --workers 1 --debug
```

### Para GitHub Actions (producción)
```bash
python script_lista_livetv_sx.py --pages 10 --workers 1
```

### Para máximo scraping (usar con cuidado)
```bash
python script_lista_livetv_sx.py --pages 20 --workers 1
```

## Consideraciones Importantes

### Protección Anti-Bot
LiveTV.sx tiene protección activa contra scraping. Estas mejoras reducen la probabilidad de bloqueo, pero no la eliminan completamente.

### Tiempos de Ejecución
- **Con delays aumentados**: El scraping toma más tiempo pero tiene mayor tasa de éxito
- **Modo secuencial**: Más lento que paralelo, pero más seguro
- **Estimado**: ~5-10 segundos por página de deporte

### Alternativas Si Sigue Fallando

1. **Usar un proxy rotativo**
2. **Implementar Selenium/Playwright** para ejecutar JavaScript
3. **Usar servicios de scraping** como ScraperAPI o Bright Data
4. **Implementar cookies y sesiones persistentes**
5. **Reducir aún más la frecuencia** del cron job

## Testing

Se incluye un script de prueba `test_scraper.py` para verificar:
- Generación de headers aleatorios
- Creación de sesión con reintentos
- Peticiones HTTP mejoradas
- Conectividad básica al sitio

```bash
python test_scraper.py
```

## Monitoreo

Revisar los logs del workflow para ver:
- ✅ Número de deportes detectados
- ✅ Eventos extraídos por deporte
- ✅ Total de peticiones realizadas
- ⚠️ Páginas que fallaron
- ❌ Errores críticos

## Próximos Pasos

Si el error 503 persiste en GitHub Actions:
1. Considerar usar GitHub Actions con proxy
2. Implementar caché de eventos para no depender de scraping en tiempo real
3. Evaluar APIs alternativas o fuentes de datos oficiales
4. Implementar sistema de scraping distribuido
