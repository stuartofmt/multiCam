import asyncio
import cv2
import time
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.requests import Request
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from defaults import (
    STATIC_DIR,
    DEFAULT_JPEG_QUALITY,
    DefaultCameraOptions,
)
from multi_camera import MultiCameraManager, is_valid_jpeg_bytes

# ============================================================
# Pydantic Models
# ============================================================


class CameraConfig(BaseModel):
    name: str
    source: str
    cameratype: str = "USB"
    fps: float = DefaultCameraOptions.fps.value
    width: Optional[int] = DefaultCameraOptions.width.value
    height: Optional[int] = DefaultCameraOptions.height.value
    api_preference: Optional[int] = None
    copy_frame: bool = False
    brightness: float = DefaultCameraOptions.brightness.value
    contrast: float = DefaultCameraOptions.contrast.value
    focus: float = DefaultCameraOptions.focus.value
    balance: float = DefaultCameraOptions.balance.value


# ============================================================
# Streaming Settings
# ============================================================

JPEG_QUALITY = DEFAULT_JPEG_QUALITY

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
    return {"cameras": list(manager.cameras.keys())}


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
            copy_frame=config.copy_frame,
            brightness=config.brightness,
            contrast=config.contrast,
            focus=config.focus,
            balance=config.balance,
            cameratype=config.cameratype,
        )
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
    camera = manager.cameras.get(camera_name)
    if camera is None:
        return

    fps = getattr(camera, "fps", DefaultCameraOptions.fps.value)
    stream_interval = 1.0 / fps if fps > 0 else 1.0 / DefaultCameraOptions.fps.value

    while True:
        if await request.is_disconnected():
            print(f"Client disconnected: {camera_name}")
            break

        start = time.perf_counter()
        frame = manager.get_frame(camera_name)

        if frame is None:
            await asyncio.sleep(0.1)
            continue

        success, encoded = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY],
        )

        if not success:
            continue

        jpg_bytes = encoded.tobytes()
        if not is_valid_jpeg_bytes(jpg_bytes):
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" +
            jpg_bytes +
            b"\r\n"
        )

        elapsed = time.perf_counter() - start
        sleep_time = stream_interval - elapsed

        if sleep_time > 0:
            await asyncio.sleep(sleep_time)


@app.get("/streaming/{camera_name}")
@app.get("/{camera_name}/stream")
async def stream_camera(request: Request, camera_name: str):
    if camera_name not in manager.cameras:
        return {"error": f"Unknown camera '{camera_name}'"}

    return StreamingResponse(
        mjpeg_generator(request, camera_name),
        media_type=(
            "multipart/x-mixed-replace;"
            " boundary=frame"
        ),
    )


@app.get("/{camera_name}/snapshot")
async def camera_snapshot(camera_name: str):
    if camera_name not in manager.cameras:
        return {"error": f"Unknown camera '{camera_name}'"}

    jpg_bytes = manager.get_jpeg(camera_name)
    if jpg_bytes is None or not is_valid_jpeg_bytes(jpg_bytes):
        return {"error": f"Camera '{camera_name}' has no valid captured frame"}

    return Response(content=jpg_bytes, media_type="image/jpeg")

