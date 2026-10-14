"""
What differs between Linux (Raspberry Pi OS - the DWC plugin, or standalone) and Windows (standalone).

Linux:   USB cameras are V4L2 devices, /dev/videoN, found and controlled with v4l2-ctl. Pi (CSI) cameras
         use Picamera2 / libcamera.
Windows: USB cameras are DirectShow devices, numbered 0, 1, ... in the order Windows lists them. Their names,
         formats and control ranges come from DirectShow (windows_cameras); without it, cameras are found by
         opening each number in turn and controls are set through OpenCV, without ranges. No Pi cameras.
Network streams (STREAM) work the same everywhere.
"""

import importlib.util
import re
import sys

import cv2

IS_WINDOWS = sys.platform == "win32"
PLATFORM = "windows" if IS_WINDOWS else "linux"

# Picamera2 comes from the Raspberry Pi OS packages (python3-picamera2); checked without importing it, which is slow
PICAMERA_AVAILABLE = not IS_WINDOWS and importlib.util.find_spec("picamera2") is not None

# The OpenCV backend for USB cameras. DirectShow on Windows: it negotiates MJPG / size / fps reliably and opens
# much faster than Media Foundation.
USB_BACKEND = cv2.CAP_DSHOW if IS_WINDOWS else cv2.CAP_V4L2

# Camera numbers tried on Windows, stopping after this many in a row are missing
WINDOWS_MAX_CAMERAS = 10
WINDOWS_MISSES_TO_STOP = 3

# Camera controls (AllowedOptions) as OpenCV properties, used on Windows when DirectShow (windows_cameras) cannot
# be. Values are passed as given: OpenCV does not report the ranges, and drivers differ (e.g. autoexposure is
# 1 = manual / 3 = auto on some, 0.25 / 0.75 on others).
OPENCV_CONTROLS = {
    "brightness": cv2.CAP_PROP_BRIGHTNESS,
    "contrast": cv2.CAP_PROP_CONTRAST,
    "saturation": cv2.CAP_PROP_SATURATION,
    "sharpness": cv2.CAP_PROP_SHARPNESS,
    "balance": cv2.CAP_PROP_AUTO_WB,
    "autofocus": cv2.CAP_PROP_AUTOFOCUS,
    "autoexposure": cv2.CAP_PROP_AUTO_EXPOSURE,
}

USB_SOURCE_HINT = "0" if IS_WINDOWS else "/dev/video0"
USB_SOURCE_RULE = "a camera number (0, 1, ...)" if IS_WINDOWS else "of the form /dev/videoN"


def is_usb_source(source):
    """Whether source is a USB camera source on this platform: a camera number on Windows, /dev/videoN on Linux."""
    source = str(source).strip()
    return bool(re.fullmatch(r"\d+", source)) if IS_WINDOWS else source.startswith("/dev/video")


def usb_capture_source(source):
    """What OpenCV opens for a USB source: the camera number on Windows, the device path on Linux."""
    return int(source) if IS_WINDOWS else source
