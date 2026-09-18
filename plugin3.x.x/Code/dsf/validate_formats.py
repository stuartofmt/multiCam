"""
validate_camera_formats.py

Validates a camera-config dict against what each /dev/videoN device
actually reports via `v4l2-ctl -d <source> --list-formats-ext`, and
adjusts format / resolution / fps to the closest supported values.

Fallback rules (as specified):
  1. If the requested format (MJPEG/MPEG -> MJPG) isn't supported,
	 fall back to the first format the device reports.
  2. If the requested width/height IS supported under the chosen
	 format, but the requested fps is not, drop to the next lower
	 fps available at that resolution.
  3. If the requested width/height is NOT supported, drop to the
	 next lower resolution (by pixel area) available under the
	 chosen format. If the original fps isn't available at that new
	 resolution, drop to the next lower fps available there.
"""

import copy
import logging
import re
import subprocess
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Aliases: config may say "MPEG"/"MJPEG", v4l2-ctl reports the fourcc "MJPG".
FORMAT_ALIASES = {
	"MPEG": "MJPG",
	"MJPEG": "MJPG",
}

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
	than requested. Falls back to the largest available if nothing is
	smaller (e.g. requested was already the smallest/unmatched)."""

	if not available_whs:
		return None

	requested_area = requested_wh[0] * requested_wh[1]

	by_area_desc = sorted(
		set(available_whs),
		key=lambda wh: wh[0] * wh[1],
		reverse=True,
	)

	lower = [wh for wh in by_area_desc if wh[0] * wh[1] < requested_area]

	return lower[0] if lower else by_area_desc[0]


def _next_lower_fps(
	requested_fps: float,
	available_fps: List[float],
) -> Optional[float]:
	"""Highest available fps that is still lower than requested. Falls
	back to the highest available fps if nothing is lower."""

	if not available_fps:
		return None

	by_fps_desc = sorted(set(available_fps), reverse=True)

	lower = [f for f in by_fps_desc if f < requested_fps]

	return lower[0] if lower else by_fps_desc[0]


def validate_camera_configs(cameras: Dict[str, dict]) -> Dict[str, dict]:
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

	Returns a NEW dict (the input is not mutated). Entries whose
	source can't be queried via v4l2-ctl (e.g. it isn't a local V4L2
	device, or the device didn't respond) are returned unchanged.
	"""

	adjusted = copy.deepcopy(cameras)

	for cam_name, cam in adjusted.items():

		source = cam.get("source")

		output = _run_v4l2_ctl(source)
		if output is None:
			logger.info(f"[{cam_name}] Skipping format validation for {source}")
			continue

		formats = _parse_formats(output)

		if not formats:
			logger.warning(f"[{cam_name}] No formats parsed for {source}; skipping")
			continue

		# --- Step 1: format ---
		requested_format = _normalize_format(str(cam.get("format", "")))

		if requested_format in formats:
			chosen_format = requested_format
		else:
			chosen_format = next(iter(formats))  # first reported format
			logger.info(
				f"[{cam_name}] Format '{cam.get('format')}' not supported on "
				f"{source}; falling back to '{chosen_format}'"
			)

		cam["format"] = chosen_format
		resolutions = formats[chosen_format]

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
				f"supported for '{chosen_format}' on {source}; falling back to "
				f"{chosen_wh[0]}x{chosen_wh[1]}"
			)

		cam["width"], cam["height"] = chosen_wh

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
			logger.info(
				f"[{cam_name}] fps {requested_fps} not supported at "
				f"{chosen_wh[0]}x{chosen_wh[1]} for '{chosen_format}' on "
				f"{source}; falling back to {chosen_fps}"
			)

		cam["fps"] = chosen_fps

	return adjusted


if __name__ == "__main__":

	logging.basicConfig(level=logging.INFO)

	example_cameras = {
		"Cam 2": {
			"name": "Cam 2",
			"source": "/dev/video2",
			"cameratype": "USB",
			"fps": 15,
			"width": 1024,
			"height": 720,
			"jpegresolution": 95,
			"rotate": 0,
			"format": "MPEG",
		}
	}

	result = validate_camera_configs(example_cameras)
	print(result)
