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

from routes import app, start_cameras

from logger_module import (setup_log, set_log_level)

def getIP(port):
	#  Get the IP and check if Port are available for use
	this_ip_address = ''
	if port != 0:
		#  Get the local ip address
		s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		try:
			s.connect(('10.255.255.255', 1))  # doesn't even have to be reachable
			this_ip_address = s.getsockname()[0]
		except Exception as e:
			logger.critical(f'''Unknown error trying to get the local IP address''')
			logger.critical(f'''{e}''')
			s.close()
			force_quit(1)
		finally:
			s.close()

		# Check that the port is available
		try:
			sock = socket.socket()
			if sock.connect_ex((this_ip_address, port)) == 0:
				raise Exception(f'''Port {port} is already in use.''')
		except Exception as e:
			logger.critical(f'''{e}''')
			sock.close()
			force_quit(1)  
	else:
		logger.critical('No port number was provided - terminating the program')
		force_quit(1)
	
	logger.info(f'''IP address {this_ip_address} with port {port} is available''')
	return this_ip_address

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
	progVersion = '0.0.1'

	CONFIGFILENAME = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "config.ini"
	LOGFILENAME = CONFIGFILENAME.parent / "multiCam.log"

	if not setup_log(progName,LOGFILENAME):
		logger.error(f"Failed to setup logging to {LOGFILENAME}. Please ensure the file is writable.")
		sys.exit(1)

	from logger_module import logger # Need to import after setup_logging is called
	logger.info(f'''Log file for {progName} -- {progVersion}''')

	
	from get_config import parse_config, get_installed_cameras, configure_cameras

	try:
		# Get info from config file
		PORT, LOGLEVEL, cameras_to_use, cameras_to_use_configs = parse_config(CONFIGFILENAME,logger)
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

	this_ip_address = getIP(PORT)

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
		for camera_name, camera_settings in configured_cameras.items():
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
	for camera_name, camera_settings in configured_cameras.items():
		logger.info(f"{camera_name} streaming url is http://{this_ip_address}:{PORT}/{camera_name}/{camera_settings['streamname']}")
		logger.info(f"{camera_name} snapshot url is http://{this_ip_address}:{PORT}/{camera_name}/{camera_settings['snapshotname']}\n")

	# Keep the main thread alive
	server_thread.join()
