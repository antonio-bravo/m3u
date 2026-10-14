import io
import gzip
import time
import json
import base64
import logging
from typing import Optional
from PIL import Image

logger = logging.getLogger("tvnow_deobfuscator")

def deobfuscate_segment(data: bytes) -> bytes:
    """
    Desofusca un segmento devuelto por CDN (TikTok u otros).
    Si los datos están empaquetados como una imagen PNG con cabecera TIKTIKPX,
    extrae los píxeles RGB reales, desempaqueta la carga y descomprime con GZIP
    para devolver los paquetes de transporte MPEG-TS limpios.
    """
    # Si no tiene la firma binaria de un PNG (\x89PNG\r\n\x1a\n), devolver tal cual
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data

    try:
        # Abrir imagen y obtener píxeles en bruto descomprimiendo el filtrado PNG
        im = Image.open(io.BytesIO(data))
        raw_pixels = im.tobytes()

        # Comprobar firma TIKTIKPX
        if raw_pixels[:8] == b"TIKTIKPX":
            content_len = int.from_bytes(raw_pixels[8:12], "big")
            payload = raw_pixels[12:12 + content_len]
            # Descomprimir flujo MPEG-TS
            ts_data = gzip.decompress(payload)
            return ts_data

        # Si es un PNG sin cabecera TIKTIKPX pero con payload gzip directo
        if raw_pixels[:3] == b"\x1f\x8b\x08":
            return gzip.decompress(raw_pixels)

    except Exception as e:
        logger.error(f"Error al desofuscar segmento PNG: {e}")

    # Fallback: devolver los datos originales si no se pudo transformar
    return data

def decode_token(token_str: str) -> Optional[dict]:
    """Decodifica un token en base64 de tvnow247 para extraer su payload."""
    try:
        # Añadir padding si es necesario
        padded = token_str + "=" * (-len(token_str) % 4)
        decoded = base64.b64decode(padded).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return None

def encode_token(channel_id: str, ts: Optional[int] = None) -> str:
    """Genera un token válido con la marca de tiempo actual."""
    if ts is None:
        ts = int(time.time() * 1000)
    payload = {"channelId": str(channel_id), "ts": ts}
    json_str = json.dumps(payload, separators=(",", ":"))
    return base64.b64encode(json_str.encode("utf-8")).decode("utf-8").rstrip("=")
