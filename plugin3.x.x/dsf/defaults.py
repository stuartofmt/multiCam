

from enum import Enum
from pathlib import Path
import re

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
    streamname = 'stream'
    snapshotname= 'snapshot'

class DefaultNetworkCameraSettings(Enum):
    fps = 15
    jpegresolution = 95
    rotate = 0
    streamname = 'stream'
    snapshotname= 'snapshot'


NETWORK_TYPES = ['http://','https://','rtsp://']

# Limits of settings that don't depend on the camera, shown on the settings page.
SETTING_LIMITS = {
    'jpegresolution': {'min': 1, 'max': 100},
    'rotate': {'min': 0, 'max': 270},
}

# Settings that become URL path segments: /<camera name>/<streamname>
URL_NAME_SETTINGS = ('streamname', 'snapshotname')
# URL-safe path segment: RFC 3986 unreserved characters, excluding '.' so "." and ".." can't be used.
URL_NAME_RE = re.compile(r'[A-Za-z0-9_~-]+')


