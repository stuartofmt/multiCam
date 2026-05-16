from pathlib import Path

import cv2
import time
import uvicorn

from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from multi_camera import MultiCameraManager

# ============================================================
# Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

STATIC_DIR = BASE_DIR / "static"

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

manager.add_camera(
    name="cam0",
    source='/dev/video0',
    fps=5,
    width=320*4,
    height=240*4,
    api_preference=cv2.CAP_V4L2,
    copy_frame=False
)

manager.add_camera(
    name="cam2",
    source='/dev/video2',
    fps=5,
    width=320*4,
    height=240*4,
    api_preference=cv2.CAP_V4L2,
    copy_frame=False
)

#
# Start all cameras
#
manager.start()

# ============================================================
# Static Files
# ============================================================

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static"
)

# ============================================================
# Routes
# ============================================================

@app.get("/")
async def root():

    return FileResponse(
        STATIC_DIR / "index.html"
    )


@app.get("/api/cameras")
async def list_cameras():

    return {
        "cameras": list(manager.cameras.keys())
    }

# ============================================================
# MJPEG Streaming
# ============================================================

async def mjpeg_generator(
    request: Request,
    camera_name
):

    while True:

        #
        # Stop processing if browser disconnected
        #
        if await request.is_disconnected():

            print(
                f"Client disconnected: {camera_name}"
            )

            break

        start = time.perf_counter()

        frame = manager.get_frame(camera_name)

        if frame is None:

            time.sleep(0.1)
            continue

        #
        # Lower JPEG quality = lower CPU
        #
        success, encoded = cv2.imencode(
            ".jpg",
            frame,
            [
                int(cv2.IMWRITE_JPEG_QUALITY),
                JPEG_QUALITY
            ]
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

        #
        # Limit streaming FPS
        #
        elapsed = time.perf_counter() - start

        sleep_time = STREAM_INTERVAL - elapsed

        if sleep_time > 0:
            time.sleep(sleep_time)


@app.get("/streaming/{camera_name}")
async def stream_camera(
    request: Request,
    camera_name: str
):

    if camera_name not in manager.cameras:

        return {
            "error": f"Unknown camera '{camera_name}'"
        }

    return StreamingResponse(
        mjpeg_generator(
            request,
            camera_name
        ),
        media_type=(
            "multipart/x-mixed-replace;"
            " boundary=frame"
        )
    )

# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    print(f"Base directory : {BASE_DIR}")
    print(f"Static directory: {STATIC_DIR}")

    if not STATIC_DIR.exists():

        print("\nERROR: static directory does not exist")
        print(f"Expected location:\n{STATIC_DIR}")

        raise SystemExit(1)

    print("\nStreaming settings:")
    print(f"  STREAM_FPS   : {STREAM_FPS}")
    print(f"  JPEG_QUALITY : {JPEG_QUALITY}")

    print("\nStarting FastAPI server...")
    print("Open browser at:")
    print("http://localhost:8000\n")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False
    )