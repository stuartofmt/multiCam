# multiCam 1.6.0 – change notes

What changed since multiCam 1.5.0. Windows support, DirectShow, **Restart now** and the split requirements files were already in 1.5.0.

## At a glance

- **New Log tab.** Every page now has the same tabs across the top: **Camera Streams | Settings | Log**. On a phone the tabs show icons only.
- **Runs on Raspberry Pi OS Bookworm.** multiCam now needs Python 3.10 or later, down from 3.12.
- **Easier to see why it stopped.** Startup messages are logged from the very start, even without a console.

### What you need to do

- Nothing for the DWC plugin: install it over the old one. On a standalone install, unzip the new release over the old one; the settings are kept.

## Web interface

- **Log tab.** The Log page shows the logfile and follows it as it grows.
  - It has **Follow**, **Wrap lines**, **Reload** and **Download**.
  - After a restart, it switches to the new log.
  - On a very long log, it starts at the end.
- **Terminate button.** It's on the Settings page, for standalone installs only (not the DWC plugin). It stops the cameras and ends multiCam.

## Running and logging

- **Startup log.** From the moment multiCam starts, messages go to the console and to a new `multiCam.startup.log` in the program folder, so an early exit is recorded even with no console. Once the startup checks pass, logging moves to `multiCam.log`, which begins with the startup log's contents.
- **Launchers run without a console window.** `run.sh` and `run.bat` start multiCam in the background. Add `console` as an argument to keep it in the terminal.
- **Windows settings folder.** If the program folder can't be written to, the log and settings go to `%APPDATA%\multiCam`. With Microsoft Store Python they go to `Documents\multiCam` instead, because that Python hides files written in AppData. The folder in use is logged.

## Compatibility

- **Python 3.10 or later** (was 3.12). The version is checked at startup, before anything else loads, and multiCam stops with a clear message if it's too old.
- **Pi cameras on Bookworm.** OpenCV is held below 4.12, which works with numpy 1 or 2. That keeps Raspberry Pi OS's own numpy, which `python3-picamera2` is built for (numpy 1 on Bookworm).
- **Package versions.** Each package has a minimum version and, where it suits, stays below its next major release. They're written one comparator per line, so the DWC plugin's installer accepts them.
  - Picamera2 is no longer installed through pip: it comes from Raspberry Pi OS.
  - The separate Starlette requirement is gone, because FastAPI installs it.

## Packaging and releases

- **Standalone zip.** It's built from the repository root, and its launchers run `plugin3.x.x/dsf`. It leaves out editor and git settings and the interface's source (`ui/`).
- **Releases.** These now use the shared DWC-Tools release workflow, and multiCam's own release scripts are gone.
