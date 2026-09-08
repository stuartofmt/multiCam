"""
The she-bang is not required if called with fully qualified paths
The plugin manager does this.  Otherwise use ...
Standard python install e.g.
#!/usr/bin/python3 -u
Venv python install e.g.
#! <path-to-virtual-environment/bin>python -u
"""


from pathlib import Path
import sys
import os

import httpx
import threading
import time
import uvicorn
import socket
import signal

# from config import STATIC_DIR
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
			logger.warning(f'''Make sure IP address {ip_address} is reachable and unique''')
			logger.warning(f'''{e}''')
		finally:
			s.close()

		# Check that the port is available
		try:
			sock = socket.socket()
		except Exception as e:
			logger.critical(f'''Unknown error trying to open a socket''')
			logger.critical(f'''{e}''')
			force_quit(1)  
		finally:
			if sock.connect_ex((this_ip_address, port)) == 0:
				logger.critical(f'''Port {port} is already in use.''')
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

	global logger

	# Create a logfile
	script_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]

	setup_log(progName,LOGFILENAME)

	from logger_module import logger # Need to import after setup_logging is called
	logger.info(f'''{progName} -- {progVersion}''')

	from get_config import parse_config

	if not parse_config(CONFIGFILENAME,logger):
		print(f"Failed to load configuration from {CONFIGFILENAME}. Please ensure the file exists and is properly formatted.")
		force_quit(1)

	# Can now get config parameters
	from get_config import (UI, LOGGING, CAMERAS)

	'''
	for camera_name, source in CAMERAS.__dict__.items():
		print(f"Camera: {camera_name}, Source: {source}")
	'''

	# Set logging level
	logger = set_log_level(LOGGING.LEVEL,logger)

	this_ip_address = getIP(UI.PORT)


	# Start uvicorn in a background thread
	def run_server():
		uvicorn.run(
			app,
			host=this_ip_address,
			port=int(UI.PORT),
			reload=False,
			log_config=None
		)

	server_thread = threading.Thread(target=run_server, daemon=False)
	server_thread.start()

	logger.info("Waiting for server to be ready")
	time.sleep(2)

	with httpx.Client() as client:
		for camera_name, source in CAMERAS.__dict__.items():

			try:
				response = client.post(
					f"http://{this_ip_address}:{UI.PORT}/api/add-camera",
					json={
						"name": camera_name,
						"source": source,
					},
				)
				logger.info(f"Added {camera_name} from {source}")
			except Exception as e:
				logger.error(f"Error adding cameras: {e}")

	# Start all cameras after registration
	start_cameras()

	logger.info('-------------------------------------------------------\n')
	logger.info(f"View cameras at http://{this_ip_address}:8002\n")
	logger.info(f"Streaming url is http://{this_ip_address}:8002/<camera name>stream\n")
	logger.info(f"Snapshot url is http://{this_ip_address}:8002/<camera name>/snapshot\n")

	# Keep the main thread alive
	server_thread.join()
