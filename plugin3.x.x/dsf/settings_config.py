"""
Read and write settings.config - the port, log level and cameras - for the
startup code and the settings page.

The file is only meant to be changed through the settings page, so it is
stored compressed and base64-encoded, with a short checksum:

    <crc32 of the JSON, 8 hex digits>:<base64 of the zlib-compressed JSON>

A file that fails the checksum (or can't be decoded) is replaced by a new
default file; the bad file is kept as <file>.bak.

The JSON holds:

    {
        "port": 0,
        "loglevel": "INFO",
        "cameras": [
            {"name": "Camera1", "cameratype": "USB", "source": "/dev/video0",
             "values": {"fps": 15, "brightness": 10}}
        ]
    }

"values" holds only the settings / options that were set; anything missing
uses its default.
"""

import base64
import json
import os
import shutil
import tempfile
import zlib
from pathlib import Path

from defaults import (
    AllowedOptions,
    DefaultCameraSettings,
    DefaultNetworkCameraSettings,
    NETWORK_TYPES,
    URL_NAME_RE,
    URL_NAME_SETTINGS,
)

CAMERA_TYPES = ("USB", "PICAMERA", "STREAM")

# Log levels parse_config accepts; the settings page offers INFO and DEBUG.
LOG_LEVELS = ("INFO", "DEBUG")
DEFAULT_LOG_LEVEL = "INFO"

# Characters that would break the camera URL.
INVALID_NAME_CHARS = set("[]=:/\\?#%")
NUMERIC_SETTINGS = {"fps", "width", "height", "jpegresolution", "rotate"}


def default_settings():
    return {"port": 0, "loglevel": DEFAULT_LOG_LEVEL, "cameras": []}


def allowed_keys(cameratype):
    """Setting / option names allowed in the values of this camera type."""
    if cameratype == "STREAM":
        return [setting.name for setting in DefaultNetworkCameraSettings]
    return [setting.name for setting in DefaultCameraSettings] + list(AllowedOptions.__members__)


def settings_schema():
    """Names and defaults of each camera type's settings and options, for the settings page."""
    return {
        "types": list(CAMERA_TYPES),
        "settings": {
            "USB": {setting.name: setting.value for setting in DefaultCameraSettings},
            "PICAMERA": {setting.name: setting.value for setting in DefaultCameraSettings},
            "STREAM": {setting.name: setting.value for setting in DefaultNetworkCameraSettings},
        },
        "options": {
            "USB": list(AllowedOptions.__members__),
            "PICAMERA": list(AllowedOptions.__members__),
            "STREAM": [],
        },
    }


class SettingsFileError(ValueError):
    """The settings file failed the checksum or could not be decoded."""


def _encode(settings):
    data = json.dumps(settings, separators=(",", ":")).encode("utf-8")
    payload = base64.b64encode(zlib.compress(data, 9)).decode("ascii")
    return f"{zlib.crc32(data):08x}:{payload}\n"


def _decode(text):
    try:
        checksum, payload = text.strip().split(":", 1)
        data = zlib.decompress(base64.b64decode(payload, validate=True))
    except Exception as e:
        raise SettingsFileError(f"could not be decoded ({e})") from e
    if f"{zlib.crc32(data):08x}" != checksum.lower():
        raise SettingsFileError("failed the checksum")
    try:
        settings = json.loads(data)
    except ValueError as e:
        raise SettingsFileError(f"is not valid JSON ({e})") from e
    if not isinstance(settings, dict) or not isinstance(settings.get("cameras", []), list):
        raise SettingsFileError("does not contain valid settings")
    return settings


def _log_warning(message):
    # The logger only exists once logging has been set up.
    import logger_module
    logger = getattr(logger_module, "logger", None)
    if logger:
        logger.warning(message)
    else:
        print(message)


def load_settings(settings_file):
    """
    Return the whole settings file as a dict, with defaults for anything
    missing. If the file fails the checksum or can't be decoded, it is
    replaced by a new default file and the defaults are returned.
    """
    with open(settings_file, encoding="utf-8") as f:
        text = f.read()
    try:
        data = _decode(text)
    except SettingsFileError as e:
        _log_warning(f"Settings file {settings_file} {e} - replacing it with a default file (the old file is kept as {Path(settings_file).name}.bak)")
        data = default_settings()
        _write_settings(settings_file, data)
    settings = default_settings()
    settings.update(data)
    return settings


def read_camera_config(settings_file):
    """
    Return the cameras configured in the file, as requested (no defaults filled in):

        [{"name": ..., "cameratype": "USB", "source": "/dev/video0",
          "values": {"fps": "30", ...}}, ...]

    Values are returned as strings, as the settings page edits them.
    """
    cameras = []
    for camera in load_settings(settings_file)["cameras"]:
        cameratype = str(camera.get("cameratype", "")).upper()
        if cameratype not in CAMERA_TYPES:
            continue
        keys = allowed_keys(cameratype)
        cameras.append({
            "name": str(camera.get("name", "")),
            "cameratype": cameratype,
            "source": str(camera.get("source", "")).strip(),
            "values": {
                key.lower(): str(value).strip()
                for key, value in (camera.get("values") or {}).items()
                if key.lower() in keys and value is not None
            },
        })
    return cameras


def read_log_level(settings_file):
    """Return the loglevel from the file, or the default if it is not set or not supported."""
    value = str(load_settings(settings_file)["loglevel"]).strip().upper()
    return value if value in LOG_LEVELS else DEFAULT_LOG_LEVEL


