"""
multi_camera.py

Background multi-camera capture manager using OpenCV.
Optimized for Linux webcam streaming.
"""

import cv2
import time
import threading

from typing import Dict, Optional


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
    ):

        self.source = source

        self.fps = fps
        self.frame_interval = 1.0 / fps

        self.width = width
        self.height = height

        self.api_preference = api_preference

        self.copy_frame = copy_frame

        self.capture = None
        self.thread = None

        self.running = False

        self.lock = threading.Lock()

        self.frame = None
        self.timestamp = 0.0

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

            grabbed, frame = self.capture.read()

            if not grabbed:
                continue

            with self.lock:

                self.frame = frame
                self.timestamp = time.time()

    def get_frame(self):
        """
        Return latest frame.
        """

        with self.lock:

            if self.frame is None:
                return None

            if self.copy_frame:
                return self.frame.copy()

            return self.frame

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