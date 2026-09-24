

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

class AllowedOptions(Enum):
    brightness = 'float'
    contrast  = 'float'
    balance = 'float'
    saturation = 'float'
    sharpness = 'float'
    autoexposure = 'int'
    autofocus = 'int'
    


class DefaultCameraSettings(Enum):
    fps = 15
    width = 1024
    height = 768
    jpegresolution = 95
    rotate = 0

class DefaultNetworkCameraSettings(Enum):
    jpegresolution = 95
    rotate = 0


# ALLOWED_SETTINGS = ['fps','width','height','jpegresolution','rotate']
NETWORK_TYPES = ['http://','https://','rtsp://']


