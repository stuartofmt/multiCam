import configparser
import errno
import fcntl
import os
import struct
import re
import subprocess
import glob
import copy
from typing import Dict, List, Optional, Tuple

from defaults import DefaultCameraSettings, DefaultNetworkCameraSettings, AllowedOptions, NETWORK_TYPES
from logger_module import logger

# --- CSI cameras via picamera2 ---
from picamera2 import Picamera2

global PORT, LOGLEVEL, CAMERAS
# From https://gist.github.com/laywill/63d75b53e8a7a801d77f0dd2b97de54d
class DictToClass:
	def __init__(self, dictionary):
		for key, value in dictionary.items():
			if isinstance(value, dict):
				value = DictToClass(value)
			setattr(self, key, value)

# ---------------------------------
# USB FUNCTIONS
# ---------------------------------

# Maps each allowable canonical key to the real v4l2 control name (v4l2-ctl --list-ctrls).
CONTROL_NAME_MAP_USB = {
	"brightness": "brightness",
	"contrast": "contrast",
	"balance": "white_balance_temperature_auto",
	"saturation": "saturation",
	"autofocus": "focus_auto",
	"sharpness": "sharpness",
	"autoexposure": "exposure_auto",
}

# Some controls go by different names depending on driver/kernel version.
# First alias found in --list-ctrls output wins.
CONTROL_NAME_ALIASES_USB = {
	"white_balance_temperature_auto": ["white_balance_temperature_auto", "white_balance_automatic"],
	"exposure_auto": ["exposure_auto", "auto_exposure"],
	"focus_auto": ["focus_auto", "auto_focus"],
}


# AllowedOptions values name the Python type each option's value should have.
_OPTION_TYPES = {"float": float, "int": int}


def _cast_option(name, value):
	"""Convert an option value to the type given for it in AllowedOptions."""
	option_type = _OPTION_TYPES[AllowedOptions[name].value]
	if option_type is int:
		# Config values may be written as "1.0"; int("1.0") would fail.
		return int(round(float(value)))
	return option_type(value)


# Control names and ranges don't change while running, so query each device once.
_USB_CTRLS_CACHE = {}
_USB_OPTIONS_CACHE = {}


def _parse_list_ctrls(device_path):
	"""
	Run v4l2-ctl --list-ctrls and parse out {real_name: (min, max, default)}
	for every control the driver reports.
	"""
	if device_path not in _USB_CTRLS_CACHE:
		_USB_CTRLS_CACHE[device_path] = _query_list_ctrls(device_path)
	return _USB_CTRLS_CACHE[device_path]


def _set_usb_ctrls(device_path, values):
	"""
	Set several controls with one v4l2-ctl call. v4l2-ctl still applies the
	others when one fails and names each failure on stderr, so return those names.
	"""
	if not values:
		return set()

	ctrl_arg = ",".join(f"{name}={value}" for name, value in values.items())
	try:
		result = subprocess.run(
			["v4l2-ctl", "-d", device_path, "--set-ctrl", ctrl_arg],
			capture_output=True,
			text=True,
		)
	except FileNotFoundError:
		logger.debug(
			"Error: 'v4l2-ctl' utility not found. Install it using 'sudo apt install v4l-utils'."
		)
		return set(values)

	if result.returncode == 0:
		return set()

	failed = {
		name for name in re.findall(r"^(\w+):", result.stderr, re.MULTILINE)
		if name in values
	}
	# A failure that names no control (e.g. device error) means nothing can be trusted.
	return failed or set(values)


def _query_list_ctrls(device_path):
	try:
		result = subprocess.run(
			["v4l2-ctl", "-d", device_path, "--list-ctrls"],
			check=True,
			capture_output=True,
			text=True,
		)
	except subprocess.CalledProcessError as e:
		logger.debug(f"Error listing controls for {device_path}: {e.stderr}")
		return {}
	except FileNotFoundError:
		logger.debug(
			"Error: 'v4l2-ctl' utility not found. Install it using 'sudo apt install v4l-utils'."
		)
		return {}

	all_controls = {}
	for line in result.stdout.splitlines():
		line_clean = line.strip()
		# Example: "brightness 0x00980900 (int) : min=-64 max=64 step=1 default=0 value=0"
		name_match = re.match(r"^(\w+)\s+0x[0-9a-fA-F]+", line_clean)
		if not name_match:
			continue
		real_name = name_match.group(1)

		min_match = re.search(r"min=(-?\d+)", line_clean)
		max_match = re.search(r"max=(-?\d+)", line_clean)
		default_match = re.search(r"default=(-?\d+)", line_clean)

		min_val = int(min_match.group(1)) if min_match else None
		max_val = int(max_match.group(1)) if max_match else None
		default_val = int(default_match.group(1)) if default_match else None

		all_controls[real_name] = (min_val, max_val, default_val)

	return all_controls


def _resolve_real_name(canonical_name, real_control_names):
	"""
	Given a canonical key, find which real control name (accounting for
	driver-specific aliases) is actually present on this camera.
	"""
	base_real_name = CONTROL_NAME_MAP_USB.get(canonical_name)
	if base_real_name is None:
		return None

	candidates = CONTROL_NAME_ALIASES_USB.get(base_real_name, [base_real_name])
	for candidate in candidates:
		if candidate in real_control_names:
			return candidate
	return None


def _clamp(source, canonical_name, value, bounds):
	"""Limit a requested control value to the camera's reported [min, max]."""
	min_val = bounds.get("min")
	max_val = bounds.get("max")

	if min_val is not None and float(value) < float(min_val):
		logger.debug(f"[{source}] {canonical_name}: value {value} below min {min_val}, clamping")
		return min_val
	if max_val is not None and float(value) > float(max_val):
		logger.debug(f"[{source}] {canonical_name}: value {value} above max {max_val}, clamping")
		return max_val
	return value


