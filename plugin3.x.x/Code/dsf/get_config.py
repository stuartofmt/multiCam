import configparser
import os
import json
import re
import subprocess
import sys

from defaults import DefaultCameraOptions, ALLOWABLE_OPTIONS
from logger_module import logger

global UI, LOGGING, CAMERAS
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


def _parse_list_ctrls(device_path):
    """
    Run v4l2-ctl --list-ctrls and parse out {real_name: (min, max, default)}
    for every control the driver reports.
    """
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


def probe_writable_controls_usb(camera_name, source):
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
        Only controls in ALLOWABLE_OPTIONS that are confirmed present and
        successfully reset on this camera are included, even though every
        control on the camera was reset.
    """
    all_controls = _parse_list_ctrls(source)

    writable_controls = {}
    reverse_lookup = {}  # real_name actually used -> canonical_name

    for canonical_name in ALLOWABLE_OPTIONS:
        real_name = _resolve_real_name(canonical_name, all_controls.keys())
        if real_name is None:
            logger.debug(f"[{camera_name}] {canonical_name}: not present on this camera")
            continue
        reverse_lookup[real_name] = canonical_name

    for real_name, (min_val, max_val, default_val) in all_controls.items():
        if default_val is None:
            logger.debug(f"[{camera_name}] {real_name}: no default value, skipping")
            continue

        try:
            subprocess.run(
                ["v4l2-ctl", "-d", source, "--set-ctrl", f"{real_name}={default_val}"],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info(f"[{camera_name}] {real_name}: reset to default={default_val}")
        except subprocess.CalledProcessError as e:
            logger.debug(f"[{camera_name}] {real_name} not settable: {e.stderr}")
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

    return {source: writable_controls}

def set_controls_usb(controls_by_source):
    """
    Apply controls to one or more USB/UVC cameras, given a dict in the
    same shape as probe_writable_controls_usb's return value:

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
            values in the same shape produced by probe_writable_controls_usb.

    Returns:
        A dict keyed on source, each value a dict of
        {canonical_name: True/False} indicating whether the set succeeded.
    """
    results_by_source = {}

    for source, controls in controls_by_source.items():
        all_real_controls = _parse_list_ctrls(source).keys()
        results = {}

        for canonical_name, bounds in controls.items():
            real_name = _resolve_real_name(canonical_name, all_real_controls)
            if real_name is None:
                logger.debug(f"[{source}] {canonical_name}: not present on this camera")
                results[canonical_name] = False
                continue

            value = bounds.get("default")
            min_val = bounds.get("min")
            max_val = bounds.get("max")

            if value is None:
                logger.debug(f"[{source}] {canonical_name}: no value to set, skipping")
                results[canonical_name] = False
                continue

            if min_val is not None and value < min_val:
                logger.debug(f"[{source}] {canonical_name}: value {value} below min {min_val}, clamping")
                value = min_val
            if max_val is not None and value > max_val:
                logger.debug(f"[{source}] {canonical_name}: value {value} above max {max_val}, clamping")
                value = max_val

            try:
                subprocess.run(
                    ["v4l2-ctl", "-d", source, "--set-ctrl", f"{real_name}={value}"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                logger.info(f"[{source}] {canonical_name} ({real_name}): set to {value}")
                results[canonical_name] = True
            except subprocess.CalledProcessError as e:
                logger.debug(f"[{source}] {canonical_name} ({real_name}) not settable: {e.stderr}")
                results[canonical_name] = False

        results_by_source[source] = results

    return results_by_source

# ----------------------------------
#  PI FUNCTIONS
# ----------------------------------


# Maps each allowable canonical key to the real Picamera2/libcamera control name.
PI_CONTROL_NAME_MAP = {
    "brightness": "Brightness",
    "contrast": "Contrast",
    "balance": "AwbEnable",
    "saturation": "Saturation",
    "autofocus": "AfMode",
    "sharpness": "Sharpness",
    "autoexposure": "AeEnable",
}

# Reverse lookup: real Picamera2 control name -> canonical name
PI_REAL_TO_CANONICAL = {v: k for k, v in PI_CONTROL_NAME_MAP.items()}


def probe_writable_controls(camera_name, source):
    """
    Reset ALL controls the camera reports back to their default values,
    but return min/max/default only for the subset in ALLOWABLE_OPTIONS.

    Args:
        camera_name: Human-readable label for this camera, used in logging.
        source: Camera index (int) or identifier passed to Picamera2(camera_num=source).
                Also used as the key in the returned dict.

    Returns:
        A dict keyed on `source`:
            {
                source: {
                    canonical_name: {"min": ..., "max": ..., "default": ...},
                    ...
                }
            }
        Only controls in ALLOWABLE_OPTIONS that are confirmed present and
        writable on this camera are included, even though every control
        on the camera was reset.
    """
    picam2 = Picamera2(camera_num=source)
    picam2.start()

    all_controls = picam2.camera_controls
    writable_controls = {}

    for real_name, (min_val, max_val, default_val) in all_controls.items():
        if default_val is None:
            logger.debug(f"[{camera_name}] {real_name}: no default value, skipping")
            continue

        try:
            picam2.set_controls({real_name: default_val})
            picam2.capture_metadata()  # force it to actually apply
            logger.info(f"[{camera_name}] {real_name}: reset to default={default_val}")
        except Exception as e:
            logger.debug(f"[{camera_name}] {real_name} not settable: {e}")
            continue

        canonical_name = PI_REAL_TO_CANONICAL.get(real_name)
        if canonical_name is None:
            continue  # not one of the allowable options, don't include in output

        resolved_min = min_val if min_val is not None else default_val
        resolved_max = max_val if max_val is not None else default_val

        writable_controls[canonical_name] = {
            "min": resolved_min,
            "max": resolved_max,
            "default": default_val,
        }

    picam2.close()

    return {source: writable_controls}

def set_controls_picam(controls_by_source):
    """
    Apply controls to one or more Pi cameras, given a dict in the same
    shape as probe_writable_controls's return value:

        {
            source: {
                canonical_name: {"min": ..., "max": ..., "default": ...},
                ...
            },
            ...
        }

    For each entry, the "default" value is written to the camera via
    Picamera2.set_controls, clamped to [min, max] first as a safety check.

    Args:
        controls_by_source: dict keyed on source (camera_num), with
            values in the same shape produced by probe_writable_controls.

    Returns:
        A dict keyed on source, each value a dict of
        {canonical_name: True/False} indicating whether the set succeeded.
    """
    results_by_source = {}

    for source, controls in controls_by_source.items():
        picam2 = Picamera2(camera_num=source)
        picam2.start()

        results = {}

        for canonical_name, bounds in controls.items():
            real_name = CONTROL_NAME_MAP.get(canonical_name)
            if real_name is None or real_name not in picam2.camera_controls:
                logger.debug(f"[{source}] {canonical_name}: not present on this camera")
                results[canonical_name] = False
                continue

            value = bounds.get("default")
            min_val = bounds.get("min")
            max_val = bounds.get("max")

            if value is None:
                logger.debug(f"[{source}] {canonical_name}: no value to set, skipping")
                results[canonical_name] = False
                continue

            if min_val is not None and value < min_val:
                logger.debug(f"[{source}] {canonical_name}: value {value} below min {min_val}, clamping")
                value = min_val
            if max_val is not None and value > max_val:
                logger.debug(f"[{source}] {canonical_name}: value {value} above max {max_val}, clamping")
                value = max_val

            try:
                picam2.set_controls({real_name: value})
                picam2.capture_metadata()  # force it to actually apply
                logger.info(f"[{source}] {canonical_name} ({real_name}): set to {value}")
                results[canonical_name] = True
            except Exception as e:
                logger.debug(f"[{source}] {canonical_name} ({real_name}) not settable: {e}")
                results[canonical_name] = False

        picam2.close()
        results_by_source[source] = results

    return results_by_source


def update_camera_config(config, options):
	"""
	Build the JSON string of settings for a single camera.

	- name: camera name (matches [CAMERAS]/[PICAMERAS] key and optional section header)
	- source: value from [CAMERAS] or [PICAMERAS], e.g. /dev/video0 or a pi camera index
	- cameratype: e.g. "USB" or "picamera"
	- Any options found in the camera's own section are cast to int
	  and merged in as-is (no defaults applied).
	"""

	if config.has_section(options['name']):
		valid_options = {option_name.lower() for option_name in DefaultCameraOptions.__members__}
		options.update({
			key.lower(): float(value)
			for key, value in config[name].items()
			if key.lower() in valid_options
		})
		

	camera_data = {
		key.lower(): value
		for key, value in {
			"name": name,
			"source": source,
			"cameratype": cameratype,
			**options
		}.items()
	}

	return camera_data

def get_config_from_file(config, name, source, cameratype):
	"""
	Build the default settings dictionaries for a single camera.
	"""

	default_options = {}
	file_options = {}

	default_options = {
		option_name: float(option.value)
		for option_name, option in DefaultCameraOptions.__members__.items()
	}

	if config.has_section(name):
		valid_options = {option_name.lower() for option_name in DefaultCameraOptions.__members__}
		file_options.update({
			key.lower(): float(value)
			for key, value in config[name].items()
			if key.lower() in valid_options
		})
		

	camera_data = {
		key.lower(): value
		for key, value in {
			"name": name,
			"source": source,
			"cameratype": cameratype,
			**default_options
		}.items()
	}

	camera_file_options = {
		key.lower(): value
		for key, value in file_options.items()
	}

	return camera_data, camera_file_options


def parse_config(config_file,logger):
	global UI, LOGGING, CAMERAS

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

			#Change the keys to UPPER for UI and LOGGING, but not for CAMERAS or PICAMERAS	
			config_dict_ui = {k.upper():v for k,v in ui_section.items()}
			config_dict_logging = {k.upper():v for k,v in logging_section.items()}

			#Convert UI and LOGGING to dot dict
			UI = DictToClass(config_dict_ui)
			LOGGING = DictToClass(config_dict_logging)

			# Adjust types and values
			# UI
			if not hasattr(UI,'PORT'):
				raise ValueError('UI section must have a PORT specified')
			
			UI.PORT = int(UI.PORT)
			if UI.PORT < 1024 or UI.PORT > 65535:
				raise ValueError('UI PORT must be between 1024 and 65535')

			# LOGGING
			if not hasattr(LOGGING,'LEVEL') : LOGGING.LEVEL = 'INFO'        
			if LOGGING.LEVEL not in ['DEBUG','INFO','WARNING']:
				raise ValueError('LOGGING LEVEL must be one of DEBUG, INFO, WARNING')

			# Process all Camera settings

			CAMERAS = {}
			camera_config_options = {}

			# Set options
			# First set program defaults (all set to PiCamera defaults)
			# if USB update options with defaults set by the camera
			# Then over write with options from config file

			for name, source in config['CAMERAS'].items():
				CAMERAS[name], camera_config_options[name] = get_config_from_file(config, name, source,"USB")
				#Ignore http etc
				if CAMERAS[name]['source'].startswith('/dev/video'):
					camera_defaults = get_usb_camera_defaults(CAMERAS[name]['source'])
					if camera_defaults:
						for option_name, limits in camera_defaults.items():
							CAMERAS[name][option_name] = float(limits.get("default", 0))
							CAMERAS[name].setdefault("_control_ranges", {})[option_name] = limits

					CAMERAS[name].update(
						{
							key: value
							for key, value in camera_config_options[name].items()
							if key != "name"
						}
					)

					set_usb_camera_controls(CAMERAS[name]['source'], CAMERAS[name])

			#for USB cameras Set camera controls

			
			for name, source in config['PICAMERAS'].items():
				CAMERAS[name] , camera_config_options[name] = get_config_from_file(config, name, source, "picamera")

			# Check CAMERAS and PICAMERAS are not both empty
			if CAMERAS == {}:
				raise ValueError('At least one camera must be specified in CAMERAS or PICAMERAS')		

			# All tests passed - log effective configuration
			camera_results = [f"\n", "-" * 95]
			
			camera_results.append("Configured Camera Settings")
			for name, options in CAMERAS.items():
				camera_results.append(f'''{options}''')

			camera_results.append("\n")
			logger.info("\n".join(camera_results))



			return True
		except Exception as e:
			logger.critical(f'Error parsing config file {config_file}')
			logger.critical(f'{e}')
			return False
