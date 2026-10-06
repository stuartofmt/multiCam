import subprocess
import re

def get_v4l2_devices():
	result = subprocess.run(
		["v4l2-ctl", "--list-devices"],
		capture_output=True,
		text=True
	)

	if result.returncode != 0:
		raise RuntimeError(f"v4l2-ctl failed: {result.stderr.strip()}")

	devices = {}
	current_name = None

	for line in result.stdout.splitlines():
		if not line.strip():
			continue
		if not line.startswith('\t') and not line.startswith('    '):
			# This is a device name line, e.g. "HD USB Camera: HD USB Camera (usb-xhci...):"
			current_name = line.strip().rstrip(':')
			devices[current_name] = []
		elif current_name is not None and '/dev/video' in line:
			devices[current_name].append(line.strip())

	return devices


if __name__ == "__main__":
	devices = get_v4l2_devices()
	for name, paths in devices.items():
		print(f"{name}")
		for path in paths:
			print(f"  - {path}")