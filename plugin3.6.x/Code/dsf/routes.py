from pathlib import Path

import asyncio
import cv2
import time

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.requests import Request

from config import (
    STATIC_DIR,
    DEFAULT_CAMERA_API_PREFERENCE,
    DEFAULT_CAMERA_COPY_FRAME,
    DEFAULT_CAMERA_FPS,
    DEFAULT_CAMERA_HEIGHT,
    DEFAULT_CAMERA_WIDTH,
    DEFAULT_CONTRAST,
    DEFAULT_BRIGHTNESS,
    DEFAULT_FOCUS,
    DEFAULT_BALANCE,
)
from multi_camera import MultiCameraManager
from typing import Optional

# ============================================================
# Pydantic Models
# ============================================================


class CameraConfig(BaseModel):
    name: str
    source: str
    fps: float = DEFAULT_CAMERA_FPS
    width: Optional[int] = DEFAULT_CAMERA_WIDTH
    height: Optional[int] = DEFAULT_CAMERA_HEIGHT
    api_preference: Optional[int] = DEFAULT_CAMERA_API_PREFERENCE
    copy_frame: bool = DEFAULT_CAMERA_COPY_FRAME
    brightness: float = DEFAULT_BRIGHTNESS
    contrast: float = DEFAULT_CONTRAST
    focus: float = DEFAULT_FOCUS
    balance: float = DEFAULT_BALANCE


# ============================================================
# Streaming Settings
# ============================================================

STREAM_FPS = 5
STREAM_INTERVAL = 1.0 / STREAM_FPS
JPEG_QUALITY = 95

# ============================================================
# FastAPI App
# ============================================================

app = FastAPI()

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

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" +
            jpg_bytes +
            b"\r\n"
        )

        elapsed = time.perf_counter() - start
        sleep_time = STREAM_INTERVAL - elapsed

        if sleep_time > 0:
            await asyncio.sleep(sleep_time)


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
    )
