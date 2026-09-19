"""
multi_camera.py

Background multi-camera capture manager using OpenCV.
Optimized for Linux webcam streaming.
"""

import cv2
import numpy as np
import time
import threading
import os

from typing import Dict, Optional

import logger_module


def is_valid_jpeg_bytes(data: Optional[bytes]) -> bool:
	"""Reject truncated or corrupt JPEG payloads before serving them."""
	if not data or len(data) < 4:
		return False

	if data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
		return False

	try:
		decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
		return decoded is not None and decoded.size > 0
	except Exception:
		return False


class CameraStream:
	"""
	Single background camera stream.
	"""

	# Guards the brief window where OPENCV_FFMPEG_CAPTURE_OPTIONS is set,
	# a VideoCapture is opened, and the env var is restored. This env var
	# is process-wide, not per-capture, so if camera startup is ever made
	# concurrent (e.g. threaded), this lock keeps opens from stepping on
	# each other. Safe (and unused) for sequential startup too.
	_ffmpeg_env_lock = threading.Lock()

	def __init__(
		self,
		source,
		cameratype: Optional[str],
		fps: float,
		width: Optional[int],
		height: Optional[int],
		api_preference,
		copy_frame: bool,
		rotate: int,
		jpegresolution: int,
	):

		self.source = source
		self.cameratype = cameratype

		self.fps = fps
		self.frame_interval = 1.0 / fps

		self.width = width
		self.height = height

		self.api_preference = api_preference

		self.copy_frame = copy_frame

		self.rotate = rotate
		self.jpegresolution = jpegresolution

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


		is_network_source = (
			self.cameratype == "STREAM"
			and
			isinstance(self.source, str)
		)
		is_rtsp_source = (
			isinstance(self.source, str)
			and self.source.startswith("rtsp://")
		)
		is_http_source = (
			isinstance(self.source, str)
			and (self.source.startswith("http://") or self.source.startswith("https://"))
		)

		logger_module.logger.debug(
			f"Opening {self.source} "
			f"using {'network backend' if is_network_source else 'V4L2'}"
		)

		#
		# Open webcam
		#
		if self.api_preference:
			backend = self.api_preference
		elif isinstance(self.source, str):
			if self.cameratype == "STREAM":
				backend = cv2.CAP_ANY
			else:
				backend = cv2.CAP_V4L2

		#
		# FFmpeg capture options differ by source type:
		# - RTSP: force TCP transport to avoid incomplete/corrupt packets
		#   that UDP can produce.
		# - HTTP/HTTPS: enable reconnect behavior, since IP cameras over
		#   HTTP MJPEG drop connections more readily than RTSP.
		#
		# OPENCV_FFMPEG_CAPTURE_OPTIONS is a process-wide environment
		# variable, not a per-VideoCapture setting. It's read once, at
		# VideoCapture construction time, so we set it immediately before
		# opening this camera and restore whatever was there immediately
		# after. This keeps one camera's ffmpeg options from leaking into
		# another camera opened later in the same process. The lock only
		# needs to cover this narrow window; already-open captures are
		# unaffected by later env var changes.
		#
		if is_rtsp_source:
			ffmpeg_opts = "rtsp_transport;tcp"
		elif is_http_source:
			ffmpeg_opts = "reconnect;1|reconnect_streamed;1|reconnect_delay_max;2"
		else:
			ffmpeg_opts = None

		with CameraStream._ffmpeg_env_lock:

			old_ffmpeg_opts = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")

			if ffmpeg_opts is not None:
				os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = ffmpeg_opts
			else:
				os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)

			try:
				self.capture = cv2.VideoCapture(self.source, backend)
			finally:
				if old_ffmpeg_opts is not None:
					os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = old_ffmpeg_opts
				else:
					os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)

		# Allow some settling time
		self.capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000)
		self.capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 10000)



		if not self.capture.isOpened():

			raise RuntimeError(
				f"Unable to open camera source: "
				f"{self.source}"
			)

		if not is_network_source:
			# MJPG and frame-size negotiation apply to local V4L2 devices.
			# Sending these properties to an RTSP/FFmpeg capture can interfere
			# with the codec selected by the network source.
			self.capture.set(
				cv2.CAP_PROP_FOURCC,
				cv2.VideoWriter.fourcc(*'MJPG')
			)

			if self.width is not None:
				self.capture.set(
					cv2.CAP_PROP_FRAME_WIDTH,
					int(self.width)
				)

			if self.height is not None:
				self.capture.set(
					cv2.CAP_PROP_FRAME_HEIGHT,
					int(self.height)
				)

			if self.fps is not None:
				self.capture.set(
					cv2.CAP_PROP_FPS,
					float(self.fps)
				)

		#
		# Attempt to apply supported camera properties.
		# Some devices reject unsupported or unavailable controls;
		# in that case, ignore the setting instead of failing startup.
		#
		# for prop, value in (
		#     (cv2.CAP_PROP_BRIGHTNESS, float(self.brightness)),
		#     (cv2.CAP_PROP_CONTRAST, float(self.contrast)),
		#     (cv2.CAP_PROP_FOCUS, float(self.focus)),
		# ):
		#     try:
		#         self.capture.set(prop, value)
		#     except Exception:
		#         logger_module.logger.debug(
		#             f"Ignoring unsupported camera property {prop} for {self.source}"
		#         )
		#
		# wb_temperature = getattr(cv2, "CAP_PROP_WB_TEMPERATURE", None)
		# if wb_temperature is not None:
		#     try:
		#         self.capture.set(wb_temperature, float(self.balance))
		#     except Exception:
		#         logger_module.logger.debug(
		#             f"Ignoring unsupported white balance setting for {self.source}"
		#         )

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

		try:
			backend_name = self.capture.getBackendName()
		except Exception:
			backend_name = "unknown"
		logger_module.logger.debug(
			f"Camera opened successfully using backend {backend_name}"
		)

		self.running = True

		self.thread = threading.Thread(
			target=self._update,
			daemon=True
		)

		self.thread.start()

	def _apply_rotation(self, frame):
		"""Apply the configured rotation to a frame."""

		if frame is None or self.rotate == 0:
			return frame

		if self.rotate == 90:
			return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
		if self.rotate in (270, -90):
			return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
		if self.rotate == 180:
			return cv2.rotate(frame, cv2.ROTATE_180)

		return frame

	def _update(self):
		"""
		Background frame capture loop.
		"""

		while self.running:
			# Capture continuously; the device controls the frame rate.

			if self.capture is None:
				raise RuntimeError(
					"Camera capture is not initialized"
				)

			grabbed, frame = self.capture.read()

			if not grabbed:
				continue

			frame = self._apply_rotation(frame)

			with self.lock:
				self.frame = frame
				self.timestamp = time.time()
				local_frame = self.frame.copy() if self.copy_frame else self.frame

			# Encode JPEG in background thread to cache latest JPEG bytes
			try:
				success, encoded = cv2.imencode(
					".jpg",
					local_frame,
					[int(cv2.IMWRITE_JPEG_QUALITY), int(self.jpegresolution)],
				)

				if success:
					jpg_bytes = encoded.tobytes()
					if not is_valid_jpeg_bytes(jpg_bytes):
						continue
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
			# if self.contrast != 1.0:
			#     frame = cv2.convertScaleAbs(frame, alpha=self.contrast, beta=0)
			#
			# if self.brightness != 1.0:
			#     frame = cv2.convertScaleAbs(frame, alpha=1.0, beta=(self.brightness - 1.0) * 50)

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
		fps: float,
		width: Optional[int],
		height: Optional[int],
		api_preference,
		copy_frame: bool,
		rotate: int,
		jpegresolution: int,
		cameratype: Optional[str],
	):

		if name in self.cameras:

			raise ValueError(
				f"Camera '{name}' already exists"
			)

		if str(cameratype).lower() == "picamera":
			from picam import Picamera2Stream
			self.cameras[name] = Picamera2Stream(
				camera_index=int(source),
				fps=fps,
				width=width,
				height=height,
				copy_frame=copy_frame,
				rotate=rotate,
				jpegresolution=jpegresolution,
			)
		else:
			self.cameras[name] = CameraStream(
				source=source,
				cameratype=cameratype,
				fps=fps,
				width=width,
				height=height,
				api_preference=api_preference,
				copy_frame=copy_frame,
				rotate=rotate,
				jpegresolution=jpegresolution,
			)

	def start(self):
		"""
		Start all cameras.
		"""

		for name, cam in self.cameras.items():
			try:
				cam.start()
			except Exception as exc:
				logger_module.logger.warning(
					f"Skipping camera '{name}' because it could not start: {exc}"
				)

	def start_camera(self, name: str):
		"""
		Start one camera.
		"""

		try:
			self.cameras[name].start()
		except Exception as exc:
			logger_module.logger.warning(
				f"Camera '{name}' could not start: {exc}"
			)

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