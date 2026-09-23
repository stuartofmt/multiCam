import cv2
import threading
import time

from multi_camera import ClientTracking


class Picamera2Stream(ClientTracking):
    def __init__(
        self,
        camera_index=0,
        fps=30.0,
        width=640,
        height=480,
        rotate=0,
        jpegresolution=95,
    ):
        self.camera_index = camera_index
        self.fps = fps
        self.width = width
        self.height = height
        self.rotate = rotate
        self.jpegresolution = jpegresolution
        self.picam2 = None
        self.thread = None
        self.running = False
        self.lock = threading.Lock()
        self.cached_jpeg = None
        self.timestamp = 0.0
        self._init_clients()

    def start(self):
        if self.running:
            return

        try:
            from picamera2 import Picamera2
            from libcamera import Transform

            width = int(self.width)
            height = int(self.height)
            self.fps = float(self.fps)
            frame_duration_us = int(1_000_000 / self.fps)

            # 180 degrees is done by the ISP at no CPU cost; 90/270 are not supported there.
            if self.rotate == 180:
                transform = Transform(hflip=1, vflip=1)
                self.rotate = 0
            else:
                transform = Transform()

            self.picam2 = Picamera2(camera_num=self.camera_index)
            # Picamera2 "RGB888" is stored B,G,R - already OpenCV's native order.
            configuration = self.picam2.create_video_configuration(
                main={"size": (width, height), "format": "RGB888"},
                transform=transform,
                controls={"FrameDurationLimits": (frame_duration_us, frame_duration_us)},
            )
            self.picam2.configure(configuration)

            self.picam2.start()
            self.running = True
            self.thread = threading.Thread(target=self._update, daemon=True)
            self.thread.start()
        except Exception as exc:
            self.picam2 = None
            self.running = False
            raise RuntimeError(f"Unable to open picamera source {self.camera_index}: {exc}") from exc

    def _apply_rotation(self, frame):
        if self.rotate == 90:
            return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        if self.rotate in (270, -90):
            return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return frame

    def _update(self):
        while self.running:
            # Picamera2 recycles unrequested buffers itself, so idling needs no draining.
            if not self._has_clients.wait(timeout=0.5):
                continue

            # Blocks until the sensor delivers the next frame at the configured rate.
            frame = self.picam2.capture_array()
            frame = self._apply_rotation(frame)

            success, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), int(self.jpegresolution)],
            )
            if success:
                jpg_bytes = encoded.tobytes()
                with self.lock:
                    self.cached_jpeg = jpg_bytes
                    self.timestamp = time.time()

    def get_jpeg(self):
        with self.lock:
            return self.cached_jpeg

    def get_jpeg_with_timestamp(self):
        with self.lock:
            return self.cached_jpeg, self.timestamp

    def stop(self):
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        if self.picam2 is not None:
            self.picam2.stop()
            self.picam2.close()
