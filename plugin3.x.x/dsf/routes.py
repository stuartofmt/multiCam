import asyncio
import logging
import os
import threading
import time
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

from defaults import (
    STATIC_DIR,
    DefaultCameraSettings,
    SETTING_LIMITS,
)
from multi_camera import MultiCameraManager
import logger_module
from settings_config import (
    LOG_LEVELS,
    read_camera_config,
    read_log_level,
    read_port,
    validate_port,
    settings_schema,
    validate_cameras,
    write_camera_config,
)


# ============================================================
# Pydantic Models
# ============================================================


class CameraConfig(BaseModel):
    name: str
    source: str | int
    cameratype: str
    fps: Optional[float] = None
    # USB only: rate requested from the device, when it differs from the served fps.
    capturefps: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    api_preference: Optional[int] = None
    rotate: int = 0
    jpegresolution: int = 95
    format: Optional[str] = None
    # URL path segments: /<camera name>/<streamname> and /<camera name>/<snapshotname>
    streamname: str = DefaultCameraSettings.streamname.value
    snapshotname: str = DefaultCameraSettings.snapshotname.value
    # Controls applied when the camera starts: libcamera ones for a Pi camera, OpenCV ones for a USB camera on Windows.
    controls: Optional[dict] = None

    @model_validator(mode="after")
    def validate_camera_type_settings(self):
        camera_type = self.cameratype.upper()
        if camera_type in {"USB", "PICAMERA"}:
            missing = [
                field for field in ("fps", "width", "height")
                if getattr(self, field) is None
            ]
            if missing:
                raise ValueError(
                    f"{camera_type} cameras require: {', '.join(missing)}"
                )
        elif camera_type == "STREAM":
            self.fps = self.fps or DefaultCameraSettings.fps.value
            self.width = None
            self.height = None
        else:
            raise ValueError(f"Unsupported camera type: {self.cameratype}")
        return self


# ============================================================
# Streaming Settings
# ============================================================

SNAPSHOT_TIMEOUT_SEC = 3.0

# ============================================================
# FastAPI App
# ============================================================

app = FastAPI()


# Added before CORSMiddleware below, so (as before) CORS stays the outermost middleware
@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)

    if request.url.path.startswith("/static/assets/"):
        # Built UI files have a content hash in their name, so they never change.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.url.path in ("/", "/index", "/settings", "/log") or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"

    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# Camera Manager
# ============================================================

manager = MultiCameraManager()

# camera name -> {"stream": streamname, "snapshot": snapshotname}
camera_urls = {}


def start_cameras():
    manager.start()
    # The cameras are running: the settings page waits for this after a restart
    settings_state["cameras_started"] = True


def stop_cameras():
    """Stop every camera, releasing its device (e.g. before multiCam restarts itself)."""
    manager.stop()


# When this run of multiCam started: the settings page sees it change once a restart has finished.
STARTED_AT = time.time()
# Restarts multiCam (set by multiCam.py, see set_restart_handler); None until then.
_restart_handler = None
# Seconds between answering /api/restart and restarting, so the answer reaches the page first.
RESTART_DELAY_SEC = 0.5


def set_restart_handler(handler):
    global _restart_handler
    _restart_handler = handler


# Ends multiCam (set by multiCam.py, see set_terminate_handler); None until then.
_terminate_handler = None
# DSF installs plugins here: as the plugin, DWC's plugin manager starts and stops multiCam - no Terminate button
DSF_PLUGIN_DIR = "/opt/dsf/plugins/"


def set_terminate_handler(handler):
    global _terminate_handler
    _terminate_handler = handler


def running_as_plugin():
    """True if DSF installed this copy of multiCam as its plugin."""
    return os.path.realpath(__file__).startswith(DSF_PLUGIN_DIR)


# Filled in by set_settings_state once the cameras are configured at startup.
settings_state = {
    "config_file": None,
    # What each detected camera supports (see get_config.get_device_capabilities)
    "capabilities": {"USB": {}, "PICAMERA": {}},
    # Values each camera runs with (see get_config.get_effective_settings)
    "effective": {},
    # Port the web interface is running on
    "port": None,
    # The config file was saved since startup, so the running cameras don't match it.
    "restart_required": False,
    # start_cameras has run: the cameras are added and started (the web server answers a little before that)
    "cameras_started": False,
}


def set_settings_state(config_file, capabilities, effective, port):
    settings_state["config_file"] = config_file
    settings_state["port"] = port
    settings_state["capabilities"] = capabilities
    settings_state["effective"] = effective


app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)

# ============================================================
# Routes
# ============================================================


@app.get("/")
@app.get("/index")
async def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/settings")
async def settings_page():
    return FileResponse(STATIC_DIR / "settings.html")


@app.get("/log")
async def log_page():
    return FileResponse(STATIC_DIR / "log.html")


# The most of the logfile sent at once: the log page starts with the end of a long log, then follows it
LOG_CHUNK_BYTES = 512 * 1024


