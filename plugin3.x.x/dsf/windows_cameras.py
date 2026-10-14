"""
USB cameras on Windows, through DirectShow - the Windows counterpart of what v4l2-ctl does on Linux:
  - list_cameras():          the cameras' names, numbered as OpenCV's DirectShow backend numbers them
  - get_formats(number):     the formats, sizes and frame rates each camera offers
  - reset_controls(number):  every control the camera has back to its default, with the ranges of the
                             AllowedOptions ones
  - set_controls(number,..): set options, limited to the camera's ranges

Only imported on Windows (see camera_platform). It needs comtypes and pygrabber (requirements.txt, Windows
only); if they cannot be loaded, AVAILABLE is False and multiCam falls back to setting controls through OpenCV
without ranges. Only pygrabber's low-level DirectShow definitions are used - not its graph module, which also
needs Windows Media (missing on Windows "N" editions).

DirectShow controls (IAMVideoProcAmp, IAMCameraControl) have a value and an auto / manual flag. multiCam's
on/off options map to the auto flags: balance -> white balance, autoexposure -> exposure, autofocus -> focus
(1 = automatic, 0 = manual). Unlike Linux, where autoexposure is the driver's own menu value.

All calls are made from multiCam's main thread at startup (COM is set up for it when comtypes is imported).
"""

from ctypes import POINTER, c_long, cast

from logger_module import logger

try:
	from comtypes import COMMETHOD, GUID, HRESULT, COMError, IUnknown, client
	from comtypes.persist import IPropertyBag
	# pygrabber's DirectShow definitions (MIT licence, Andrea Schiavinato): device enumerator, stream config,
	# media type layout, and the qedit.dll type library (IBaseFilter, IPin, AM_MEDIA_TYPE)
	from pygrabber.dshow_core import IAMStreamConfig, ICreateDevEnum, VIDEOINFOHEADER, qedit
	from pygrabber.dshow_ids import DeviceCategories, FormatTypes, clsids, subtypes
	AVAILABLE = True
except Exception as e:   # not Windows, packages missing, or DirectShow parts missing on this Windows
	AVAILABLE = False
	_IMPORT_ERROR = e


if AVAILABLE:
	# axextend.idl: IAMVideoProcAmp and IAMCameraControl have the same three methods
	_CONTROL_METHODS = [
		COMMETHOD([], HRESULT, "GetRange",
				  (["in"], c_long, "Property"),
				  (["out"], POINTER(c_long), "pMin"),
				  (["out"], POINTER(c_long), "pMax"),
				  (["out"], POINTER(c_long), "pSteppingDelta"),
				  (["out"], POINTER(c_long), "pDefault"),
				  (["out"], POINTER(c_long), "pCapsFlags")),
		COMMETHOD([], HRESULT, "Set",
				  (["in"], c_long, "Property"),
				  (["in"], c_long, "lValue"),
				  (["in"], c_long, "Flags")),
		COMMETHOD([], HRESULT, "Get",
				  (["in"], c_long, "Property"),
				  (["out"], POINTER(c_long), "lValue"),
				  (["out"], POINTER(c_long), "Flags")),
	]

	class IAMVideoProcAmp(IUnknown):
		_iid_ = GUID("{C6E13360-30AC-11d0-A18C-00A0C9118956}")
		_methods_ = _CONTROL_METHODS

	class IAMCameraControl(IUnknown):
		_iid_ = GUID("{C6E13370-30AC-11d0-A18C-00A0C9118956}")
		_methods_ = _CONTROL_METHODS


# Flags of IAMVideoProcAmp / IAMCameraControl (VideoProcAmp_Flags_*, CameraControl_Flags_*)
FLAG_AUTO = 0x1
FLAG_MANUAL = 0x2

