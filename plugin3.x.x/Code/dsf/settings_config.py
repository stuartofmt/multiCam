"""
Read and write the camera entries of multiCam.config for the settings page.

The file is rewritten line by line rather than with configparser.write(), so
the explanatory comments in it are kept. Only these parts are changed:
  - the name = source entries in [USBCAMERAS], [PICAMERAS] and [STREAMS]
  - the per-camera sections of cameras that were, or now are, configured
"""

import configparser
import os
import re
import shutil
import tempfile
from pathlib import Path

from defaults import (
    AllowedOptions,
    DefaultCameraSettings,
    DefaultNetworkCameraSettings,
    NETWORK_TYPES,
    URL_NAME_RE,
    URL_NAME_SETTINGS,
)

# Camera type -> section listing cameras of that type
CAMERA_LIST_SECTIONS = {
    "USB": "USBCAMERAS",
    "PICAMERA": "PICAMERAS",
    "STREAM": "STREAMS",
}
RESERVED_SECTIONS = {"UI", "LOGGING", *CAMERA_LIST_SECTIONS.values()}

# Log levels parse_config accepts; the settings page offers INFO and DEBUG.
LOG_LEVELS = ("INFO", "DEBUG")
DEFAULT_LOG_LEVEL = "INFO"

SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
# Characters that would break the name = source line, the [name] header or the camera URL.
INVALID_NAME_CHARS = set("[]=:/\\?#%")
NUMERIC_SETTINGS = {"fps", "width", "height", "jpegresolution", "rotate"}


def allowed_keys(cameratype):
    """Setting / option names allowed in the per-camera section of this camera type."""
    if cameratype == "STREAM":
        return [setting.name for setting in DefaultNetworkCameraSettings]
    return [setting.name for setting in DefaultCameraSettings] + list(AllowedOptions.__members__)


