# multiCam 1.7 – change notes

What has changed since multiCam 1.6.0. Version 1.7 is in development: the current pre-release is 1.7.0-beta.1.

## At a glance

- **Newer OpenCV where the system allows it.** The Python packages are now chosen per Python version. On Raspberry Pi OS Trixie (Python 3.13) and other systems with Python 3.12 or later, multiCam installs OpenCV 5.
- **Bookworm is unchanged.** On Python 3.10 and 3.11, OpenCV stays below 4.12, so Bookworm keeps the numpy 1 that its Pi camera packages are built for.

### What you need to do

- Nothing: install it over 1.6. The settings are kept.

## Python packages

- **One section per Python version.** The DWC plugin installs from a new `requirements1.txt`, which has one section each for Python 3.10, 3.11, 3.12, 3.13, and 3.14 and later. Only one section applies on a given system. Each section has its own version ranges, starting from the oldest release that works on that Python. pip and pipInstall2 both read the file.
- **OpenCV and numpy.**

  | Python | Typical system | OpenCV | numpy |
  | --- | --- | --- | --- |
  | 3.10 | – | 4.10.0.84 to below 4.12 | 1 or 2 |
  | 3.11 | Raspberry Pi OS Bookworm | 4.10.0.84 to below 4.12 | Raspberry Pi OS's numpy 1.24 is kept |
  | 3.12 and later | Raspberry Pi OS Trixie (3.13) | 4.10.0.84 to OpenCV 5 | Raspberry Pi OS's numpy 2 is kept |

- **Python version check at install.** `plugin.json` asks the installer for Python 3.10 or later (`--python>=3.10`), so an older Python is reported when the plugin is installed, not only when it starts.
- **Standalone.** The standalone launchers still install from `requirements.txt` for now.
