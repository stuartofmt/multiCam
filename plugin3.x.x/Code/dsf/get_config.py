import configparser
import os
import json
from config import ALLOWED_CAMERA_OPTIONS

global UI, LOGGING, CAMERAS
# From https://gist.github.com/laywill/63d75b53e8a7a801d77f0dd2b97de54d
class DictToClass:
	def __init__(self, dictionary):
		for key, value in dictionary.items():
			if isinstance(value, dict):
				value = DictToClass(value)
			setattr(self, key, value)

def get_camera_config(config, name, source, cameratype):
	"""
	Build the JSON string of settings for a single camera.

	- name: camera name (matches [CAMERAS]/[PICAMERAS] key and optional section header)
	- source: value from [CAMERAS] or [PICAMERAS], e.g. /dev/video0 or a pi camera index
	- cameratype: e.g. "USB" or "picamera"
	- Any options found in the camera's own section are cast to int
	  and merged in as-is (no defaults applied).
	"""
	options = {}
	if config.has_section(name):
		allowed_options = {option.lower() for option in ALLOWED_CAMERA_OPTIONS}
		options = {
			key.lower(): float(value)
			for key, value in config[name].items()
			if key.lower() in allowed_options
		}
		

	camera_data = {
		key.lower(): value
		for key, value in {
			"name": name,
			"source": source,
			"cameratype": cameratype,
			**options
		}.items()
	}

	return json.dumps(camera_data)



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

			for name, source in config['CAMERAS'].items():
				CAMERAS[name] = get_camera_config(config, name, source,"USB")

			for name, source in config['PICAMERAS'].items():
				CAMERAS[name] = get_camera_config(config, name, source, "picamera")

			# Check CAMERAS and PICAMERAS are not both empty
			if CAMERAS == {}:
				raise ValueError('At least one camera must be specified in CAMERAS or PICAMERAS')		
				
			# All tests passed - log effective configuration
			logger.info("Configured Camera Settings")
			for name, options in CAMERAS.items():
				logger.info(f'{options}')


			return True
		except Exception as e:
			logger.critical(f'Error parsing config file {config_file}')
			logger.critical(f'{e}')
			return False
