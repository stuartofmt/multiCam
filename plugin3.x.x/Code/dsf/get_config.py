import configparser
import os
import sys

global DUET, UI, LOGGING, ACTION, MACRO, NTFY, PUSHOVER

# From https://gist.github.com/laywill/63d75b53e8a7a801d77f0dd2b97de54d
class DictToClass:
	def __init__(self, dictionary):
		for key, value in dictionary.items():
			if isinstance(value, dict):
				value = DictToClass(value)
			setattr(self, key, value)

def parse_config(config_file,logger):
	global UI, LOGGING, CAMERAS
	logger.debug(f"Looking for config file at {config_file}")

	if os.path.exists(config_file):
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

		#Change the keys to UPPER
		config_dict_ui = {k.upper():v for k,v in ui_section.items()}
		config_dict_logging = {k.upper():v for k,v in logging_section.items()}
		config_dict_cameras = dict(cameras_section)

		#Convert to dot dict

		UI = DictToClass(config_dict_ui)
		LOGGING = DictToClass(config_dict_logging)
		CAMERAS = DictToClass(config_dict_cameras)

		# Adjust types and values
		# UI
		if not hasattr(UI,'PORT'):
			logger.critical('UI section must have a PORT specified')
			sys.exit(1)

		UI.HOST = '0.0.0.0'
		UI.PORT = int(UI.PORT)

		# LOGGING
		if not hasattr(LOGGING,'LEVEL') : LOGGING.LEVEL = 'INFO'        

		# CAMERAS
		if CAMERAS is None or CAMERAS.__dict__ == {}:
			logger.critical('CAMERAS section must have at least one Camera specified')
			sys.exit(1)
		return True
	
	return False