def settings_schema():
    """Names and defaults of each camera type's settings and options, for the settings page."""
    return {
        "types": list(CAMERA_LIST_SECTIONS),
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


def _read_parser(config_file):
    config = configparser.ConfigParser()
    config.optionxform = str  # camera names are case sensitive
    config.read(config_file)
    return config


def read_camera_config(config_file):
    """
    Return the cameras configured in the file, as requested (no defaults filled in):

        [{"name": ..., "cameratype": "USB", "source": "/dev/video0",
          "values": {"fps": "30", ...}}, ...]
    """
    config = _read_parser(config_file)
    cameras = []
    for cameratype, section in CAMERA_LIST_SECTIONS.items():
        if not config.has_section(section):
            continue
        keys = allowed_keys(cameratype)
        for name, source in config[section].items():
            values = {}
            if name not in RESERVED_SECTIONS and config.has_section(name):
                values = {
                    key.lower(): value.strip()
                    for key, value in config[name].items()
                    if key.lower() in keys
                }
            cameras.append({
                "name": name,
                "cameratype": cameratype,
                "source": source.strip(),
                "values": values,
            })
    return cameras


def read_log_level(config_file):
    """Return the [LOGGING] loglevel from the file, or the default if it is not set or not supported."""
    config = _read_parser(config_file)
    if config.has_section("LOGGING"):
        for key, value in config["LOGGING"].items():
            if key.lower() == "loglevel":
                value = value.strip().upper()
                return value if value in LOG_LEVELS else DEFAULT_LOG_LEVEL
    return DEFAULT_LOG_LEVEL


def validate_cameras(cameras):
    """
    Check camera entries sent from the settings page.

    Returns (cleaned cameras, list of error messages). Empty values are
    dropped, so the default is used for them.
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
        elif INVALID_NAME_CHARS & set(name) or name[0] in ";#" or not name.isprintable():
            errors.append(f"{label}: name cannot start with ; or # or contain any of {''.join(sorted(INVALID_NAME_CHARS))}")
        elif name.upper() in RESERVED_SECTIONS:
            errors.append(f"{label}: name cannot be {name}")
        elif name in names:
            errors.append(f"{label}: name is used by more than one camera")
        names.add(name)

        if cameratype not in CAMERA_LIST_SECTIONS:
            errors.append(f"{label}: camera type must be one of {', '.join(CAMERA_LIST_SECTIONS)}")
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
            values[key] = value

        stream = values.get("streamname", DefaultCameraSettings.streamname.value)
        snapshot = values.get("snapshotname", DefaultCameraSettings.snapshotname.value)
        if stream == snapshot:
            errors.append(f"{label}: streamname and snapshotname must be different")

        cleaned.append({"name": name, "cameratype": cameratype, "source": source, "values": values})

    return cleaned, errors


def _escape(value):
    # configparser interpolation reads %% back as %.
    return value.replace("%", "%%")


def _split_sections(lines):
    """Return (lines before the first section, [(section name, lines incl. header)])."""
    preamble = []
    sections = []
    for line in lines:
        match = SECTION_RE.match(line)
        if match:
            sections.append((match.group(1).strip(), [line]))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
    return preamble, sections


def _is_comment_or_blank(line):
    stripped = line.strip()
    return not stripped or stripped[0] in "#;"


def _rebuild_section_entries(section_lines, entries):
    """
    Replace the key = value entries of a section, keeping its comments.
    New entries go where the first old entry was or, if there were none,
    straight after the comment lines under the header.
    """
    header, body = section_lines[0], section_lines[1:]

    insert_at = None
    kept = []
    for line in body:
        if _is_comment_or_blank(line):
            kept.append(line)
        elif insert_at is None:
            insert_at = len(kept)

    if insert_at is None:
        insert_at = 0
        while insert_at < len(kept) and not kept[insert_at].strip():
            insert_at += 1
        while insert_at < len(kept) and kept[insert_at].lstrip().startswith("#"):
            insert_at += 1

    new_lines = [f"{name} = {_escape(source)}" for name, source in entries]
    return [header] + kept[:insert_at] + new_lines + kept[insert_at:]


def write_camera_config(config_file, cameras, log_level=None):
    """
    Save validated cameras (see validate_cameras) and, if given, the log
    level to the config file. A copy of the previous file is kept as
    <config file>.bak.
    """
    config_path = Path(config_file)
    old_names = {camera["name"] for camera in read_camera_config(config_path)}
    new_names = {camera["name"] for camera in cameras}
    # Per-camera sections are all rewritten at the end of the file.
    replaced_sections = (old_names | new_names) - RESERVED_SECTIONS

    preamble, sections = _split_sections(config_path.read_text().splitlines())

    # Sections whose entries are replaced: section name -> [(key, value)]
    managed = {
        list_section: [(c["name"], c["source"]) for c in cameras if c["cameratype"] == cameratype]
        for cameratype, list_section in CAMERA_LIST_SECTIONS.items()
    }
    if log_level is not None:
        managed["LOGGING"] = [("loglevel", log_level)]

    output = list(preamble)
    written = set()
    for section_name, section_lines in sections:
        if section_name in replaced_sections:
            continue
        if section_name in managed and section_name not in written:
            section_lines = _rebuild_section_entries(section_lines, managed[section_name])
            written.add(section_name)
        output.extend(section_lines)

    # parse_config needs [LOGGING] and every list section, even if empty.
    for section_name, entries in managed.items():
        if section_name not in written:
            output.extend(["", f"[{section_name}]"])
            output.extend(f"{key} = {_escape(value)}" for key, value in entries)

    while output and not output[-1].strip():
        output.pop()

    for camera in cameras:
        if not camera["values"]:
            continue
        output.extend(["", f"[{camera['name']}]"])
        output.extend(f"{key} = {_escape(value)}" for key, value in camera["values"].items())

    # Write to a temporary file first so a failure can't leave a half-written config.
    fd, temp_name = tempfile.mkstemp(dir=config_path.parent, prefix=".multiCam.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as temp_file:
            temp_file.write("\n".join(output) + "\n")
        shutil.copymode(config_path, temp_name)
        shutil.copy2(config_path, config_path.with_name(config_path.name + ".bak"))
        os.replace(temp_name, config_path)
    except Exception:
        if os.path.exists(temp_name):
            os.remove(temp_name)
        raise