def read_port(settings_file):
    """Return the port from the file, or 0 (pick a free port) if it is not set or not a number."""
    try:
        return int(load_settings(settings_file)["port"] or 0)
    except (TypeError, ValueError):
        return 0


def validate_port(port):
    """Return an error message if the port can't be used, else None."""
    if port != 0 and not 1024 <= port <= 65535:
        return "Port must be 0 (pick a free port) or between 1024 and 65535"
    return None


def validate_cameras(cameras):
    """
    Check camera entries sent from the settings page.

    Returns (cleaned cameras, list of error messages). Empty values are
    dropped, so the default is used for them. Numeric values are returned
    as numbers.
    """
    errors = []
    cleaned = []
    names = set()
    sources = set()

    for index, camera in enumerate(cameras, start=1):
        name = str(camera.get("name", "")).strip()
        cameratype = str(camera.get("cameratype", "")).upper()
        source = str(camera.get("source", "")).strip()
        label = f"'{name}'" if name else f"Camera {index}"

        if not name:
            errors.append(f"{label}: name is required")
        elif INVALID_NAME_CHARS & set(name) or not name.isprintable():
            errors.append(f"{label}: name cannot contain any of {''.join(sorted(INVALID_NAME_CHARS))}")
        elif name in names:
            errors.append(f"{label}: name is used by more than one camera")
        names.add(name)

        if cameratype not in CAMERA_TYPES:
            errors.append(f"{label}: camera type must be one of {', '.join(CAMERA_TYPES)}")
            continue

        if not source or not source.isprintable():
            errors.append(f"{label}: source is required")
        elif cameratype == "USB" and not source.startswith("/dev/video"):
            errors.append(f"{label}: USB source must be of the form /dev/videoN")
        elif cameratype == "PICAMERA" and not source.isdigit():
            errors.append(f"{label}: PICAMERA source must be a camera index (0, 1, ...)")
        elif cameratype == "STREAM" and not any(source.startswith(t) for t in NETWORK_TYPES):
            errors.append(f"{label}: STREAM source must start with one of {', '.join(NETWORK_TYPES)}")
        elif (cameratype, source) in sources:
            errors.append(f"{label}: source {source} is used by more than one camera")
        sources.add((cameratype, source))

        keys = allowed_keys(cameratype)
        values = {}
        for key, value in (camera.get("values") or {}).items():
            key = str(key).lower()
            value = "" if value is None else str(value).strip()
            if value == "" or key not in keys:
                continue
            if key in URL_NAME_SETTINGS:
                if not URL_NAME_RE.fullmatch(value):
                    errors.append(f"{label}: {key} must only contain letters, digits, '-', '_' or '~'")
            else:
                try:
                    number = float(value)
                except ValueError:
                    errors.append(f"{label}: {key} must be a number")
                    continue
                if key in NUMERIC_SETTINGS - {"rotate"} and number <= 0:
                    errors.append(f"{label}: {key} must be greater than 0")
                value = int(number) if number.is_integer() else number
            values[key] = value

        stream = values.get("streamname", DefaultCameraSettings.streamname.value)
        snapshot = values.get("snapshotname", DefaultCameraSettings.snapshotname.value)
        if stream == snapshot:
            errors.append(f"{label}: streamname and snapshotname must be different")

        cleaned.append({"name": name, "cameratype": cameratype, "source": source, "values": values})

    return cleaned, errors


def _write_settings(settings_file, settings):
    """Write settings (encoded) to the file, keeping a copy of the previous file as <file>.bak."""
    settings_path = Path(settings_file)
    # Write to a temporary file first so a failure can't leave a half-written file.
    fd, temp_name = tempfile.mkstemp(dir=settings_path.parent, prefix=".multiCam.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            temp_file.write(_encode(settings))
        if settings_path.exists():
            shutil.copymode(settings_path, temp_name)
            shutil.copy2(settings_path, settings_path.with_name(settings_path.name + ".bak"))
        os.replace(temp_name, settings_path)
    except Exception:
        if os.path.exists(temp_name):
            os.remove(temp_name)
        raise


def write_camera_config(settings_file, cameras, log_level=None, port=None):
    """
    Save validated cameras (see validate_cameras) and, if given, the log
    level and port to the settings file.
    """
    settings = load_settings(settings_file)
    settings["cameras"] = [
        {
            "name": camera["name"],
            "cameratype": camera["cameratype"],
            "source": camera["source"],
            "values": camera["values"],
        }
        for camera in cameras
    ]
    if log_level is not None:
        settings["loglevel"] = log_level
    if port is not None:
        settings["port"] = port
    _write_settings(settings_file, settings)


def ensure_settings_file(settings_file):
    """
    Make sure the settings file and its folder exist, and that the file
    passes the checksum. A missing file is created with defaults; a file
    that fails the checksum is replaced by a new default file.

    Returns a message saying what was done, or None.
    """
    settings_path = Path(settings_file)
    if settings_path.exists():
        try:
            _decode(settings_path.read_text(encoding="utf-8", errors="replace"))
            return None
        except SettingsFileError as e:
            _write_settings(settings_path, default_settings())
            return f"Settings file {settings_path} {e} - replaced it with a default file (the old file is kept as {settings_path.name}.bak)"

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    _write_settings(settings_path, default_settings())
    return f"Created default settings file {settings_path}"
