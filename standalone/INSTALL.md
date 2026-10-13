# multiCam - standalone install

This is multiCam packaged to run on its own, without DuetWebControl (DWC) or a Duet.
It is for Raspberry Pi OS (it uses the Raspberry Pi camera packages) and needs internet
access while installing.

## Install or upgrade

From this unzipped folder:

```sh
sudo bash install.sh
```

This installs the system packages multiCam needs, puts the program in `/opt/multiCam`,
and starts the `multicam` service, which also starts at every boot. Running it again
upgrades multiCam and keeps your settings.

At the end it shows the address of the camera page, e.g. `http://192.168.1.50:17800`.
Open `/settings` on that address to add cameras.

## Afterwards

| To                         | Run                                         |
|----------------------------|---------------------------------------------|
| Find the address and port  | `grep "View cameras at" /var/lib/multiCam/multiCam.log` |
| See the log                | `less /var/lib/multiCam/multiCam.log` or `journalctl -u multicam` |
| Restart (after saving settings) | `sudo systemctl restart multicam`      |
| Stop / start               | `sudo systemctl stop multicam` / `sudo systemctl start multicam` |

The settings are kept in `/var/lib/multiCam/settings.config`. Change them only on the settings
page - the file is encoded, and an edited file is replaced with default settings.

## Uninstall

```sh
sudo bash uninstall.sh           # keeps the settings and log in /var/lib/multiCam
sudo bash uninstall.sh --purge   # removes those too
```
