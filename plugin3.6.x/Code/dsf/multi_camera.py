"""
multi_camera.py

Background multi-camera capture manager using OpenCV.
Optimized for Linux webcam streaming.
"""

import cv2
import time
import threading

from typing import Dict, Optional
from config import DEFAULT_JPEG_QUALITY


class CameraStream:
    """
    Single background camera stream.
    """

    def __init__(
        self,
        source,
        fps: float = 30.0,
        width: Optional[int] = None,
        height: Optional[int] = None,
        api_preference=None,
        copy_frame: bool = False,
        brightness: float = 1.0,
        contrast: float = 1.0,
        focus: float = 1.0,
        balance: float = 1.0,
    ):

        self.source = source

        self.fps = fps
        self.frame_interval = 1.0 / fps

        self.width = width
        self.height = height

        self.api_preference = api_preference

        self.copy_frame = copy_frame

        self.brightness = brightness
        self.contrast = contrast
        self.focus = focus
        self.balance = balance

        self.capture: Optional[cv2.VideoCapture] = None
        self.thread: Optional[threading.Thread] = None

        self.running = False

        self.lock = threading.Lock()

        self.frame = None
        self.timestamp = 0.0
        self.cached_jpeg: Optional[bytes] = None

    def start(self):
        """
        Start background capture thread.
        """

        if self.running:
            return

        print(
            f"Opening "
            f"{self.source} "
            f"using V4L2"
        )

        #
        # Open webcam
        #
        self.capture = cv2.VideoCapture(
            self.source,
            self.api_preference if self.api_preference else cv2.CAP_V4L2
        )

        if not self.capture.isOpened():

            raise RuntimeError(
                f"Unable to open camera source: "
                f"{self.source}"
            )

        #
        # Force MJPEG mode
        #
        self.capture.set(
            cv2.CAP_PROP_FOURCC,
            cv2.VideoWriter.fourcc(*'MJPG')
        )

        #
        # Resolution
        #
        if self.width:

            self.capture.set(
                cv2.CAP_PROP_FRAME_WIDTH,
                self.width
            )

        if self.height:

            self.capture.set(
                cv2.CAP_PROP_FRAME_HEIGHT,
                self.height
            )

        #
        # FPS
        #
        self.capture.set(
            cv2.CAP_PROP_FPS,
            self.fps
        )

        #
        # Test frame capture
        #
        ok, frame = self.capture.read()

        if not ok or frame is None:

            self.capture.release()

            raise RuntimeError(
                f"Camera opened but "
                f"frame capture failed: "
                f"{self.source}"
            )

        print("Camera opened successfully")

        self.running = True

        self.thread = threading.Thread(
            target=self._update,
            daemon=True
        )

        self.thread.start()

    def _update(self):
        """
        Background frame capture loop.
        """

        next_frame_time = time.perf_counter()

        while self.running:

            current_time = time.perf_counter()

            #
            # FPS limiting
            #
            if current_time < next_frame_time:

                time.sleep(
                    next_frame_time - current_time
                )

            #
            # Prevent timing drift
            #
            next_frame_time = (
                current_time + self.frame_interval
            )

            if self.capture is None:
                raise RuntimeError(
                    "Camera capture is not initialized"
                )

            grabbed, frame = self.capture.read()

            if not grabbed:
                continue

            with self.lock:
                self.frame = frame
                self.timestamp = time.time()
                local_frame = self.frame.copy() if self.copy_frame else self.frame

            # Encode JPEG in background thread to cache latest JPEG bytes
            try:
                success, encoded = cv2.imencode(
                    ".jpg",
                    local_frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), DEFAULT_JPEG_QUALITY],
                )

                if success:
                    jpg_bytes = encoded.tobytes()
                    with self.lock:
                        self.cached_jpeg = jpg_bytes
            except Exception:
                # Encoding failures should not stop the capture loop
                pass


    def get_frame(self):
        """
        Return latest frame.
        """

        with self.lock:

            if self.frame is None:
                return None

            frame = self.frame.copy() if self.copy_frame else self.frame

            # Apply image adjustments
            if self.contrast != 1.0:
                frame = cv2.convertScaleAbs(frame, alpha=self.contrast, beta=0)

            if self.brightness != 1.0:
                frame = cv2.convertScaleAbs(frame, alpha=1.0, beta=(self.brightness - 1.0) * 50)

            return frame

    def get_jpeg(self):
        """
        Return cached JPEG bytes for the latest frame.
        """

        with self.lock:
            return self.cached_jpeg

    def get_frame_with_timestamp(self):
        """
        Return frame + timestamp.
        """

        with self.lock:

            if self.frame is None:
                return None, None

            if self.copy_frame:
                return self.frame.copy(), self.timestamp

            return self.frame, self.timestamp

    def stop(self):
        """
        Stop background capture.
        """

        self.running = False

        if self.thread is not None:

            self.thread.join(timeout=2.0)

        if self.capture is not None:

            self.capture.release()


class MultiCameraManager:
    """
    Multi-camera manager.
    """

    def __init__(self):

        self.cameras: Dict[
            str,
            CameraStream
        ] = {}

    def add_camera(
        self,
        name: str,
        source,
        fps: float = 30.0,
        width: Optional[int] = None,
        height: Optional[int] = None,
        api_preference=None,
        copy_frame: bool = False,
        brightness: float = 1.0,
        contrast: float = 1.0,
        focus: float = 1.0,
        balance: float = 1.0,
    ):

        if name in self.cameras:

            raise ValueError(
                f"Camera '{name}' already exists"
            )

        self.cameras[name] = CameraStream(
            source=source,
            fps=fps,
            width=width,
            height=height,
            api_preference=api_preference,
            copy_frame=copy_frame,
            brightness=brightness,
            contrast=contrast,
            focus=focus,
            balance=balance,
        )

    def start(self):
        """
        Start all cameras.
        """

        for cam in self.cameras.values():

            cam.start()

    def start_camera(self, name: str):
        """
        Start one camera.
        """

        self.cameras[name].start()

    def get_frame(self, name: str):
        """
        Retrieve latest frame.
        """

        if name not in self.cameras:

            raise KeyError(
                f"Unknown camera '{name}'"
            )

        return self.cameras[name].get_frame()

    def get_jpeg(self, name: str):
        """
        Retrieve latest cached JPEG bytes for a camera.
        """

        if name not in self.cameras:

            raise KeyError(
                f"Unknown camera '{name}'"
            )

        return self.cameras[name].get_jpeg()

    def get_frame_with_timestamp(self, name: str):
        """
        Retrieve frame + timestamp.
        """

        if name not in self.cameras:

            raise KeyError(
                f"Unknown camera '{name}'"
            )

        return self.cameras[name].get_frame_with_timestamp()

    def stop_camera(self, name: str):
        """
        Stop one camera.
        """

        if name in self.cameras:

            self.cameras[name].stop()

    def stop(self):
        """
        Stop all cameras.
        """

        for cam in self.cameras.values():

            cam.stop()