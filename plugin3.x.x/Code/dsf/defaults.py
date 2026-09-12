

from enum import Enum
from pathlib import Path

# ============================================================
# Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


# ============================================================
# Allowed Camera Defaults
# ============================================================
class DefaultCameraOptions(Enum):
    fps = 15
    width = 1024
    height = 768
    brightness = 1.0
    contrast = 1.0
    focus = 0.0
    balance = 1.0


# ============================================================
# Camera Defaults
# ============================================================

DEFAULT_CAMERA_WIDTH = 320 * 4
DEFAULT_CAMERA_HEIGHT = 240 * 4
DEFAULT_CAMERA_API_PREFERENCE = None
DEFAULT_CAMERA_COPY_FRAME = False


# JPEG encoding quality for cached frames
DEFAULT_JPEG_QUALITY = 95


