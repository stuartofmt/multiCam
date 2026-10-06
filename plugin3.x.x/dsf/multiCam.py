"""
The she-bang is not required if called with fully qualified paths
The plugin manager does this.  Otherwise use ...
Standard python install e.g.
#!/usr/bin/python3 -u
Venv python install e.g.
#! <path-to-virtual-environment/bin>python -u
"""

# This is to supress the noisy libcam
# MUST BE AT THE VERY START OF THE SCRIPT
import os
os.environ["LIBCAMERA_LOG_LEVELS"] = "*:ERROR"

from pathlib import Path
import sys

import httpx
import threading
import time
import uvicorn
import socket
import signal

from routes import app, start_cameras, set_settings_state
from settings_config import ensure_settings_file

from logger_module import (setup_log, set_log_level)

def port_in_use(ip_address, port):
	#  A successful connection means something is already listening there
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
		sock.settimeout(1)
		return sock.connect_ex((ip_address, port)) == 0


def validate_port(port=0, start_port=17800, max_tries=100):
	#  Get the local ip address
	this_ip_address = ''
	s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	try:
		s.connect(('10.255.255.255', 1))  # doesn't even have to be reachable
		this_ip_address = s.getsockname()[0]
	except Exception as e:
		logger.critical(f'''Unknown error trying to get the local IP address''')
		logger.critical(f'''{e}''')
		force_quit(1)
	finally:
		s.close()

	if port:
		#  A port was provided - check that it is available
		if port_in_use(this_ip_address, port):
			logger.warning(f'''Port {port} is already in use - falling back to searching from {start_port}''')
			port = 0
	else:
		logger.info(f'''No port number was provided - searching for a free port starting at {start_port}''')

	if not port:
		#  No usable port yet - search for one starting at start_port
		for candidate in range(start_port, start_port + max_tries):
			if not port_in_use(this_ip_address, candidate):
				port = candidate
				break
		else:
			logger.critical(f'''No free port found between {start_port} and {start_port + max_tries - 1}''')
			force_quit(1)

	logger.info(f'''IP address {this_ip_address} with port {port} is available''')
	return this_ip_address, port


def force_quit(code):
	logger.critical(f'''Terminating the program with exit code {code}''')
	sys.exit(code)

def sig_handler(signum, frame):
	signame = signal.Signals(signum).name
	logger.info(f'Shutting down.  Recieved signal {signame} ({signum})')
	force_quit(0)


if __name__ == "__main__":

	# shutdown with SIGINT (kill -2 <pid> or SIGTERM)
	signal.signal(signal.SIGINT, sig_handler)
	signal.signal(signal.SIGTERM, sig_handler)

	global progName, progVersion
	progName = os.path.splitext(os.path.basename(sys.argv[0]))[0]
	progVersion = '1.0.0'

	LOGFILENAME = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "multiCam.log"
	#  The settings file is kept in the same folder as the logfile
	SETTINGSFILENAME = LOGFILENAME.parent / 'settings.config'
	print (f'{SETTINGSFILENAME=}')
	#  Create the settings file (and folder), or replace it if it fails the checksum
	try:
		settings_message = ensure_settings_file(SETTINGSFILENAME)
	except Exception as e:
		print(f'Could not create settings file {SETTINGSFILENAME}: {e}')
		sys.exit(1)


	if not setup_log(progName,LOGFILENAME):
		logger.error(f"Failed to setup logging to {LOGFILENAME}. Please ensure the file is writable.")
		sys.exit(1)

	from logger_module import logger # Need to import after setup_logging is called
	logger.info(f'''Log file for {progName} -- {progVersion}''')
	if settings_message:
		logger.info(settings_message)

	from get_config import (get_port, parse_config, get_installed_cameras, configure_cameras,
						get_device_capabilities, get_effective_settings)

	try:
		# Get port from settings file
		PORT = get_port(SETTINGSFILENAME)
		print(f'{PORT=}')
		this_ip_address, PORT = validate_port(PORT)
	except Exception as e:
		logger.info(f'{e}')
		force_quit(1)

	try:
		# Get log level and cameras from settings file
		LOGLEVEL, cameras_to_use, cameras_to_use_configs = parse_config(SETTINGSFILENAME,logger)
	except Exception as e:
		logger.info(f'{e}')
		force_quit(1)


	# Set logging level
	logger = set_log_level(LOGLEVEL,logger)

	# Set camera options to default values and override only if configured 
	try:
		installed_cameras = get_installed_cameras()
		configured_cameras = configure_cameras(installed_cameras,cameras_to_use, cameras_to_use_configs)
	except Exception as e:
		logger.info(f'{e}')
		force_quit(1)

	# Values shown on the settings page
	try:
		set_settings_state(SETTINGSFILENAME, get_device_capabilities(installed_cameras), get_effective_settings(configured_cameras), PORT)
	except Exception as e:
		logger.warning(f'Settings page will not show camera capabilities - {e}')
		set_settings_state(SETTINGSFILENAME, {"USB": {}, "PICAMERA": {}}, {}, PORT)



	# Start uvicorn in a background thread
	def run_server():
		uvicorn.run(
			app,
			host=this_ip_address,
			port=PORT,
			reload=False,
			log_config=None
		)

	server_thread = threading.Thread(target=run_server, daemon=True)
	server_thread.start()

	logger.info("Waiting for server to be ready")
	time.sleep(2)

	with httpx.Client() as client:
			# Use case sensitive order based on keys		
			for camera_name, camera_settings in sorted(configured_cameras.items()):
				try:
					camera_payload = camera_settings
					response = client.post(
						f"http://{this_ip_address}:{PORT}/api/add-camera",
						json=camera_payload,
					)
					if response.is_success and response.json().get("status") == "success":
						logger.info(f"Added {camera_name} with source '{camera_payload['source']}'")
					else:
						logger.error(f"Error adding camera {camera_name}: {response.text}")
				except Exception as e:
					logger.error(f"Error adding camera {camera_name}: {e}")

	# Start all cameras after registration
	try:
		start_cameras()
	except Exception as e:
		logger.critical(f"{e}")
		force_quit(1)

	logger.info('-------------------------------------------------------\n')
	logger.info(f"View cameras at http://{this_ip_address}:{PORT}\n")
	logger.info(f"Manage settings at http://{this_ip_address}:{PORT}/settings\n")

	for camera_name, camera_settings in configured_cameras.items():
		camera_name = camera_name.replace(' ', '%20')
		logger.info(f"{camera_name} streaming url is http://{this_ip_address}:{PORT}/{camera_name}/{camera_settings['streamname']}")
		logger.info(f"{camera_name} snapshot url is http://{this_ip_address}:{PORT}/{camera_name}/{camera_settings['snapshotname']}\n")

	# Keep the main thread alive
	server_thread.join()