# VIDIOC_REQBUFS = _IOWR('V', 8, struct v4l2_requestbuffers), a 20-byte struct:
# count, type, memory, capabilities (u32 each), flags (u8), reserved[3] (u8).
_VIDIOC_REQBUFS = 0xC0145608
_V4L2_BUF_TYPE_VIDEO_CAPTURE = 1
_V4L2_MEMORY_MMAP = 1


class CameraInUseError(RuntimeError):
	"""Raised when a camera can't be set up because another process is streaming from it."""


def _usb_camera_in_use(device_path):
	"""
	Return True if another process owns the capture stream of a V4L2 device.

	V4L2 lets any number of processes open a device and change its controls;
	only the stream is exclusive, and it belongs to whichever process first
	requested buffers. Requesting a buffer here fails with EBUSY while another
	process holds the stream. The buffer is released again straight away.
	"""
	fd = os.open(device_path, os.O_RDWR | os.O_NONBLOCK)
	try:
		request = struct.pack("5I", 1, _V4L2_BUF_TYPE_VIDEO_CAPTURE, _V4L2_MEMORY_MMAP, 0, 0)
		try:
			fcntl.ioctl(fd, _VIDIOC_REQBUFS, request)
		except OSError as e:
			if e.errno == errno.EBUSY:
				return True
			raise
		release = struct.pack("5I", 0, _V4L2_BUF_TYPE_VIDEO_CAPTURE, _V4L2_MEMORY_MMAP, 0, 0)
		fcntl.ioctl(fd, _VIDIOC_REQBUFS, release)
		return False
	finally:
		os.close(fd)


def get_camera_options_usb(camera_name, source):
	"""
	Reset ALL controls a USB/UVC camera reports back to their default
	values, but return min/max/default only for the subset in
	ALLOWABLE_OPTIONS.

	Args:
		camera_name: Human-readable label for this camera, used in logging.
		source: Device path (e.g. "/dev/video0"). Also used as the key in
				the returned dict.

	Returns:
		A dict keyed on `source`:
			{
				source: {
					canonical_name: {"min": ..., "max": ..., "default": ...},
					...
				}
			}
		Only controls in AllowedOptions that are confirmed present and
		successfully reset on this camera are included, even though every
		control on the camera was reset.
	"""
	# UVC control values persist in the device, so resetting once per run is enough.
	if source in _USB_OPTIONS_CACHE:
		return _USB_OPTIONS_CACHE[source]

	# Controls belong to the device, so resetting them would change the picture
	# of whatever process is already streaming from it; don't touch a busy camera.
	if _usb_camera_in_use(source):
		raise CameraInUseError(f'{source} is in use by another process')

	try:
		all_controls = _parse_list_ctrls(source)

		writable_controls = {}
		reverse_lookup = {}  # real_name actually used -> canonical_name

		for canonical_name in AllowedOptions.__members__:
			real_name = _resolve_real_name(canonical_name, all_controls.keys())
			if real_name is None:
				logger.debug(f"[{camera_name}] {canonical_name}: not present on this camera")
				continue
			reverse_lookup[real_name] = canonical_name

		defaults = {
			real_name: default_val
			for real_name, (_, _, default_val) in all_controls.items()
			if default_val is not None
		}
		failed = _set_usb_ctrls(source, defaults)
		logger.debug(f"[{camera_name}] reset to defaults: {defaults}; not settable: {failed or 'none'}")

		for real_name, (min_val, max_val, default_val) in all_controls.items():
			if real_name not in defaults or real_name in failed:
				continue

			canonical_name = reverse_lookup.get(real_name)
			if canonical_name is None:
				continue  # not one of the allowable options, don't include in output

			resolved_min = min_val if min_val is not None else default_val
			resolved_max = max_val if max_val is not None else default_val

			writable_controls[canonical_name] = {
				"min": resolved_min,
				"max": resolved_max,
				"default": default_val,
			}

		_USB_OPTIONS_CACHE[source] = {source: writable_controls}
		return _USB_OPTIONS_CACHE[source]
	except Exception as e:
		raise Exception(f'Issue setting camera defaults - {e}') from e


def set_controls_usb(controls_by_source, camera_config_options=None):
	"""
	Apply controls to one or more USB/UVC cameras, given a dict in the
	same shape as get_camera_options_usb's return value:

		{
			source: {
				canonical_name: {"min": ..., "max": ..., "default": ...},
				...
			},
			...
		}

	For each entry, the "default" value is written to the camera via
	v4l2-ctl --set-ctrl, clamped to [min, max] first as a safety check.

	Args:
		controls_by_source: dict keyed on source (device path), with
			values in the same shape produced by get_camera_options_usb.
		camera_config_options: optional dict of config file options. Only controls
			present in both this dict and AllowedOptions will be set.

	Returns:
		A dict keyed on source, each value a dict of
		{canonical_name: value} for the controls that were set, with
		value as actually applied (after clamping).
	"""
	results_by_source = {}

	for source, controls in controls_by_source.items():
		all_real_controls = _parse_list_ctrls(source).keys()
		results = {}
		to_set = {}  # real_name -> (canonical_name, value)

		for canonical_name, bounds in controls.items():
			if camera_config_options and canonical_name not in camera_config_options:
				continue
			if canonical_name not in AllowedOptions.__members__:
				continue
			real_name = _resolve_real_name(canonical_name, all_real_controls)
			if real_name is None:
				logger.debug(f"[{source}] {canonical_name}: not present on this camera")
				continue

			if camera_config_options:
				value = camera_config_options.get(canonical_name)
			else:
				value = bounds.get("default")

			if value is None:
				logger.debug(f"[{source}] {canonical_name}: no value to set, skipping")
				continue

			# v4l2 controls are integers; config values arrive as floats.
			value = int(round(_clamp(source, canonical_name, value, bounds)))
			to_set[real_name] = (canonical_name, value)

		failed = _set_usb_ctrls(source, {name: value for name, (_, value) in to_set.items()})
		for real_name, (canonical_name, value) in to_set.items():
			if real_name in failed:
				logger.debug(f"[{source}] {canonical_name} ({real_name}) not settable")
			else:
				results[canonical_name] = value
				logger.debug(f"[{source}] {canonical_name} ({real_name}): set to {value}")

		results_by_source[source] = results

	return results_by_source