def current_logfile():
    """The file the log is being written to: the logfile, or the startup log until multiCam has switched to it
    (or if it could not). None if there is neither."""
    logger = getattr(logger_module, "logger", None)
    files = [h.baseFilename for h in getattr(logger, "handlers", []) if isinstance(h, logging.FileHandler)]
    return files[-1] if files else None


@app.get("/api/log")
async def get_log(offset: Optional[int] = None):
    """
    The log's text from byte offset on (at most LOG_CHUNK_BYTES), with the offset to ask for next time. With no
    offset, the end of the log. An offset past the end means the log was started again (multiCam restarted): it
    is then sent from the beginning, with "restarted": true.
    """
    path = current_logfile()
    if path is None or not os.path.isfile(path):
        return JSONResponse({"status": "error", "message": "There is no logfile yet"}, status_code=404)
    size = os.path.getsize(path)
    restarted = offset is not None and offset > size
    if offset is None:
        start = max(0, size - LOG_CHUNK_BYTES)
    elif restarted:
        start = 0
    else:
        start = max(0, offset)
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(LOG_CHUNK_BYTES)
    if len(data) == LOG_CHUNK_BYTES and b"\n" in data:
        # A full chunk: up to its last whole line, the rest next time
        data = data[:data.rfind(b"\n") + 1]
    end = start + len(data)   # where the next request carries on
    if offset is None and start > 0:
        # Started part way through: from the next whole line
        newline = data.find(b"\n")
        data = data[newline + 1:] if newline >= 0 else b""
    return {
        "status": "success",
        "path": path,
        "size": size,
        "offset": end,
        "truncated": offset is None and start > 0,   # the beginning of the log is not shown
        "restarted": restarted,
        "text": data.decode("utf-8", errors="replace"),
    }


@app.get("/api/log/download")
async def download_log():
    """The whole logfile, as a file to save."""
    path = current_logfile()
    if path is None or not os.path.isfile(path):
        return JSONResponse({"status": "error", "message": "There is no logfile yet"}, status_code=404)
    return FileResponse(path, media_type="text/plain", filename=os.path.basename(path))


@app.get("/api/settings")
async def get_settings():
    config_file = settings_state["config_file"]
    if config_file is None:
        return JSONResponse({"status": "error", "message": "Settings are not available yet"}, status_code=503)
    try:
        cameras = read_camera_config(config_file)
        log_level = read_log_level(config_file)
        port = read_port(config_file)
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"Could not read {config_file}: {e}"}, status_code=500)
    return {
        "status": "success",
        "config_file": str(config_file),
        "cameras": cameras,
        "log_level": log_level,
        # port as set in the file (0 = pick a free port) and the port actually in use
        "port": port,
        "port_in_use": settings_state["port"],
        "capabilities": settings_state["capabilities"],
        # Only cameras that were added and are running have effective values.
        "effective": {
            name: values for name, values in settings_state["effective"].items()
            if name in manager.cameras
        },
        "schema": settings_schema(),
        "setting_limits": SETTING_LIMITS,
        "restart_required": settings_state["restart_required"],
        "started_at": STARTED_AT,
        "cameras_started": settings_state["cameras_started"],
        # As the plugin there is no Terminate button - DWC's plugin manager stops it
        "plugin": running_as_plugin(),
    }


class SettingsCamera(BaseModel):
    name: str
    cameratype: str
    source: str
    values: dict[str, str | int | float | None] = {}


class SettingsRequest(BaseModel):
    cameras: list[SettingsCamera]
    # None leaves the log level unchanged.
    log_level: Optional[str] = None
    # None leaves the port unchanged; 0 picks a free port.
    port: Optional[int] = None


@app.post("/api/settings")
async def save_settings(request: SettingsRequest):
    config_file = settings_state["config_file"]
    if config_file is None:
        return JSONResponse({"status": "error", "message": "Settings are not available yet"}, status_code=503)

    cameras, errors = validate_cameras([camera.model_dump() for camera in request.cameras])
    log_level = request.log_level.upper() if request.log_level else None
    if log_level is not None and log_level not in LOG_LEVELS:
        errors.append(f"Log level must be one of {', '.join(LOG_LEVELS)}")
    if request.port is not None:
        port_error = validate_port(request.port)
        if port_error:
            errors.append(port_error)
    if errors:
        return JSONResponse({"status": "error", "errors": errors}, status_code=400)

    try:
        write_camera_config(config_file, cameras, log_level, request.port)
    except Exception as e:
        logger_module.logger.error(f"Could not save {config_file}: {e}")
        return JSONResponse({"status": "error", "message": f"Could not save {config_file}: {e}"}, status_code=500)

    settings_state["restart_required"] = True
    logger_module.logger.info(f"Camera settings saved to {config_file}")
    return {"status": "success"}


@app.post("/api/restart")
async def restart():
    """
    Restart multiCam to apply saved settings. Answers first, with the port the new run will listen on (the
    configured one, else the one in use now - with port 0 a free one is picked again, normally the same), then
    restarts.
    """
    if _restart_handler is None:
        return JSONResponse({"status": "error", "message": "Restarting is not available yet"}, status_code=503)
    port = settings_state["port"]
    try:
        port = read_port(settings_state["config_file"]) or port
    except Exception:
        pass
    logger_module.logger.info("Restart requested from the settings page")
    threading.Timer(RESTART_DELAY_SEC, _restart_handler).start()
    return {"status": "success", "port": port}


