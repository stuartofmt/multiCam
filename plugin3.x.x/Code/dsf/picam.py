import cv2
import threading
import time

from multi_camera import is_valid_jpeg_bytes


class Picamera2Stream:
    def __init__(
        self,
        camera_index=0,
        fps=30.0,
        width=640,
        height=480,
        copy_frame=False,
        brightness=1.0,
        contrast=1.0,
        focus=1.0,
        balance=1.0,
    ):
        self.camera_index = camera_index
        self.fps = fps
        self.width = width
        self.height = height
        self.copy_frame = copy_frame
        self.brightness = brightness
        self.contrast = contrast
        self.focus = focus
        self.balance = balance
        self.picam2 = None
        self.thread = None
        self.running = False
        self.lock = threading.Lock()
        self.frame = None
        self.cached_jpeg = None
        self.timestamp = 0.0

    def start(self):
        if self.running:
            return

        from picamera2 import Picamera2

        width = int(self.width)
        height = int(self.height)
        fps = int(self.fps)

        self.picam2 = Picamera2(camera_num=self.camera_index)
        configuration = self.picam2.create_video_configuration(
            main={"size": (width, height), "format": "RGB888"}
        )
        self.picam2.configure(configuration)

        for control_name, value in (
            ("Brightness", float(self.brightness)),
            ("Contrast", float(self.contrast)),
            ("LensPosition", float(self.focus)),
            ("AwbMode", int(self.balance)),
        ):
            try:
                self.picam2.set_controls({control_name: value})
            except Exception:
                pass

        self.fps = float(fps)

        self.picam2.start()
        self.running = True
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _update(self):
        frame_interval = 1.0 / self.fps
        while self.running:
            started = time.perf_counter()
            frame = self.picam2.capture_array()
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            success, encoded = cv2.imencode(".jpg", frame)
            if success:
                jpg_bytes = encoded.tobytes()
                if is_valid_jpeg_bytes(jpg_bytes):
                    with self.lock:
                        self.frame = frame.copy() if self.copy_frame else frame
                        self.cached_jpeg = jpg_bytes
                        self.timestamp = time.time()

            elapsed = time.perf_counter() - started
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)

    def get_frame(self):
        with self.lock:
            if self.frame is None:
                return None
            return self.frame.copy() if self.copy_frame else self.frame

    def get_jpeg(self):
        with self.lock:
            return self.cached_jpeg

    def get_frame_with_timestamp(self):
        with self.lock:
            if self.frame is None:
                return None, None
            frame = self.frame.copy() if self.copy_frame else self.frame
            return frame, self.timestamp

    def stop(self):
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        if self.picam2 is not None:
            self.picam2.stop()
            self.picam2.close()