# -------------------------------------------------
# validate camera configs
# -------------------------------------------------

"""
Validates a camera-config dict against what each /dev/videoN device
actually reports via `v4l2-ctl -d <source> --list-formats-ext`, and
adjusts format / resolution / fps to the closest supported values.

Fallback rules (as specified):
  1. If the requested format (MJPEG/MPEG -> MJPG) isn't supported,
	 fall back to the first format the device reports.
  2. If the requested width/height IS supported under the chosen
	 format, but the requested fps is not, drop to the next lower
	 fps available at that resolution, or if there is none, rise to
	 the next higher one.
  3. If the requested width/height is NOT supported, drop to the
	 next lower resolution (by pixel area) available under the
	 chosen format, or if there is none, rise to the next size up. If the original fps isn't available at that new
	 resolution, drop to the next lower fps available there (or the
	 next higher one if there is none lower).
"""

# Aliases: config may say "MPEG"/"MJPEG", v4l2-ctl reports the fourcc "MJPG".
FORMAT_ALIASES = {
	"MPEG": "MJPG",
	"MJPEG": "MJPG",
}

# Fallback format sequence if requested format is unavailable
FORMAT_FALLBACK_SEQUENCE = ["MJPG", "YUYV", "YUY2"]

FORMAT_LINE_RE = re.compile(r"^\s*\[\d+\]:\s*'(\w+)'")
SIZE_LINE_RE = re.compile(r"Size:\s*Discrete\s*(\d+)x(\d+)")
FPS_LINE_RE = re.compile(r"\(([\d.]+)\s*fps\)")


def _run_v4l2_ctl(source: str) -> Optional[str]:
	"""Run v4l2-ctl --list-formats-ext for a source. Returns stdout, or None on failure."""

	try:
		result = subprocess.run(
			["v4l2-ctl", "-d", source, "--list-formats-ext"],
			capture_output=True,
			text=True,
			timeout=5,
		)
	except (OSError, subprocess.TimeoutExpired) as exc:
		logger.warning(f"Could not query {source} with v4l2-ctl: {exc}")
		return None

	if result.returncode != 0 or not result.stdout.strip():
		logger.warning(
			f"v4l2-ctl reported no formats for {source}: {result.stderr.strip()}"
		)
		return None

	return result.stdout


def _parse_formats(output: str) -> "dict[str, dict[Tuple[int, int], List[float]]]":
	"""
	Parse `v4l2-ctl --list-formats-ext` output into:
		{ format_code: { (width, height): [fps, fps, ...] } }
	Order of formats/resolutions/fps as reported is preserved (dict
	insertion order), since v4l2-ctl typically lists preferred/higher
	options first.
	"""

	formats: "dict[str, dict[Tuple[int, int], List[float]]]" = {}

	current_format: Optional[str] = None
	current_size: Optional[Tuple[int, int]] = None

	for line in output.splitlines():

		fmt_match = FORMAT_LINE_RE.match(line)
		if fmt_match:
			current_format = fmt_match.group(1).upper()
			formats.setdefault(current_format, {})
			current_size = None
			continue

		size_match = SIZE_LINE_RE.search(line)
		if size_match and current_format is not None:
			current_size = (int(size_match.group(1)), int(size_match.group(2)))
			formats[current_format].setdefault(current_size, [])
			continue

		fps_match = FPS_LINE_RE.search(line)
		if fps_match and current_format is not None and current_size is not None:
			formats[current_format][current_size].append(float(fps_match.group(1)))
			continue

	return formats


def _normalize_format(requested_format: str) -> str:
	upper = requested_format.upper()
	return FORMAT_ALIASES.get(upper, upper)


def _next_lower_resolution(
	requested_wh: Tuple[int, int],
	available_whs: "List[Tuple[int, int]]",
) -> Optional[Tuple[int, int]]:
	"""Largest available resolution (by pixel area) that is still smaller
	than requested. Falls back to the next size up (the smallest available
	that is not smaller) if nothing is smaller."""

	if not available_whs:
		return None

	requested_area = requested_wh[0] * requested_wh[1]

	# Width breaks ties between resolutions with the same area, so the result is deterministic.
	def area_key(wh):
		return (wh[0] * wh[1], wh[0])

	lower = [wh for wh in available_whs if wh[0] * wh[1] < requested_area]
	if lower:
		return max(lower, key=area_key)

	return min(available_whs, key=area_key)


def _next_lower_fps(
	requested_fps: float,
	available_fps: List[float],
) -> Optional[float]:
	"""Highest available fps that is still lower than requested. Falls
	back to the next higher fps (the lowest available above requested)
	if nothing is lower."""

	if not available_fps:
		return None

	lower = [f for f in available_fps if f < requested_fps]
	if lower:
		return max(lower)

	return min(f for f in available_fps if f > requested_fps)


def _add_applied_options(cam: dict, options: Optional[dict]) -> None:
	"""Copy any AllowedOptions in `options` (as applied) into the camera config."""
	for key, value in (options or {}).items():
		if key in AllowedOptions.__members__:
			cam[key] = _cast_option(key, value)


