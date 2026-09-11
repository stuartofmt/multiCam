"""
The she-bang is not required if called with fully qualified paths
The plugin manager does this.  Otherwise use ...
Standard python install e.g.
#!/usr/bin/python3 -u
Venv python install e.g.
#! <path-to-virtual-environment/bin>python -u
"""

#This is to supress the noisy libcam  MUST BE AT THE VERY START OF THE SCRIPT
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

import subprocess
import re

import glob


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

def list_pi_cameras():
	# --- CSI cameras via picamera2 ---
	from picamera2 import Picamera2

	pi_cameras = []
	try:
		cameras = Picamera2.global_camera_info()
		for index, cam_info in enumerate(cameras):
			if 'usb' in cam_info['Id'].lower():  # Ignore USB Cameras in this section
				continue  # Skip
			pi_cameras.append((index))

		camera_results = [f"\n", "-" * 95]
		if pi_cameras:
			for index in pi_cameras:
				camera_results.append(f"PiCamera found with Index: {index}")
			camera_results.append("\n")
			logger.info("\n".join(camera_results))
		else:
			camera_results.append("No Pi cameras found.")
			logger.info("\n".join(camera_results))

	except Exception as e:
		logger.critical(f"Error listing Pi cameras")
		logger.critical(f"{e}")
		force_quit(1)

def list_usb_cameras():
	"""
	Return a list of /dev/videoN paths for USB cameras that actually
	support video capture (filters out metadata-only nodes by
	checking reported capabilities, not by even/odd guessing).
	"""
	try:
		video_nodes = sorted(glob.glob('/dev/video*'),
							key=lambda x: int(re.search(r'\d+', x).group()))
	except Exception as e:
		logger.critical(f"Error listing /dev/video* nodes")
		logger.critical(f"{e}")
		force_quit(1)

	usb_devices = []
	try:
		for node in video_nodes:
			result = subprocess.run(
					['v4l2-ctl', '-d', node, '--info'],
					capture_output=True, text=True, timeout=2
				)

			if result.returncode != 0:
				continue

			out = result.stdout

			is_usb = 'usb' in out.lower()
			# "Device Caps" lists the node's own capabilities; some nodes
			# only show "Video Capture" under "All Caps" (i.e. the driver
			# supports it) without exposing it on this particular node —
			# we want it listed under Device Caps to be usable.
			device_caps_section = out.split('Device Caps')[-1] if 'Device Caps' in out else ''
			supports_capture = 'Video Capture' in device_caps_section

			if is_usb and supports_capture:
				usb_devices.append(node)
	except Exception as e:
		logger.critical(f"Error checking USB camera capabilities")
		logger.critical(f"{e}")
		force_quit(1)

	camera_results = [f"\n", "-" * 95]

	if usb_devices:
		# --- USB cameras via /dev/video* (capability-checked) ---
		camera_results.append("USB camera devices (capture-capable):")
		for dev in usb_devices:
			camera_results.append(f"  {dev}")
		logger.info("\n".join(camera_results))
	else:
		camera_results.append("No USB cameras found.")
		logger.info("\n".join(camera_results))	


if __name__ == "__main__":

	# shutdown with SIGINT (kill -2 <pid> or SIGTERM)
	signal.signal(signal.SIGINT, sig_handler)
	signal.signal(signal.SIGTERM, sig_handler)

	global progName, progVersion
	progName = os.path.splitext(os.path.basename(sys.argv[0]))[0]
	progVersion = '0.0.1'

	CONFIGFILENAME = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "config.ini"
	LOGFILENAME = CONFIGFILENAME.parent / "multiCam.log"

	# Create a logfile
	script_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]

	if not setup_log(progName,LOGFILENAME):
		logger.error(f"Failed to setup logging to {LOGFILENAME}. Please ensure the file is writable.")
		sys.exit(1)

	from logger_module import logger # Need to import after setup_logging is called
	logger.info(f'''Log file for {progName} -- {progVersion}''')
	list_usb_cameras()

	from get_config import parse_config

	if not parse_config(CONFIGFILENAME,logger):
		logger.error(f"Failed to load configuration from {CONFIGFILENAME}. Please ensure the file exists and is properly formatted.")
		force_quit(1)

	# Can now get config parameters
	from get_config import (UI, LOGGING, CAMERAS, PICAMERAS)
	if PICAMERAS.__dict__:
		list_pi_cameras()

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

	server_thread = threading.Thread(target=run_server, daemon=True)
	server_thread.start()

	logger.info("Waiting for server to be ready")
	time.sleep(2)

	with httpx.Client() as client:
		for camera_name, source in PICAMERAS.__dict__.items():
			try:
				response = client.post(
					f"http://{this_ip_address}:{UI.PORT}/api/add-camera",
					json={
						"name": camera_name,
						"source": source,
						"cameratype": "picamera",
					},
				)
				if response.is_success and response.json().get("status") == "success":
					logger.info(f"Added {camera_name} with index '{source}'")
				else:
					logger.error(f"Error adding PiCamera {camera_name}: {response.text}")
			except Exception as e:
				logger.error(f"Error adding PiCamera {camera_name}: {e}")

		for camera_name, source in CAMERAS.__dict__.items():

			try:
				response = client.post(
					f"http://{this_ip_address}:{UI.PORT}/api/add-camera",
					json={
						"name": camera_name,
						"source": source,
						"cameratype": "USB",
					},
				)
				if response.is_success and response.json().get("status") == "success":
					logger.info(f"Added {camera_name} from '{source}'")
				else:
					logger.error(f"Error adding camera {camera_name}: {response.text}")
			except Exception as e:
				logger.error(f"Error adding cameras: {e}")

	# Start all cameras after registration
	try:
		start_cameras()
	except Exception as e:
		logger.critical(f"{e}")
		force_quit(1)

	logger.info('-------------------------------------------------------\n')
	logger.info(f"View cameras at http://{this_ip_address}:{UI.PORT}\n")
	logger.info(f"Streaming url is http://{this_ip_address}:{UI.PORT}/<camera name>/stream\n")
	logger.info(f"Snapshot url is http://{this_ip_address}:{UI.PORT}/<camera name>/snapshot\n")

	# Keep the main thread alive
	server_thread.join()
