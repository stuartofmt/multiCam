# multiCam

multiCam is a Duet Web Control SBC plugin for viewing and streaming multiple
cameras. It supports USB/V4L2 cameras, Raspberry Pi
cameras through Picamera2, and network cameras that provide an HTTP, HTTPS, or
RTSP stream.

## Configuration

The plugin reads its settings from:

`/opt/dsf/sd/sys/multiCam/multiCam.config`

Use `multiCam.config.example` as a starting point. Camera names are used in
the web URLs and must be unique.

### USB cameras

Add V4L2 device paths under `[CAMERAS]`:

```ini
[CAMERAS]
Front=/dev/video0
Rear=/dev/video2
```

The source must be a `/dev/videoN` device. The plugin uses V4L2 and applies
the configured resolution and frame rate where the camera supports them.

### Network cameras

Network streams can also be added under `[CAMERAS]`:

```ini
[CAMERAS]
Workshop=http://192.168.30.31:8081
```

HTTP, HTTPS, and RTSP sources use OpenCV's automatic backend selection.

### Raspberry Pi cameras

Add Pi cameras under `[PICAMERAS]` using a zero-based Pi-camera index:

```ini
[PICAMERAS]
Picamera=0
```

The index refers only to non-USB Picamera2 devices. For example, if a USB
camera is reported before the Pi camera by the system, `Picamera=0` still
selects the first Pi camera rather than `/dev/video0`.

## Camera settings

Create a section with the same name as each configured camera. Omitted values
use the plugin defaults.

```ini
[Front]
fps=30
width=1280
height=720
rotate=0
jpegresolution=90
brightness=1
contrast=1
```

The general settings are `fps`, `width`, `height`, `rotate`, and
`jpegresolution`. Supported camera controls include `brightness`, `contrast`,
`balance`, `saturation`, `autofocus`, `sharpness`, and `autoexposure`, when
exposed by the camera driver.

## Logging

Set the logging level in `[LOGGING]`:

```ini
[LOGGING]
LEVEL=INFO
```

Supported levels are `WARNING`, `INFO`, and `DEBUG`. The log is written beside
the configuration file as `multiCam.log`.

## Accessing the streams

The plugin starts its web interface on the port configured in `[UI]`:

```ini
[UI]
PORT=8044
```

Open `http://<SBC-IP>:8044/` to view the configured cameras. Each camera also
has these endpoints, where `<camera-name>` is URL-encoded when necessary:

```text
http://<SBC-IP>:8044/<camera-name>/stream
http://<SBC-IP>:8044/<camera-name>/snapshot
```

## Running manually

From the plugin's `dsf` directory, run:

```bash
python3 multiCam.py /opt/dsf/sd/sys/multiCam/multiCam.config
```

When installed through Duet Web Control, the plugin manager starts the
executable using the configuration path declared in `Code/plugin.json`.

