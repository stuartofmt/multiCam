import configparser
import os
import json
import re
import subprocess
import sys

from defaults import DefaultCameraOptions
from logger_module import logger

global UI, LOGGING, CAMERAS
# From https://gist.github.com/laywill/63d75b53e8a7a801d77f0dd2b97de54d
class DictToClass:
	def __init__(self, dictionary):
		for key, value in dictionary.items():
			if isinstance(value, dict):
				value = DictToClass(value)
			setattr(self, key, value)

# Map a canonical setting name to the possible real control names a driver
# might expose for it. First match wins.
CONTROL_ALIASES = {
	"contrast": ["contrast"],
	"white_balance_auto": [
		"white_balance_temperature_auto",
		"white_balance_automatic",
		"auto_white_balance",
	],
	"white_balance_temperature": ["white_balance_temperature"],
	"brightness": ["brightness"],
	"saturation": ["saturation"],
	"sharpness": ["sharpness"],
}


def get_camera_defaults(device_path="/dev/video0"):
	print(f'{device_path=}')
	# 1. Query the device control list (no --reset-all; it doesn't exist)
	try:
		result = subprocess.run(
			["v4l2-ctl", "-d", device_path, "--list-ctrls"],
			check=True,
			capture_output=True,
			text=True,
		)
	except subprocess.CalledProcessError as e:
		logger.debug(f"Error listing controls for {device_path}: {e.stderr}")
		return None
	except FileNotFoundError:
		logger.debug(
			"Error: 'v4l2-ctl' utility not found. Install it using 'sudo apt install v4l-utils'."
		)
		return None

	lines = [line.strip() for line in result.stdout.splitlines()]

	# canonical_name -> {"real_name": str, "default": int}
	target_controls = {name: None for name in CONTROL_ALIASES}

	# 2. Parse control output, trying each alias per canonical setting
	for canonical_name, aliases in CONTROL_ALIASES.items():
		for alias in aliases:
			for line in lines:
				if line.startswith(alias):
					match = re.search(r"default=(-?\d+)", line)
					if match:
						target_controls[canonical_name] = {
							"real_name": alias,
							"default": int(match.group(1)),
						}
					break  # stop scanning lines once alias is found
			if target_controls[canonical_name] is not None:
				break  # stop trying other aliases once one matched

	# 3. Reset each supported control to its default value
	ctrl_args = [
		f"{info['real_name']}={info['default']}"
		for info in target_controls.values()
		if info is not None
	]
	if ctrl_args:
		try:
			subprocess.run(
				["v4l2-ctl", "-d", device_path, "--set-ctrl", ",".join(ctrl_args)],
				check=True,
				capture_output=True,
				text=True,
			)
			logger.info(f"Successfully reset controls for {device_path}")
		except subprocess.CalledProcessError as e:
			logger.debug(f"Error resetting {device_path}: {e.stderr}")
	else:
		logger.debug("No supported controls found to reset.")
	logger.info(target_controls)
	return target_controls


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
		valid_options = {option.name.lower() for option in DefaultCameraOptions}
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
		option.name: float(option.value)
		for option in DefaultCameraOptions
	}

	if config.has_section(name):
		valid_options = {option.name.lower() for option in DefaultCameraOptions}
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
		for key, value in {
			"name": name,
			**file_options
		}.items()
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
			print('Starting Defaults')
			for name, source in config['CAMERAS'].items():
				CAMERAS[name], camera_config_options[name] = get_config_from_file(config, name, source,"USB")
				CAMERAS[name]=get_camera_defaults(CAMERAS[name]['source'])
			for name, source in config['PICAMERAS'].items():
				CAMERAS[name] , camera_config_options[name] = get_config_from_file(config, name, source, "picamera")

			# Check CAMERAS and PICAMERAS are not both empty
			if CAMERAS == {}:
				raise ValueError('At least one camera must be specified in CAMERAS or PICAMERAS')		

			# All tests passed - log effective configuration
			logger.info("Configured Camera Settings")
			for name, options in CAMERAS.items():
				logger.info(f'''{options}''')
			import sys
			sys.exit(0)

			return True
		except Exception as e:
			logger.critical(f'Error parsing config file {config_file}')
			logger.critical(f'{e}')
			return False
