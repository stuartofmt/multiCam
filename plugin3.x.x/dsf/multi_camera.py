"""
multi_camera.py

Background multi-camera capture manager using OpenCV.
Optimized for Linux webcam streaming.
"""

import cv2
import time
import threading
import os

from typing import Dict, Optional

import logger_module


NETWORK_TIMEOUT_MSEC = 10000
# Accept a frame slightly early so source jitter doesn't halve the output rate.
FRAME_DUE_TOLERANCE = 0.25
# Seconds without a frame before a network source is reopened.
RECONNECT_AFTER_SEC = 5.0


def normalize_rotation(rotate) -> int:
	"""Round to the nearest quarter turn, returned as 0, 90, 180 or 270 (clockwise)."""
	return (int((float(rotate) + 45) // 90) * 90) % 360


def _is_jpeg(data) -> bool:
	return data is not None and data.size > 4 and data.flat[0] == 0xFF and data.flat[1] == 0xD8


def _dht_table(table_class_id, bits, values):
	return bytes([table_class_id]) + bytes(bits) + bytes(values)


# Standard Huffman tables (JPEG spec Annex K.3). Many webcams omit these from their
# MJPEG frames; desktop browsers assume them, but Safari / iOS refuse to decode the frame.
_STANDARD_DHT_TABLES = (
	_dht_table(0x00, [0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0], range(12)),
	_dht_table(0x01, [0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0], range(12)),
	_dht_table(0x10, [0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d], [
		0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07,
		0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xa1, 0x08, 0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52, 0xd1, 0xf0,
		0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0a, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x25, 0x26, 0x27, 0x28,
		0x29, 0x2a, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49,
		0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69,
		0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7a, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
		0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7,
		0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5,
		0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe1, 0xe2,
		0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf1, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
		0xf9, 0xfa,
	]),
	_dht_table(0x11, [0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77], [
		0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12, 0x41, 0x51, 0x07, 0x61, 0x71,
		0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91, 0xa1, 0xb1, 0xc1, 0x09, 0x23, 0x33, 0x52, 0xf0,
		0x15, 0x62, 0x72, 0xd1, 0x0a, 0x16, 0x24, 0x34, 0xe1, 0x25, 0xf1, 0x17, 0x18, 0x19, 0x1a, 0x26,
		0x27, 0x28, 0x29, 0x2a, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48,
		0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68,
		0x69, 0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7a, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
		0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5,
		0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3,
		0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda,
		0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
		0xf9, 0xfa,
	]),
)
_STANDARD_DHT_BODY = b"".join(_STANDARD_DHT_TABLES)
STANDARD_DHT_SEGMENT = b"\xff\xc4" + (len(_STANDARD_DHT_BODY) + 2).to_bytes(2, "big") + _STANDARD_DHT_BODY


def ensure_huffman_tables(jpg_bytes: bytes) -> bytes:
	"""Insert the standard Huffman tables if the JPEG header has none (webcam MJPEG)."""

	# Walk the header segments up to the start of scan (SOS) looking for a DHT.
	pos = 2
	while pos + 4 <= len(jpg_bytes) and jpg_bytes[pos] == 0xFF:
		marker = jpg_bytes[pos + 1]
		if marker == 0xC4:
			return jpg_bytes
		if marker == 0xDA:
			return jpg_bytes[:pos] + STANDARD_DHT_SEGMENT + jpg_bytes[pos:]
		pos += 2 + int.from_bytes(jpg_bytes[pos + 2:pos + 4], "big")

	return jpg_bytes


class ClientTracking:
	"""Counts active consumers so capture threads only encode when someone is watching."""

	def _init_clients(self):
		self._clients = 0
		self._clients_lock = threading.Lock()
		self._has_clients = threading.Event()

	def add_client(self):
		with self._clients_lock:
			self._clients += 1
			self._has_clients.set()

	def remove_client(self):
		with self._clients_lock:
			self._clients = max(0, self._clients - 1)
			if self._clients == 0:
				self._has_clients.clear()


class CameraStream(ClientTracking):
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
		rotate: int,
		jpegresolution: int,
		format: Optional[str] = None,
		capturefps: Optional[float] = None,
	):

		self.source = source
		self.cameratype = cameratype

		# fps is the rate served; the device can run faster (capturefps) and
		# the capture loop drops frames to match fps.
		self.fps = fps
		self.capturefps = capturefps if capturefps is not None else fps
		self.frame_interval = 1.0 / fps

		self.width = width
		self.height = height

		self.api_preference = api_preference

		self.rotate = normalize_rotation(rotate)
		self.jpegresolution = jpegresolution
		self.format = format

		self.capture: Optional[cv2.VideoCapture] = None
		self.thread: Optional[threading.Thread] = None

		self.running = False

		self.lock = threading.Lock()

		self.timestamp = 0.0
		self.cached_jpeg: Optional[bytes] = None

		# True when the source already delivers JPEG and it can be served unchanged.
		self.passthrough = False

		self._init_clients()

	def _open_capture(self, backend, ffmpeg_opts, params):
		"""Open a VideoCapture with per-camera ffmpeg options (see comment in start)."""

		with CameraStream._ffmpeg_env_lock:

			old_ffmpeg_opts = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")

			if ffmpeg_opts is not None:
				os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = ffmpeg_opts
			else:
				os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)

			try:
				return cv2.VideoCapture(self.source, backend, params)
			finally:
				if old_ffmpeg_opts is not None:
					os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = old_ffmpeg_opts
				else:
					os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)

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

		# Timeouts only take effect when passed at open time.
		if is_network_source:
			params = [
				cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, NETWORK_TIMEOUT_MSEC,
				cv2.CAP_PROP_READ_TIMEOUT_MSEC, NETWORK_TIMEOUT_MSEC,
			]
		else:
			params = []

		self._open_args = (backend, ffmpeg_opts, params)
		self._is_network = is_network_source
		self._is_http = is_http_source

		self.capture = self._open_capture(backend, ffmpeg_opts, params)

		if not self.capture.isOpened():

			raise RuntimeError(
				f"Unable to open camera source: "
				f"{self.source}"
			)

		if not is_network_source:
			# MJPG and frame-size negotiation apply to local V4L2 devices.
			# Sending these properties to an RTSP/FFmpeg capture can interfere
			# with the codec selected by the network source.
			fourcc_str = self.format if self.format else 'MJPG'
			self.capture.set(
				cv2.CAP_PROP_FOURCC,
				cv2.VideoWriter.fourcc(*fourcc_str)
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

			if self.capturefps is not None:
				self.capture.set(
					cv2.CAP_PROP_FPS,
					float(self.capturefps)
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
		# JPEG passthrough: serve the source's own JPEG without decoding
		# or re-encoding. Only possible without rotation, which needs pixels.
		# USB: CONVERT_RGB=0 returns the raw MJPG buffer.
		# HTTP: CAP_PROP_FORMAT=-1 returns raw demuxed packets (JPEG for MJPEG streams).
		#
		try_passthrough = self.rotate == 0 and (is_http_source or not is_network_source)

		if try_passthrough:
			if is_http_source:
				self.capture.set(cv2.CAP_PROP_FORMAT, -1)
			else:
				self.capture.set(cv2.CAP_PROP_CONVERT_RGB, 0)

		#
		# Test frame capture
		#
		ok, frame = self.capture.read()

		if try_passthrough and ok:
			self.passthrough = _is_jpeg(frame)

			if not self.passthrough:
				logger_module.logger.debug(
					f"{self.source} does not deliver JPEG; using decode/encode"
				)
				if is_http_source:
					# Raw mode can't be switched off on an open capture.
					self.capture.release()
					self.capture = self._open_capture(backend, ffmpeg_opts, params)
				else:
					self.capture.set(cv2.CAP_PROP_CONVERT_RGB, 1)
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
			f"{' (JPEG passthrough)' if self.passthrough else ''}"
		)

		self.running = True

		self.thread = threading.Thread(
			target=self._update,
			daemon=True
		)

		self.thread.start()

	def _apply_rotation(self, frame):
		"""Apply the configured rotation to a frame."""

		if self.rotate == 90:
			return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
		if self.rotate == 270:
			return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
		if self.rotate == 180:
			return cv2.rotate(frame, cv2.ROTATE_180)

		return frame

	def _reopen(self):
		"""Reopen a network source after it stops delivering frames."""

		logger_module.logger.warning(f"No frames from {self.source}; reconnecting")

		self.capture.release()
		self.capture = self._open_capture(*self._open_args)

		if not self.capture.isOpened():
			logger_module.logger.warning(f"Reconnect to {self.source} failed; will retry")
			return

		if self.passthrough and self._is_http:
			self.capture.set(cv2.CAP_PROP_FORMAT, -1)

		logger_module.logger.info(f"Reconnected to {self.source}")

	def _update(self):
		"""
		Background frame capture loop.
		"""

		next_due = 0.0
		last_frame_at = time.monotonic()

		while self.running:
			# Always grab so device/network buffers are drained and frames stay current.
			# grab() only dequeues; decoding happens in retrieve().

			if self.capture is None:
				raise RuntimeError(
					"Camera capture is not initialized"
				)

			if not self.capture.grab():
				# Failed/disconnected sources return immediately; avoid spinning.
				if self._is_network and time.monotonic() - last_frame_at >= RECONNECT_AFTER_SEC:
					self._reopen()
					last_frame_at = time.monotonic()
				time.sleep(0.1)
				continue

			last_frame_at = time.monotonic()

			if not self._has_clients.is_set():
				continue

			now = time.monotonic()
			if now < next_due - FRAME_DUE_TOLERANCE * self.frame_interval:
				continue
			next_due = max(next_due + self.frame_interval, now)

			ok, frame = self.capture.retrieve()

			if not ok or frame is None:
				continue

			try:
				if self.passthrough:
					jpg_bytes = ensure_huffman_tables(frame.tobytes())
				else:
					frame = self._apply_rotation(frame)
					success, encoded = cv2.imencode(
						".jpg",
						frame,
						[int(cv2.IMWRITE_JPEG_QUALITY), int(self.jpegresolution)],
					)
					if not success:
						continue
					jpg_bytes = encoded.tobytes()

				with self.lock:
					self.cached_jpeg = jpg_bytes
					self.timestamp = time.time()
			except Exception:
				# Encoding failures should not stop the capture loop
				pass

	def get_jpeg(self):
		"""
		Return cached JPEG bytes for the latest frame.
		"""

		with self.lock:
			return self.cached_jpeg

	def get_jpeg_with_timestamp(self):
		"""
		Return cached JPEG bytes and the time they were captured.
		"""

		with self.lock:
			return self.cached_jpeg, self.timestamp

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
		rotate: int,
		jpegresolution: int,
		cameratype: Optional[str],
		format: Optional[str] = None,
		controls: Optional[dict] = None,
		capturefps: Optional[float] = None,
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
				rotate=rotate,
				jpegresolution=jpegresolution,
				controls=controls,
			)
		else:
			self.cameras[name] = CameraStream(
				source=source,
				cameratype=cameratype,
				fps=fps,
				width=width,
				height=height,
				api_preference=api_preference,
				rotate=rotate,
				jpegresolution=jpegresolution,
				format=format,
				capturefps=capturefps,
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

	def get_jpeg(self, name: str):
		"""
		Retrieve latest cached JPEG bytes for a camera.
		"""

		if name not in self.cameras:

			raise KeyError(
				f"Unknown camera '{name}'"
			)

		return self.cameras[name].get_jpeg()

	def get_jpeg_with_timestamp(self, name: str):
		"""
		Retrieve latest cached JPEG bytes + capture timestamp.
		"""

		if name not in self.cameras:

			raise KeyError(
				f"Unknown camera '{name}'"
			)

		return self.cameras[name].get_jpeg_with_timestamp()

	def add_client(self, name: str):
		self.cameras[name].add_client()

	def remove_client(self, name: str):
		self.cameras[name].remove_client()

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