def validate_usb_camera_configs(cameras: Dict[str, dict], applied_options: Optional[Dict[str, dict]] = None) -> Dict[str, dict]:
	"""
	Takes a dict of camera configs keyed by camera name, e.g.:

		{'Cam 2': {'name': 'Cam 2', 'source': '/dev/video2',
				   'cameratype': 'USB', 'fps': 15, 'width': 1024,
				   'height': 768, 'jpegresolution': 95, 'rotate': 0,
				   'format': 'MPEG'}}

	For each entry, queries `v4l2-ctl -d <source> --list-formats-ext`
	and adjusts 'format', 'width'/'height', and 'fps' to the closest
	values the device actually supports, per the fallback rules
	described in the module docstring.

	Any AllowedOptions in applied_options[<camera name>] (the values
	actually set, after clamping) are added to that camera's entry.

	Returns a NEW dict (the input is not mutated). Entries whose
	source can't be queried via v4l2-ctl (e.g. it isn't a local V4L2
	device, or the device didn't respond) are otherwise returned unchanged.
	"""

	adjusted = copy.deepcopy(cameras)
	for cam_name, cam in adjusted.items():
		if cam.get('cameratype') != 'USB':
			continue
		_add_applied_options(cam, (applied_options or {}).get(cam_name))
		try:
			source = cam.get("source")
			output = _run_v4l2_ctl(source)
			if output is None:
				logger.info(f"[{cam_name}] Skipping format validation for {source}")
				continue

			formats = _parse_formats(output)

			if not formats:
				logger.warning(f"[{cam_name}] No formats parsed for {source}; skipping")
				continue
		except Exception as e:
			logger.info(f'Format parsing{e}')
			raise
		try:
			# --- Step 1: format ---
			requested_format = _normalize_format(str(cam.get("format", "")))

			if requested_format in formats:
				chosen_format = requested_format
			else:
				# Try fallback sequence if requested format not available
				chosen_format = None
				for fallback_fmt in FORMAT_FALLBACK_SEQUENCE:
					if fallback_fmt in formats:
						chosen_format = fallback_fmt
						break

				# If no fallback worked, use first available format
				if chosen_format is None:
					chosen_format = next(iter(formats))

				logger.info(
					f"[{cam_name}] Format '{cam.get('format')}' not supported on "
					f"{source}; Adjusting to '{chosen_format}'"
				)

			cam["format"] = chosen_format
			resolutions = formats[chosen_format]
		except Exception as e:
			logger.info(f'Format setting{e}')
			raise
		try:
			# --- Step 2/3: resolution + fps ---
			requested_wh = (int(cam["width"]), int(cam["height"]))
			requested_fps = float(cam["fps"])

			if requested_wh in resolutions:
				chosen_wh = requested_wh
			else:
				chosen_wh = _next_lower_resolution(requested_wh, list(resolutions.keys()))
				if chosen_wh is None:
					logger.warning(
						f"[{cam_name}] No usable resolution found for format "
						f"'{chosen_format}' on {source}; leaving as requested"
					)
					continue
				logger.info(
					f"[{cam_name}] Resolution {requested_wh[0]}x{requested_wh[1]} not "
					f"supported for '{chosen_format}' on {source}; Adjusting to "
					f"{chosen_wh[0]}x{chosen_wh[1]}"
				)

			cam["width"], cam["height"] = chosen_wh
		except Exception as e:
			logger.info(f'Resolution setting{e}')
			raise

		try:
			available_fps = resolutions.get(chosen_wh, [])

			if requested_fps in available_fps:
				chosen_fps = requested_fps
			else:
				chosen_fps = _next_lower_fps(requested_fps, available_fps)
				if chosen_fps is None:
					logger.warning(
						f"[{cam_name}] No usable fps found at "
						f"{chosen_wh[0]}x{chosen_wh[1]} for '{chosen_format}' on "
						f"{source}; leaving fps as requested"
					)
					continue
				if chosen_fps > requested_fps:
					fallback = (
						f"capturing at {int(chosen_fps)} and streaming at "
						f"{int(requested_fps)}"
					)
				else:
					fallback = f"falling back to {int(chosen_fps)}"
				logger.info(
					f"[{cam_name}] fps {int(requested_fps)} not supported at "
					f"{chosen_wh[0]}x{chosen_wh[1]} for '{chosen_format}' on "
					f"{source}; {fallback}"
				)

			# The camera runs at capturefps; the capture thread drops frames to serve fps.
			# If the camera only offers a higher rate, the requested rate is still served.
			cam["capturefps"] = chosen_fps
			cam["fps"] = min(requested_fps, chosen_fps)
		except Exception as e:
			logger.info(f'FPS setting{e}')
			raise
	return adjusted




# ----------------------------------
#  PI FUNCTIONS
# ----------------------------------


# Maps each allowable canonical key to the real Picamera2/libcamera control name.
CONTROL_NAME_MAP_PICAM = {
	"brightness": "Brightness",
	"contrast": "Contrast",
	"balance": "AwbEnable",
	"saturation": "Saturation",
	"autofocus": "AfMode",
	"sharpness": "Sharpness",
	"autoexposure": "AeEnable",
}

# Reverse lookup: real Picamera2 control name -> canonical name
PI_REAL_TO_CANONICAL = {v: k for k, v in CONTROL_NAME_MAP_PICAM.items()}


# source -> {"controls": {canonical: bounds}, "modes": {(w, h): max_fps}}
_PICAM_INFO_CACHE = {}


def _picam_error_is_busy(error):
	"""Return True if a Picamera2 error, or any error it was raised from, is EBUSY."""
	while error is not None:
		if getattr(error, 'errno', None) == errno.EBUSY or 'Device or resource busy' in str(error):
			return True
		error = error.__cause__ or error.__context__
	return False