@app.post("/api/terminate")
async def terminate():
    """End multiCam (the Terminate button) - not as the plugin, which DWC's plugin manager starts and stops."""
    if running_as_plugin():
        return JSONResponse({"status": "error", "message": "Running as a plugin - stop it from DWC's plugin manager"},
                            status_code=403)
    if _terminate_handler is None:
        return JSONResponse({"status": "error", "message": "Terminating is not available yet"}, status_code=503)
    logger_module.logger.info("Terminate requested from the settings page")
    threading.Timer(RESTART_DELAY_SEC, _terminate_handler).start()  # after this answer reaches the page
    return {"status": "success", "message": "multiCam is stopping"}


@app.get("/api/cameras")
async def list_cameras():
    return {
        # Changes when multiCam restarts: the camera streams page then reconnects
        "started_at": STARTED_AT,
        "cameras": [
            {"name": name, **camera_urls[name]}
            for name in manager.cameras
        ]
    }


@app.post("/api/add-camera")
async def api_add_camera(config: CameraConfig):
    try:
        manager.add_camera(
            name=config.name,
            source=config.source,
            fps=config.fps,
            capturefps=config.capturefps,
            width=config.width,
            height=config.height,
            api_preference=config.api_preference,
            rotate=config.rotate,
            jpegresolution=config.jpegresolution,
            cameratype=config.cameratype,
            format=config.format,
            controls=config.controls,
        )
        camera_urls[config.name] = {
            "stream": config.streamname,
            "snapshot": config.snapshotname,
        }
        return {"status": "success", "name": config.name}
    except Exception as e:
        return {"status": "error", "message": str(e)}


class StartCameraRequest(BaseModel):
    name: str


@app.post("/api/start-camera")
async def api_start_camera(request: StartCameraRequest):
    try:
        manager.start_camera(request.name)
        return {"status": "success", "name": request.name}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ============================================================
# MJPEG Streaming
# ============================================================

async def mjpeg_generator(request: Request, camera_name: str):
    if camera_name not in manager.cameras:
        return

    frame_count = 0
    last_timestamp = None

    # Capture threads only encode while at least one client is registered.
    manager.add_client(camera_name)
    logger_module.logger.debug(f"Client connected: {camera_name}")
    try:
        while True:
            if await request.is_disconnected():
                logger_module.logger.debug(f"Client disconnected: {camera_name}")
                break

            try:
                # Capture threads already limit output to the camera fps; only send new frames.
                jpg_bytes, timestamp = manager.get_jpeg_with_timestamp(camera_name)

                if jpg_bytes is None or timestamp == last_timestamp:
                    await asyncio.sleep(0.01)
                    continue
                last_timestamp = timestamp

                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpg_bytes)).encode() + b"\r\n\r\n" +
                    jpg_bytes +
                    b"\r\n"
                )

                frame_count += 1
                if frame_count % 10000 == 0:  # periodic output
                    logger_module.logger.debug(f"[{camera_name}] Streamed {frame_count} frames")
            except Exception as e:
                logger_module.logger.error(f"Error in mjpeg_generator for {camera_name}: {e}")
                break
    finally:
        manager.remove_client(camera_name)


@app.get("/streaming/{camera_name}")
async def stream_camera(request: Request, camera_name: str):
    if camera_name not in manager.cameras:
        return {"error": f"Unknown camera '{camera_name}'"}

    return StreamingResponse(
        mjpeg_generator(request, camera_name),
        media_type=(
            "multipart/x-mixed-replace;"
            " boundary=frame"
        ),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


async def camera_snapshot(camera_name: str):
    # The cached JPEG may be stale if nobody is streaming, so request a fresh one.
    requested_at = time.time()
    deadline = time.monotonic() + SNAPSHOT_TIMEOUT_SEC
    manager.add_client(camera_name)
    try:
        while True:
            jpg_bytes, timestamp = manager.get_jpeg_with_timestamp(camera_name)
            if jpg_bytes is not None and timestamp >= requested_at:
                break
            if time.monotonic() > deadline:
                break
            await asyncio.sleep(0.02)
    finally:
        manager.remove_client(camera_name)

    if jpg_bytes is None:
        return {"error": f"Camera '{camera_name}' has no valid captured frame"}

    return Response(content=jpg_bytes, media_type="image/jpeg")


# Declared last so fixed paths such as /api/cameras and /streaming/<camera> match first.
@app.get("/{camera_name}/{endpoint}")
async def camera_endpoint(request: Request, camera_name: str, endpoint: str):
    if camera_name not in manager.cameras:
        return {"error": f"Unknown camera '{camera_name}'"}

    urls = camera_urls[camera_name]
    if endpoint == urls["stream"]:
        return await stream_camera(request, camera_name)
    if endpoint == urls["snapshot"]:
        return await camera_snapshot(camera_name)
    return {"error": f"Unknown endpoint '{endpoint}' for camera '{camera_name}'"}
