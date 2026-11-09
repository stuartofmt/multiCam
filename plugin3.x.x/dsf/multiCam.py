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
# OpenCV warns about every camera number that does not open while Windows' cameras are looked for
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

from pathlib import Path
import sys

import httpx
import threading
import time
import uvicorn
import socket
import signal
import subprocess
import logging

from routes import app, start_cameras, stop_cameras, set_settings_state, set_restart_handler, set_terminate_handler
from camera_platform import IS_WINDOWS
from settings_config import ensure_settings_file

from logger_module import (setup_console_logger, setup_log, set_log_level)


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


def restart_program():
    """
    Restart multiCam with the same command line, to apply saved settings (from the settings page). The cameras are
    stopped first, so the new run can open them; the web server's socket closes with this process.
    Linux: the process is replaced (os.execv), keeping its process id - DSF, which started the plugin, sees no change.
    Windows: os.execv does not quote arguments (a path with a space breaks), so a new process is started and this
    one ends; it keeps the console window.
    """
    logger.info('Restarting multiCam to apply the settings')
    try:
        stop_cameras()
    except Exception as e:
        logger.warning(f'Could not stop every camera before restarting: {e}')
    logging.shutdown()   # flush the log file
    command = [sys.executable] + sys.orig_argv[1:]   # the interpreter's own options (e.g. -u) too
    if IS_WINDOWS:
        subprocess.Popen(command)
        os._exit(0)
    os.execv(sys.executable, command)


def terminate_program():
    """End multiCam (the Terminate button on the settings page): stop the cameras, releasing their devices, then exit.
    The web server runs in a daemon thread, so it ends with the process."""
    logger.info('Terminating multiCam')
    try:
        stop_cameras()
    except Exception as e:
        logger.warning(f'Could not stop every camera before terminating: {e}')
    logging.shutdown()   # flush the log file
    os._exit(0)


def force_quit(code):
    logger.critical(f'''Terminating the program with exit code {code}''')
    sys.exit(code)


def sig_handler(signum, frame):
    signame = signal.Signals(signum).name
    logger.info(f'Shutting down.  Recieved signal {signame} ({signum})')
    force_quit(0)


_folder_notes = []  # What default_folder found out, logged once the log is set up


def packaged_python():
    """True on Windows if this Python runs as a packaged app (e.g. from the Microsoft Store, started through
    ...\\WindowsApps\\python.exe).  Windows then quietly redirects files it writes under AppData to a private folder of
    the package, which Explorer and other programs do not see."""
    if os.name != 'nt':
        return False
    try:
        import ctypes
        length = ctypes.c_uint32(0)
        # 15700 (APPMODEL_ERROR_NO_PACKAGE) = not packaged; 122 (ERROR_INSUFFICIENT_BUFFER) = packaged
        return ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None) != 15700
    except (AttributeError, OSError):
        return False


def folder_writable(folder):
    try:
        test = folder / '.multiCam-write-test'
        test.write_text('')
        test.unlink()
        return True
    except OSError:
        return False


def default_folder():
    """The folder for the log and settings.config when no logfile is given: this program's folder.  If it can not be
    written to (e.g. in Program Files) on Windows: the user's application data - or Documents\\multiCam with a
    packaged Python (Microsoft Store), which would hide files in application data."""
    folder = Path(__file__).parent
    if os.name != 'nt' or folder_writable(folder):
        return folder
    if packaged_python() or not os.environ.get('APPDATA'):
        folder = Path.home() / 'Documents' / 'multiCam'
        _folder_notes.append(f'The program folder can not be written to, and this Python is a packaged app (e.g. from the '
                             f'Microsoft Store), which hides files in AppData from other programs - so {folder} is used')
    else:
        folder = Path(os.environ['APPDATA']) / 'multiCam'
        _folder_notes.append(f'The program folder can not be written to - so {folder} is used')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


if __name__ == "__main__":

    # shutdown with SIGINT (kill -2 <pid> or SIGTERM)
    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    global progName, progVersion
    progName = os.path.splitext(os.path.basename(sys.argv[0]))[0]
    progVersion = '1.0.0'

    # The console and <program name>.startup.log until the startup checks are done - then the logfile
    logger = setup_console_logger(progName)
    logger.info(f'''{progName} -- {progVersion}''')

    LOGFILENAME = Path(sys.argv[1]) if len(sys.argv) > 1 else default_folder() / "multiCam.log"
    #  The settings file is kept in the same folder as the logfile
    SETTINGSFILENAME = LOGFILENAME.parent / 'settings.config'
    logger.info(f'Settings and log folder: {LOGFILENAME.parent}')
    for note in _folder_notes:
        logger.info(note)
    #  Create the settings file (and folder), or replace it if it fails the checksum
    try:
        settings_message = ensure_settings_file(SETTINGSFILENAME)
    except Exception as e:
        logger.critical(f'Could not create settings file {SETTINGSFILENAME}: {e}')
        force_quit(1)

    if settings_message:
        logger.info(settings_message)

    from get_config import (get_port, parse_config, get_installed_cameras, configure_cameras,
                            get_device_capabilities, get_effective_settings)

    try:
        # Get port from settings file
        PORT = get_port(SETTINGSFILENAME)
        logger.info(f'Port from the settings file: {PORT}')
        this_ip_address, PORT = validate_port(PORT)
    except Exception as e:
        logger.info(f'{e}')
        force_quit(1)

    try:
        # Get log level and cameras from settings file
        LOGLEVEL, cameras_to_use, cameras_to_use_configs = parse_config(SETTINGSFILENAME, logger)
    except Exception as e:
        logger.info(f'{e}')
        force_quit(1)

    # Set logging level
    logger = set_log_level(LOGLEVEL, logger)

    # Set camera options to default values and override only if configured
    try:
        installed_cameras = get_installed_cameras()
        configured_cameras = configure_cameras(installed_cameras, cameras_to_use, cameras_to_use_configs)
    except Exception as e:
        logger.info(f'{e}')
        force_quit(1)

    # The startup checks are done: from here on, the logfile (which starts with the startup log)
    if not setup_log(progName, LOGFILENAME):
        logger.error(f"Failed to setup logging to {LOGFILENAME}. Please ensure the file is writable.")
        force_quit(1)

    # The settings page can restart multiCam to apply saved settings
    set_restart_handler(restart_program)
    # ... and end it (its Terminate button, when not the plugin)
    set_terminate_handler(terminate_program)

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