def get_camera_options_picam(camera_name, source):
	"""
	Return min/max/default for the AllowedOptions controls a Pi camera supports.

	No reset is needed: libcamera controls only last for one camera session,
	so every new session starts at the defaults. The camera is opened once per
	run; its controls and sensor modes are cached.

	Returns:
		{source: {canonical_name: {"min": ..., "max": ..., "default": ...}}}
	"""
	if source not in _PICAM_INFO_CACHE:
		try:
			picam2 = Picamera2(camera_num=source)
		except RuntimeError as e:
			# Picamera2 reports "Camera __init__ sequence did not complete."; the
			# underlying acquire() error says whether the camera is busy.
			if _picam_error_is_busy(e):
				raise CameraInUseError(f'Camera {source} is in use by another process') from e
			raise
		try:
			all_controls = picam2.camera_controls
			sensor_modes = picam2.sensor_modes
		finally:
			picam2.close()

		controls = {}
		for real_name, (min_val, max_val, default_val) in all_controls.items():
			canonical_name = PI_REAL_TO_CANONICAL.get(real_name)
			if canonical_name is None or default_val is None:
				continue
			controls[canonical_name] = {
				"min": min_val if min_val is not None else default_val,
				"max": max_val if max_val is not None else default_val,
				"default": default_val,
			}

		modes = {}
		for mode in sensor_modes:
			wh = tuple(mode["size"])
			modes[wh] = max(modes.get(wh, 0.0), float(mode["fps"]))

		_PICAM_INFO_CACHE[source] = {"controls": controls, "modes": modes}
		logger.debug(f'[{camera_name}] PI CONTROLS = {controls}')

	return {source: _PICAM_INFO_CACHE[source]["controls"]}


def set_controls_picam(controls_by_source, camera_config_options=None):
	"""
	Resolve config-file options into libcamera control values, clamped to
	each control's [min, max].

	Values are not written to the camera here: libcamera controls reset when
	the camera is closed, so they are passed to the stream and applied when
	it starts.

	Args:
		controls_by_source: {source: {canonical_name: {"min", "max", "default"}}}
			as returned by get_camera_options_picam.
		camera_config_options: config file options; only these are set, since
			every other control already starts at its default.

	Returns:
		{source: {real_name: value}}
	"""

	resolved_by_source = {}

	for source, controls in controls_by_source.items():
		resolved = {}

		for canonical_name, value in (camera_config_options or {}).items():
			if canonical_name not in AllowedOptions.__members__:
				continue
			bounds = controls.get(canonical_name)
			if bounds is None:
				logger.debug(f"[{source}] {canonical_name}: not present on this camera")
				continue

			value = _clamp(source, canonical_name, value, bounds)
			# Config values are floats; libcamera needs the control's own type (bool/int/float).
			value = type(bounds["default"])(value)
			resolved[CONTROL_NAME_MAP_PICAM[canonical_name]] = value
			logger.debug(f"[{source}] {canonical_name}: will be set to {value}")

		resolved_by_source[source] = resolved

	return resolved_by_source


def _get_picam_sensor_modes(source) -> "dict[Tuple[int, int], float]":
	"""Return {(width, height): max_fps} for each sensor mode of a Pi camera."""

	get_camera_options_picam(f"PiCamera-{source}", source)
	return _PICAM_INFO_CACHE[source]["modes"]


def validate_pi_camera_configs(cameras: Dict[str, dict], applied_options: Optional[Dict[str, dict]] = None) -> Dict[str, dict]:
	"""
	Pi camera equivalent of validate_usb_camera_configs.

	Adjusts 'width'/'height' to a supported sensor mode and caps 'fps'
	at that mode's maximum. Format is not validated because the Pi
	stream always captures RGB888.

	Any AllowedOptions in applied_options[<camera name>] (the values
	actually set, after clamping) are added to that camera's entry.

	Returns a NEW dict (the input is not mutated). Entries whose sensor
	modes can't be queried are otherwise returned unchanged.
	"""

	adjusted = copy.deepcopy(cameras)
	for cam_name, cam in adjusted.items():
		if cam.get('cameratype') != 'PICAMERA':
			continue
		_add_applied_options(cam, (applied_options or {}).get(cam_name))

		source = cam.get("source")
		try:
			modes = _get_picam_sensor_modes(source)
		except Exception as e:
			logger.warning(f"[{cam_name}] Could not query sensor modes for camera {source}: {e}")
			continue

		if not modes:
			logger.warning(f"[{cam_name}] No sensor modes reported for camera {source}; skipping")
			continue

		requested_wh = (int(cam["width"]), int(cam["height"]))
		requested_fps = float(cam["fps"])

		# Sensor modes are raw readout sizes; the ISP scales the output stream
		# to any size, so any resolution within a sensor mode is supported.
		covering = [wh for wh in modes if wh[0] >= requested_wh[0] and wh[1] >= requested_wh[1]]
		if covering:
			chosen_wh = requested_wh
		else:
			chosen_wh = _next_lower_resolution(requested_wh, list(modes.keys()))
			covering = [chosen_wh]
			logger.info(
				f"[{cam_name}] Resolution {requested_wh[0]}x{requested_wh[1]} exceeds "
				f"the sensor on camera {source}; Adjusting to "
				f"{chosen_wh[0]}x{chosen_wh[1]}"
			)

		cam["width"], cam["height"] = chosen_wh

		# Pi sensors accept any frame rate up to the maximum of the sensor mode
		# libcamera picks: the fastest mode large enough for the output.
		max_fps = max(modes[wh] for wh in covering)
		if requested_fps > max_fps:
			logger.info(
				f"[{cam_name}] fps {int(requested_fps)} not supported at "
				f"{chosen_wh[0]}x{chosen_wh[1]} on camera {source}; "
				f"falling back to {int(max_fps)}"
			)
			cam["fps"] = max_fps
		else:
			cam["fps"] = requested_fps

	return adjusted



