

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

ALLOWED_OPTIONS = ["brightness","contrast","balance","saturation","autofocus","sharpness","autoexposure"]

class DefaultCameraSettings(Enum):
    fps = 15
    width = 1024
    height = 768
    jpegresolution = 95
    rotate = 0


ALLOWED_SETTINGS = ['fps','width','height','jpegresolution','rotate']
# ============================================================
# Camera Defaults
# ============================================================

DEFAULT_CAMERA_WIDTH = 320 * 4
DEFAULT_CAMERA_HEIGHT = 240 * 4
DEFAULT_CAMERA_API_PREFERENCE = None
DEFAULT_CAMERA_COPY_FRAME = False

# JPEG encoding quality for cached frames
DEFAULT_JPEG_QUALITY = 95