# Every control there is: (interface name, property number). VideoProcAmpProperty and CameraControlProperty.
VIDEO_PROC_AMP = {"brightness": 0, "contrast": 1, "hue": 2, "saturation": 3, "sharpness": 4, "gamma": 5,
				  "colorenable": 6, "whitebalance": 7, "backlightcompensation": 8, "gain": 9}
CAMERA_CONTROL = {"pan": 0, "tilt": 1, "roll": 2, "zoom": 3, "exposure": 4, "iris": 5, "focus": 6}

# multiCam options (AllowedOptions) -> (interface, property, what is set: "value" or "auto" (the auto flag))
OPTION_CONTROLS = {
	"brightness": ("procamp", VIDEO_PROC_AMP["brightness"], "value"),
	"contrast": ("procamp", VIDEO_PROC_AMP["contrast"], "value"),
	"saturation": ("procamp", VIDEO_PROC_AMP["saturation"], "value"),
	"sharpness": ("procamp", VIDEO_PROC_AMP["sharpness"], "value"),
	"balance": ("procamp", VIDEO_PROC_AMP["whitebalance"], "auto"),
	"autoexposure": ("camera", CAMERA_CONTROL["exposure"], "auto"),
	"autofocus": ("camera", CAMERA_CONTROL["focus"], "auto"),
}

# DirectShow gives each format a slowest and fastest frame rate, not a list: offered as these common rates
# between the two (plus both ends), so multiCam's "next lower frame rate" rule works as it does on Linux
COMMON_RATES = (5, 7.5, 10, 15, 20, 24, 25, 30, 50, 60, 90, 120)

_PIN_OUT = 1   # PINDIR_OUTPUT


def _devices():
	"""[(name, IBaseFilter)] for each video capture device, in DirectShow's order - the order OpenCV numbers them."""
	enumerator = client.CreateObject(clsids.CLSID_SystemDeviceEnum, interface=ICreateDevEnum)
	try:
		monikers = enumerator.CreateClassEnumerator(GUID(DeviceCategories.VideoInputDevice), dwFlags=0)
	except (ValueError, COMError):
		return []   # no video devices at all (the enumerator is NULL)
	if not monikers:
		return []
	devices = []
	while True:
		try:
			moniker, count = monikers.Next(1)
		except (ValueError, COMError):
			break
		if not count:
			break
		try:
			bag = moniker.BindToStorage(0, 0, IPropertyBag._iid_).QueryInterface(IPropertyBag)
			name = str(bag.Read("FriendlyName", pErrorLog=None))
		except COMError:
			name = f"Camera {len(devices)}"
		try:
			device = moniker.BindToObject(0, 0, qedit.IBaseFilter._iid_).QueryInterface(qedit.IBaseFilter)
		except COMError as e:
			logger.debug(f"Could not open DirectShow device {name}: {e}")
			device = None
		devices.append((name, device))
	return devices


def _device(number):
	devices = _devices()
	number = int(number)
	if number >= len(devices) or devices[number][1] is None:
		raise RuntimeError(f"No DirectShow camera {number}")
	return devices[number][1]


def list_cameras():
	"""[(number as a string, name)] of the video capture devices - the numbers OpenCV's DirectShow backend opens."""
	return [(str(number), name) for number, (name, _) in enumerate(_devices())]


def _interface(device, which):
	try:
		return device.QueryInterface(IAMVideoProcAmp if which == "procamp" else IAMCameraControl)
	except COMError:
		return None   # the camera has no controls of this kind