URL_NAME_SETTINGS = ('streamname', 'snapshotname')
# URL-safe path segment: RFC 3986 unreserved characters, excluding '.' so "." and ".." can't be used.
URL_NAME_RE = re.compile(r'[A-Za-z0-9_~-]+')


def get_config_from_file(config, name, source, cameratype):
	"""
	Build the default settings dictionaries for a single camera.
	"""

	file_options = {}
	file_settings = {}

	if config.has_section(name):
		file_options.update({
			key.lower(): _cast_option(key.lower(), value)
			for key, value in config[name].items()
			if key.lower() in AllowedOptions.__members__
		})

		valid_settings = [setting.name for setting in DefaultCameraSettings]
		file_settings.update({
			key.lower(): value.strip() if key.lower() in URL_NAME_SETTINGS else float(value)
			for key, value in config[name].items()
			if key.lower() in valid_settings
		})

	
	mpeg_default = {}		
	if cameratype == 'USB': # Set MPEG as default
		default_camera_settings = {
			setting.name: setting.value for setting in DefaultCameraSettings
		}
		mpeg_default = {'format' : 'MPEG'}
	if cameratype == 'PICAMERA':	# PICAMERA has different formats
		default_camera_settings = {
			setting.name: setting.value for setting in DefaultCameraSettings
		}
	elif cameratype == 'STREAM': # STREAMS come as they are 
		default_camera_settings = {
			setting.name: setting.value for setting in DefaultNetworkCameraSettings
		}
		valid_network_settings = {
			setting.name for setting in DefaultNetworkCameraSettings
		}
		file_settings = {
			key: value
			for key, value in file_settings.items()
			if key in valid_network_settings
		}



	camera_settings = {
		**default_camera_settings,
		**file_settings,
		**mpeg_default
	}

	# fps sets the frame interval (1/fps), so it must be positive.
	if camera_settings['fps'] <= 0:
		logger.warning(f"[{name}] fps {camera_settings['fps']} must be greater than 0; using default {default_camera_settings['fps']}")
		camera_settings['fps'] = default_camera_settings['fps']

	# streamname and snapshotname become URL path segments (/<camera name>/<streamname>).
	for setting in URL_NAME_SETTINGS:
		if not URL_NAME_RE.fullmatch(camera_settings[setting]):
			logger.warning(f"[{name}] {setting} '{camera_settings[setting]}' must only contain letters, digits, '-', '_' or '~'; using default {default_camera_settings[setting]}")
			camera_settings[setting] = default_camera_settings[setting]
	if camera_settings['streamname'] == camera_settings['snapshotname']:
		logger.warning(f"[{name}] streamname and snapshotname must be different; using defaults {default_camera_settings['streamname']} and {default_camera_settings['snapshotname']}")
		camera_settings['streamname'] = default_camera_settings['streamname']
		camera_settings['snapshotname'] = default_camera_settings['snapshotname']

	camera_data = {
		key.lower(): value
		for key, value in {
			"name": name,
			"source": source,
			"cameratype": cameratype,
			**camera_settings
		}.items()
	}

	camera_file_options = {
		key.lower(): value
		for key, value in file_options.items()
	}

	return camera_data, camera_file_options

def highlight_print(msg):
	"""
	Log a message, or a list/tuple of lines, at INFO level as a block
	framed by separator lines so it stands out in the log.
	"""
	highlight = ["","=" * 95]
	if isinstance(msg, (list, tuple)):
		highlight.extend(msg)
	else:
		highlight.append(msg)
	highlight = highlight +  ["-" * 95, f'\n']
	logger.info("\n".join(highlight))



def find_usb_cameras():
	"""
	Return a list of /dev/videoN paths for USB cameras that actually
	support video capture (filters out metadata-only nodes by
	checking reported capabilities, not by even/odd guessing).
	"""
	try:
		video_nodes = sorted(glob.glob('/dev/video*'),
							key=lambda x: int(re.search(r'\d+', x).group()))
	except Exception as e:
		raise Exception(f"Error listing /dev/video* nodes - {e}")


	usb_devices = []
	try:
		for node in video_nodes:
			result = subprocess.run(
					['v4l2-ctl', '-d', node, '--info'],
					capture_output=True, text=True, timeout=2
				)

			if result.returncode != 0:
				continue

			out = result.stdout

			is_usb = 'usb' in out.lower()
			# "Device Caps" lists the node's own capabilities; some nodes
			# only show "Video Capture" under "All Caps" (i.e. the driver
			# supports it) without exposing it on this particular node —
			# we want it listed under Device Caps to be usable.
			device_caps_section = out.split('Device Caps')[-1] if 'Device Caps' in out else ''
			supports_capture = 'Video Capture' in device_caps_section

			if is_usb and supports_capture:
				usb_devices.append(node)
	except Exception as e:
		raise Exception(f"Error checking USB camera capabilities - {e}")

	camera_results = []
	if usb_devices:
		# --- USB cameras via /dev/video* (capability-checked) ---
		camera_results.append("USB camera(s) found with these options:")
		for dev in usb_devices:
			camera_results.append(f"\n-- {dev}")
			try:
				camera_options = get_camera_options_usb(f"USB-{dev}", dev)
				if camera_options.get(dev):
					for control_name, bounds in camera_options[dev].items():
						min_val = bounds.get("min", "N/A")
						max_val = bounds.get("max", "N/A")
						default_val = bounds.get("default", "N/A")
						camera_results.append(f"   {control_name}: min={min_val}, max={max_val}, default={default_val}")
			except CameraInUseError:
				camera_results.append("   In use by another process")
			except Exception as e:
				logger.debug(f"Could not query options for {dev}: {e}")
	else:
		camera_results.append("No USB cameras were found.")

	highlight_print(camera_results)

	return usb_devices


