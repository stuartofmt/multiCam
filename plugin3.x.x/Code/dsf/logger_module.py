"""
Logging module needs to be imported twice during startup
The first time imports the setup functions
After the setup functions have been called,
the logger can be imported 

All other modules simply need to import the logger

"""
import logging
import sys
import os

global logger

def setup_log(progName,logfile):
	global logger		
	# Create logger
	try:	
		logger = logging.getLogger(progName)
		logger.propagate = False
		
		# set initial logging level
		logger.setLevel(logging.INFO)

		# Create handler for console output - file output handler is created
		c_handler = logging.StreamHandler(sys.stdout)
		format = f'{progName} "%(asctime)s [%(levelname)s] %(message)s"'
		c_format = logging.Formatter(format)
		c_handler.setFormatter(c_format)
		logger.addHandler(c_handler)
		create_log_file(logger,logfile)
		return logger
	except Exception as e:
		print(f"Failed to setup logging: {e}")
		return False
	finally:
		try:
			create_log_file(logger,logfile)
		except Exception as e:
			logger.critical(f"Failed to create logfile {logfile}")
			logger.critical(f"{e}")
		return logger

def create_log_file(console_logger, logfile):
	global logger
	try:	
		if os.path.exists(logfile):
			os.remove(logfile)

		# Create handler for logfile
		f_handler = logging.FileHandler(logfile, mode='w', encoding='utf-8')
		_set_file_formatter(console_logger, f_handler)
		console_logger.addHandler(f_handler)
		logger = console_logger
		return True
	except Exception as e:
		raise Exception(f"{e}")
		return False

def _set_file_formatter(log, handler):
	if log.level == logging.INFO:
		f_format = logging.Formatter(
			"%(asctime)s  %(message)s", "%m-%d %H:%M:%S"
		)
	else:
		f_format = logging.Formatter(
			"%(asctime)s %(module)s - %(funcName)s:[%(levelname)s] %(message)s",
			"%m-%d %H:%M:%S",
		)
	handler.setFormatter(f_format)

def set_log_level(log_level,logger):  
	logger.debug(f'Log level changed to {log_level}')

	if log_level == 'DEBUG':
		logger.setLevel(logging.DEBUG)
	elif log_level == 'INFO':
		logger.setLevel(logging.INFO)
	else: # warning
		logger.setLevel(logging.WARNING)

	for handler in logger.handlers:
		if isinstance(handler, logging.FileHandler):
			_set_file_formatter(logger, handler)

	return logger
