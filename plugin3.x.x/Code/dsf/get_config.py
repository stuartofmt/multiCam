import configparser
import os
import sys
from venv import logger

global DUET, UI, LOGGING, ACTION, MACRO, NTFY, PUSHOVER

# From https://gist.github.com/laywill/63d75b53e8a7a801d77f0dd2b97de54d
class DictToClass:
	def __init__(self, dictionary):
		for key, value in dictionary.items():
			if isinstance(value, dict):
				value = DictToClass(value)
			setattr(self, key, value)

def parse_config(config_file,logger):
	global UI, LOGGING, CAMERAS, PICAMERAS

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
			cameras_section = config_dict["CAMERAS"]
			picameras_section = config_dict["PICAMERAS"]
			

			#Change the keys to UPPER
			config_dict_ui = {k.upper():v for k,v in ui_section.items()}
			config_dict_logging = {k.upper():v for k,v in logging_section.items()}
			config_dict_cameras = {k.upper():v for k,v in cameras_section.items()}
			config_dict_picameras = {k.upper():v for k,v in picameras_section.items()}

			#Convert to dot dict

			UI = DictToClass(config_dict_ui)
			LOGGING = DictToClass(config_dict_logging)
			CAMERAS = DictToClass(config_dict_cameras)
			PICAMERAS = DictToClass(config_dict_picameras)

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

			# CAMERAS
			if CAMERAS is None or CAMERAS.__dict__ == {}:
				raise ValueError('CAMERAS section must have at least one Camera specified')

			# PICAMERAS
			if PICAMERAS is None:
				PICAMERAS = DictToClass({})
				
			# All tests passed
			return True
		except Exception as e:
			logger.critical(f'Error parsing config file {config_file}')
			logger.critical(f'{e}')
			return False