def get_formats(number):
	"""
	{format fourcc: {(width, height): [fps, ...]}} - the same shape get_config._parse_formats makes from
	v4l2-ctl - from the capture pin's stream capabilities. Empty if they cannot be read.
	"""
	formats = {}
	try:
		device = _device(number)
		pins = device.EnumPins()
		while True:
			pin, count = pins.Next(1)
			if not count:
				break
			if pin.QueryDirection() != _PIN_OUT:
				continue
			try:
				config = pin.QueryInterface(IAMStreamConfig)
			except COMError:
				continue
			caps_count, _ = config.GetNumberOfCapabilities()
			for index in range(caps_count):
				media_type, caps = config.GetStreamCaps(index)
				if media_type.contents.formattype != GUID(FormatTypes.FORMAT_VideoInfo):
					continue
				header = cast(media_type.contents.pbFormat, POINTER(VIDEOINFOHEADER)).contents.bmi_header
				fourcc = subtypes.get(str(media_type.contents.subtype).upper(), subtypes.get(str(media_type.contents.subtype)))
				if not fourcc:
					continue
				size = (int(header.biWidth), abs(int(header.biHeight)))
				# Frame intervals are in 100 ns units: the shortest interval is the fastest rate
				fastest = round(10_000_000 / caps.MinFrameInterval, 3) if caps.MinFrameInterval else None
				slowest = round(10_000_000 / caps.MaxFrameInterval, 3) if caps.MaxFrameInterval else fastest
				if fastest is None:
					continue
				rates = formats.setdefault(fourcc.upper(), {}).setdefault(size, [])
				for rate in (fastest, *[r for r in COMMON_RATES if slowest <= r <= fastest], slowest):
					if rate not in rates:
						rates.append(rate)
			break   # the first output pin is the capture pin
	except Exception as e:
		logger.debug(f"Could not read the formats of camera {number}: {e}")
		return {}
	for sizes in formats.values():
		for rates in sizes.values():
			rates.sort(reverse=True)
	return formats


def _read_range(control, prop, mode):
	"""{"min", "max", "default"} of one control, or None if the camera does not have it (or not its auto flag)."""
	try:
		low, high, _step, default, caps_flags = control.GetRange(prop)
	except COMError:
		return None
	if mode == "auto":
		# An on/off option: only when the camera can switch between automatic and manual
		if not (caps_flags & FLAG_AUTO and caps_flags & FLAG_MANUAL):
			return None
		return {"min": 0, "max": 1, "default": 1}
	return {"min": int(low), "max": int(high), "default": int(default)}


def reset_controls(number):
	"""
	Set every control the camera has back to its default - as get_config does with v4l2-ctl on Linux - and return
	{option: {"min", "max", "default"}} for the AllowedOptions it supports.
	Value controls go to their default value (manual); controls that can be automatic go back to automatic.
	"""
	device = _device(number)
	options = {}
	for which, props in (("procamp", VIDEO_PROC_AMP), ("camera", CAMERA_CONTROL)):
		control = _interface(device, which)
		if control is None:
			continue
		for prop in props.values():
			try:
				_low, _high, _step, default, caps_flags = control.GetRange(prop)
			except COMError:
				continue   # this camera does not have this control
			flags = FLAG_AUTO if caps_flags & FLAG_AUTO else FLAG_MANUAL
			try:
				control.Set(prop, default, flags)
			except COMError as e:
				logger.debug(f"[camera {number}] could not reset control {which}/{prop}: {e}")
		for option, (option_which, prop, mode) in OPTION_CONTROLS.items():
			if option_which == which:
				bounds = _read_range(control, prop, mode)
				if bounds is not None:
					options[option] = bounds
	return options


def set_controls(number, values):
	"""
	Set options ({option: value}, already limited to the camera's ranges) on the camera. Returns the ones that were
	set, as set.
	"""
	device = _device(number)
	applied = {}
	for option, value in values.items():
		if option not in OPTION_CONTROLS:
			continue
		which, prop, mode = OPTION_CONTROLS[option]
		control = _interface(device, which)
		if control is None:
			continue
		try:
			if mode == "auto":
				current, _flags = control.Get(prop)
				control.Set(prop, current, FLAG_AUTO if int(value) else FLAG_MANUAL)
			else:
				control.Set(prop, int(round(float(value))), FLAG_MANUAL)
			applied[option] = value
		except COMError as e:
			logger.info(f"[camera {number}] {option}: the camera did not accept {value} ({e})")
	return applied
