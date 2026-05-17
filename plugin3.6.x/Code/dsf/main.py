from pathlib import Path

import httpx
import threading
import time
import uvicorn

from config import STATIC_DIR
from routes import app, start_cameras

if __name__ == "__main__":
    print(f"Static directory: {STATIC_DIR}")

    if not STATIC_DIR.exists():
        print("\nERROR: static directory does not exist")
        print(f"Expected location:\n{STATIC_DIR}")
        raise SystemExit(1)

    # Start uvicorn in a background thread
    def run_server():
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8000,
            reload=False,
            log_level="warning",
        )

    server_thread = threading.Thread(target=run_server, daemon=False)
    server_thread.start()

    # Wait for server to be ready
    time.sleep(2)

    # Add cameras via HTTP API
    with httpx.Client() as client:
        try:
            response = client.post(
                "http://localhost:8000/api/add-camera",
                json={
                    "name": "cam0",
                    "source": "/dev/video0",
                },
            )
            print(f"Added cam0: {response.json()}")

            response = client.post(
                "http://localhost:8000/api/add-camera",
                json={
                    "name": "cam2",
                    "source": "/dev/video2",
                    "brightness": 2.0,
                },
            )
            print(f"Added cam2: {response.json()}")
        except Exception as e:
            print(f"Error adding cameras: {e}")

    # Start all cameras after registration
    start_cameras()

    print("\nStreaming server is running.")
    print("Open browser at:")
    print("http://localhost:8000\n")

    # Keep the main thread alive
    server_thread.join()