def find_pi_cameras():
	"""
	Log the Pi (non-USB) cameras found by Picamera2 and the options each
	supports.

	Returns a list of the Picamera2 slots of those cameras. These match the
	camera sources that parse_config resolves from PICAMERAS.
	"""
	try:
		pi_camera_indices = get_pi_camera_indices()

		camera_results = []
		if pi_camera_indices:
			camera_results.append(f"PiCamera(s) found with these options:")
			for index, pi_camera_index in enumerate(pi_camera_indices):
				camera_results.append(f"\n--index {index}")
				try:
					camera_options = get_camera_options_picam(f"PiCamera-{index}", pi_camera_index)
					if camera_options.get(pi_camera_index):
						for control_name, bounds in camera_options[pi_camera_index].items():
							min_val = bounds.get("min", "N/A")
							max_val = bounds.get("max", "N/A")
							default_val = bounds.get("default", "N/A")
							camera_results.append(f"   {control_name}: min={min_val}, max={max_val}, default={default_val}")
				except CameraInUseError:
					camera_results.append("   In use by another process")
				except Exception as e:
					logger.debug(f"Could not query options for camera index {index}: {e}")
		else:
			camera_results.append("No Pi cameras found.")

		highlight_print(camera_results)

		return pi_camera_indices
	except Exception as e:
		raise Exception(f"Error listing Pi cameras - {e}") from e


def get_pi_camera_indices():
	"""
	Return the Picamera2 camera numbers of the Pi (CSI) cameras, in
	global_camera_info order.

	libcamera also lists USB cameras (uvcvideo pipeline), so only cameras
	on a Raspberry Pi pipeline (rpi/vc4, rpi/pisp) are kept.
	"""
	# global_camera_info is sorted by Id, so its order is not the camera number; 'Num' is.
	# It omits PipelineHandler, so read that from the libcamera camera itself.
	libcamera_cameras = Picamera2._cm.cms.cameras
	pi_cameras = []
	for cam_info in Picamera2.global_camera_info():
		num = cam_info['Num']
		properties = {k.name: v for k, v in libcamera_cameras[num].properties.items()}
		pipeline = properties.get('PipelineHandler')
		if pipeline is not None:
			is_pi_camera = pipeline.startswith('rpi/')
		else:
			# Older libcamera doesn't report PipelineHandler.
			is_pi_camera = '/usb' not in cam_info['Id'].lower()
		if is_pi_camera:
			pi_cameras.append(num)
	return pi_cameras


def resolve_pi_camera_index(camera_number):
	"""Resolve a user-facing Pi-camera ordinal to its Picamera2 slot."""
	if camera_number < 0:
		raise ValueError(
			f'PICAMERAS index {camera_number} does not identify a Pi camera'
		)
	pi_camera_indices = get_pi_camera_indices()
	try:
		return pi_camera_indices[camera_number]
	except IndexError as e:
		raise ValueError(
			f'PICAMERAS index {camera_number} does not identify a Pi camera'
		) from e


def parse_config(config_file,logger):
	"""
	Read and validate the config file.

	Checks the UI port and LOGGING level, and builds settings for each
	camera listed under USBCAMERAS, PICAMERAS and STREAMS. The requested
	settings are logged.

	Returns:
		(PORT, LOGLEVEL, CAMERAS, CAMERA_CONFIG), where CAMERAS maps camera
		name to its settings and CAMERA_CONFIG maps camera name to its
		AllowedOptions from the file. Returns False if the file does not exist.

	Raises:
		Exception('Config Issue') if the file is invalid.
	"""
	if not os.path.exists(config_file):
		logger.debug(f"No Config file:  {config_file}")
		return False
	else:
		logger.debug(f"Parsing {config_file}")
		try:
			config = configparser.ConfigParser()
			config.optionxform = str #preserves case of keys
			config.read(config_file)

			# Convert to dict
			# Source - https://stackoverflow.com/a/28990982
			config_dict = {s:dict(config.items(s)) for s in config.sections()}

			# Get the various sections
			ui_section = config_dict["UI"]
			logging_section = config_dict["LOGGING"]

			#Change the keys to lower for UI and UPPER for LOGGING, but not for USBCAMERAS or PICAMERAS
			config_dict_ui = {k.lower():v for k,v in ui_section.items()}
			config_dict_logging = {k.upper():v for k,v in logging_section.items()}

			#Convert UI and LOGGING to dot dict
			#UI = DictToClass(config_dict_ui)
			#LOGGING = DictToClass(config_dict_logging)

			# Adjust types and values
			# UI
			if 'port' not in config_dict_ui:
				raise ValueError('UI section must have a PORT specified')

			PORT = int(config_dict_ui['port'])
			if PORT < 1024 or PORT > 65535:
				raise ValueError('UI PORT must be between 1024 and 65535')

			# LOGGING
			LOGLEVEL = config_dict_logging.get('LOGLEVEL', 'INFO')
			if LOGLEVEL not in ['DEBUG','INFO','WARNING']:
				raise ValueError('LOGGING LEVEL must be one of DEBUG, INFO, WARNING')

			# Process all Camera settings

			CAMERAS = {}
			CAMERA_CONFIG = {}

			for name, source in config['USBCAMERAS'].items():
				if source.startswith('/dev/video'):
					CAMERAS[name], CAMERA_CONFIG[name] = get_config_from_file(config, name, source,"USB")

			for name, source in config['PICAMERAS'].items():
				try:
					camera_number = int(source)
				except ValueError:
					raise ValueError(f'PICAMERAS entry {name} must be an integer camera index')
				source = resolve_pi_camera_index(camera_number)
				CAMERAS[name], CAMERA_CONFIG[name] = get_config_from_file(config, name, source,"PICAMERA")
			# Check USBCAMERAS and PICAMERAS are not both empty

			for name, source in config['STREAMS'].items():
				if any(
                    source.startswith(network_type)
                    for network_type in NETWORK_TYPES
                ):
					CAMERAS[name], CAMERA_CONFIG[name] = get_config_from_file(config, name, source,"STREAM")

			if CAMERAS == {}:
				get_installed_cameras()
				raise ValueError('At least one camera must be specified in [USBCAMERAS] or [PICAMERAS] or [STREAM]')		

			# All tests passed - log effective configuration
			camera_results = []
			
			camera_results.append("Requested Settings from Configuration File")
			for name, options in CAMERAS.items():
				for option, value in options.items():
					if option == 'name':
						camera_results.append(f'\n{value}')
					else:
						camera_results.append(f'\t--{option} = {value}')
				if CAMERA_CONFIG and CAMERA_CONFIG[name]:
					for option, value in CAMERA_CONFIG[name].items():
						camera_results.append(f'\t--{option} = {value}')
				elif not CAMERA_CONFIG:
					camera_results.append(f'\tNo additional settings')
			highlight_print(camera_results)

			return PORT,LOGLEVEL,CAMERAS, CAMERA_CONFIG
		
		except Exception as e:
			logger.critical(f'Error parsing config file {config_file}')
			logger.critical(f'{e}')
			raise Exception('Config Issue')

