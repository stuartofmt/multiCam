

from pathlib import Path

# ============================================================
# Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# ============================================================
# Camera Defaults
# ============================================================

DEFAULT_CAMERA_FPS = 5
DEFAULT_CAMERA_WIDTH = 320 * 4
DEFAULT_CAMERA_HEIGHT = 240 * 4
DEFAULT_CAMERA_API_PREFERENCE = None
DEFAULT_CAMERA_COPY_FRAME = False

# ============================================================
# Image Adjustment Defaults
# ============================================================

DEFAULT_CONTRAST = 1.0
DEFAULT_BRIGHTNESS = 1.0
DEFAULT_FOCUS = 1.0
DEFAULT_BALANCE = 1.0

# JPEG encoding quality for cached frames
DEFAULT_JPEG_QUALITY = 95


