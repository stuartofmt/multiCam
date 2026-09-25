import asyncio
import time
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator
from starlette.requests import Request
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from defaults import (
    STATIC_DIR,
    DefaultCameraSettings,
)
from multi_camera import MultiCameraManager
import logger_module


# ============================================================
# Pydantic Models
# ============================================================


class CameraConfig(BaseModel):
    name: str
    source: str | int
    cameratype: str
    fps: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    api_preference: Optional[int] = None
    rotate: int = 0
    jpegresolution: int = 95
    format: Optional[str] = None
    # URL path segments: /<camera name>/<streamname> and /<camera name>/<snapshotname>
    streamname: str = DefaultCameraSettings.streamname.value
    snapshotname: str = DefaultCameraSettings.snapshotname.value
    # Picamera only: libcamera controls applied when the camera starts.
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


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        if request.url.path == "/" or request.url.path == "/index" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"

        return response


app.add_middleware(NoCacheStaticMiddleware)
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


@app.get("/api/cameras")
async def list_cameras():
    return {
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