def get_installed_cameras():
	"""
	Detect attached cameras and log what was found.

	Returns USB device paths (e.g. "/dev/video0") followed by Pi camera
	Picamera2 slots.
	"""
	usb_cameras = find_usb_cameras()
	pi_cameras = find_pi_cameras()

	return usb_cameras + pi_cameras

	
def configure_cameras(installed_cameras,camera_list,requested_options):
		"""
		Apply the requested options to each configured camera and validate
		its format, resolution and fps against what the device supports.

		Args:
			installed_cameras: sources returned by get_installed_cameras.
			camera_list: camera settings keyed by camera name, as returned
				by parse_config. Updated in place.
			requested_options: AllowedOptions from the config file, keyed by
				camera name.

		Returns:
			camera_list with USB and Pi cameras that are not installed, or that
			could not be set up (e.g. busy in another process), removed, and the remaining settings adjusted to their actual values. Pi
			cameras with configured options also get a 'controls' entry.
			The actual settings are logged.
		"""
			# Assemble the camera configurations
		logger.debug(f'{camera_list=}')
		logger.debug(f'{requested_options=}')
		logger.debug(f'{installed_cameras=}')
		try:
			# Option values as applied to each camera (after clamping), keyed by camera name.
			applied_options = {}
			# Cameras that could not be set up (e.g. busy in another process); removed below
			# so the remaining cameras still start.
			failed_cameras = set()
			for name, details in camera_list.items():
				if (details['source'] not in installed_cameras) and (details['cameratype'] != 'STREAM'):
					logger.warning(f'Camera source {details['source']} is not installed')
					continue

				try:
					# Get and set camera options for USB and Pi cameras - No need for streams
					if details['cameratype'] == 'USB':
						camera_options = get_camera_options_usb(name,details['source'])
						logger.debug(camera_options)
						applied = set_controls_usb(camera_options,requested_options[name])
						# With no options configured every control is set to its default; only report requested ones.
						applied_options[name] = {
							key: value
							for key, value in applied[details['source']].items()
							if key in requested_options[name]
						}

					elif details['cameratype'] == 'PICAMERA':
						camera_options = get_camera_options_picam(name,details['source'])
						logger.debug(f'{camera_options=}')
						resolved = set_controls_picam(camera_options,requested_options[name])
						applied_options[name] = {
							PI_REAL_TO_CANONICAL[real_name]: value
							for real_name, value in resolved[details['source']].items()
						}
						# Only carry controls when some are configured, so the camera dict
						# otherwise matches the USB one; picam treats missing as {}.
						if resolved[details['source']]:
							camera_list[name]['controls'] = resolved[details['source']]

					elif details['cameratype'] == 'STREAM':
						logger.debug(f'Skipping camera controls for stream {name}')

					else:
						logger.warning(f'Unrecognized camera type {details['cameratype']}')
						continue
				except Exception as e:
					logger.warning(f'[{name}] Could not set up camera {details['source']}; skipping it\nIs it being used by another process?.\nError reported was - {e}')
					failed_cameras.add(name)
					continue
				'''
				camera_list[name].update({
				key: value
				for key, value in requested_options.items()
				if key in DefaultCameraSettings.__members__
				})
				'''

			# Remove any cameras that are not present or could not be set up
			for camera, details in list(camera_list.items()):
				if (details['source'] not in installed_cameras) and (details['cameratype'] != 'STREAM'):
					camera_list.pop(camera)
					logger.debug(f'Camera {camera} with source {details['source']} removed')
					continue
				if camera in failed_cameras:
					camera_list.pop(camera)
					logger.debug(f'Camera {camera} with source {details['source']} removed')
					continue

				# Validate this camera's format, resolution, and fps.
				try:
					if details['cameratype'] == 'USB':
						validated_camera = validate_usb_camera_configs({camera: details}, applied_options)
						camera_list[camera] = validated_camera[camera]
					elif details['cameratype'] == 'PICAMERA':
						validated_camera = validate_pi_camera_configs({camera: details}, applied_options)
						camera_list[camera] = validated_camera[camera]
				except Exception as e:
					logger.warning(f'[{camera}] Could not validate camera {details['source']}; skipping it - {e}')
					camera_list.pop(camera)



			camera_results = []

			camera_results.append("Actual Camera Settings")
			for name, options in camera_list.items():
				for option, value in options.items():
					if option == 'name':
						camera_results.append(f'\n{value}')
					else:
						if option != 'controls': # Dont want this displayed
							camera_results.append(f'\t--{option} = {value}')

			highlight_print(camera_results)

			return camera_list
		except Exception as e:
			logger.exception('Camera option setup failed')
			raise Exception(f'Error setting camera options {e}') from e
