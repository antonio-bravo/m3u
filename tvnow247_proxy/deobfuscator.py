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
    Soporta:
    1. MPEG-TS puro (comienza con el byte de sincronización 0x47).
    2. Formato actual TVNow247: Contenedor RIFF / WEBP con payload MPEG-TS en chunk EXIF.
    3. Formato anterior: Imagen PNG con cabecera TIKTIKPX o compresión GZIP directa.
    """
    if not data:
        return data

    # 1. Ya es flujo MPEG-TS directo
    if data.startswith(b"\x47"):
        return data

    # 2. Formato WebP (RIFF....WEBP) con payload MPEG-TS en chunk EXIF
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        try:
            exif_idx = data.find(b"EXIF")
            if exif_idx != -1 and len(data) >= exif_idx + 8:
                chunk_size = int.from_bytes(data[exif_idx + 4:exif_idx + 8], "little")
                payload = data[exif_idx + 8:exif_idx + 8 + chunk_size]

                # Si el payload viniese comprimido con gzip
                if payload.startswith(b"\x1f\x8b\x08"):
                    try:
                        return gzip.decompress(payload)
                    except Exception:
                        pass

                # Si empieza directamente con el sync byte 0x47 de MPEG-TS
                if payload.startswith(b"\x47"):
                    return payload

                # Buscar posible offset del sync byte 0x47 dentro de los primeros bytes
                sync_pos = payload.find(b"\x47")
                if 0 <= sync_pos < 64:
                    return payload[sync_pos:]

                return payload
        except Exception as e:
            logger.error(f"Error al desofuscar segmento WEBP/EXIF: {e}")

    # 3. Formato anterior: imagen PNG con cabecera TIKTIKPX o gzip
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        try:
            im = Image.open(io.BytesIO(data))
            raw_pixels = im.tobytes()

            # Comprobar firma TIKTIKPX
            if raw_pixels[:8] == b"TIKTIKPX":
                content_len = int.from_bytes(raw_pixels[8:12], "big")
                payload = raw_pixels[12:12 + content_len]
                return gzip.decompress(payload)

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
