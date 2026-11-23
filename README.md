# sysmon — System Monitor

A graphical system resource monitor for Raspberry Pi based systems, built with PyQt6. It displays live rolling charts for CPU utilization, disk I/O, network I/O, temperatures, and fan speed, updated once per second with 60 seconds of history.

sysmon started life as *oneUpMon*, a monitor written specifically for the [Argon ONE UP](https://argon40.com/) laptop (a Raspberry Pi Compute Module 5 in a laptop case). It has since been generalized: nothing in it is tied to the ONE UP any more, and it runs on any Raspberry Pi with a desktop, whether that's a bare Pi on a desk, a Pi in a case with an NVMe drive, or a CM4/CM5 in a carrier board or a laptop or handheld build.

## Features

- **Live charts, no configuration needed**: CPU cores, drives, and network interfaces are discovered automatically from the kernel at startup.
- **Per-core CPU utilization**, one line per logical core.
- **Disk and network throughput** that auto-scales from Bytes/s up to GiB/s, so an idle SD card and a busy NVMe drive are both readable.
- **Temperatures** for the CPU and for every drive that reports one over SMART (NVMe, SATA, and SATA-over-USB).
- **Fan speed** for the official Raspberry Pi 5 active cooler / fan header, plus an optional case fan read from a GPIO tachometer.
- **Collapsible, hideable charts**, with your layout remembered between runs.
- **Lightweight**: a single Python process polling `/proc` and `/sys` once a second; no daemon or background service.

## Supported hardware

sysmon reads standard Linux kernel interfaces, so most of it works on any Raspberry Pi. What differs between models is which sensors are present:

| System | CPU | Disk I/O | Network | CPU temp | Drive temps | Fan Speed chart |
|--------|:---:|:--------:|:-------:|:--------:|:-----------:|:---------------:|
| Raspberry Pi 5, Compute Module 5 (including the Argon ONE UP and other CM5 builds) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Raspberry Pi 4 / 400, Compute Module 4 | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| Raspberry Pi 3 and earlier | ✓ | ✓ | ✓ | ✓ | ✓ | — |

Notes:

- **Fan Speed chart**: only shown on a Pi 5 / CM5, because it relies on the Pi 5's `cooling_fan` kernel driver, which reports the fan's RPM. Older models have no standard fan tachometer interface, so the chart (and the optional case fan, which is drawn on it) is left out.
- **Drive temperatures** depend on the drive, not the Pi. NVMe drives and most SATA SSDs report a temperature; SD cards and eMMC never do, and some USB-SATA adapters need an extra `smartctl` option (see [`[smartctl]`](#smartctl-per-drive-smartctl-options-optional)). Drives that can't report a temperature can be hidden from the Temperature chart with [`temp_ignore`](#drive-drive-filtering).
- **Other Linux machines**: the CPU, disk, and network charts only use `/proc` and `/sys`, so they work on most Linux systems. However, sysmon imports `gpiozero` and `RPi.GPIO` at startup, so it is intended for, and only tested on, Raspberry Pi hardware running Raspberry Pi OS.

## Requirements

```
Python 3.10+
PyQt6 (including QtCharts)
gpiozero
RPi.GPIO
smartmontools (smartctl, for drive temperatures)
```

sysmon is a desktop (GUI) application, so it needs Raspberry Pi OS with desktop (or another distribution with a graphical session); it won't run on a headless Lite install without a display.

## Installation

### As a Debian package (recommended)

Build the `.deb` from the repository, then install it with `apt`, which pulls in all the dependencies:

```bash
sudo apt install debhelper              # one-time build tool
./build_package.sh                      # tests, builds, lintian; writes dist/sysmon_<version>_all.deb
sudo apt install ./dist/sysmon_*_all.deb
```

`build_package.sh` runs `./run_tests.sh` first and stops if any check fails, then builds with `dpkg-buildpackage -us -uc -b`, moves the results into `dist/`, cleans the build tree, and checks the `.deb` with `lintian` (if installed). Options: `--skip-tests` skips the checks, and `--install` (re)installs the new package with `apt` afterwards. The version comes from `debian/changelog`.

The package installs:

- the application in `/usr/lib/sysmon/app` (`src/` and `icons/`), run from its own Python virtual environment in `/usr/lib/sysmon/venv` (created at install time on top of the Debian-packaged PyQt6 and GPIO libraries, so nothing is downloaded with pip)
- a `sysmon` command in `/usr/bin`
- a **System Tools** menu entry with the sysmon icon
- an example configuration in `/usr/share/doc/sysmon/examples/sysmon.ini`

Remove it with `sudo apt remove sysmon`. The package installs no configuration file: sysmon works out [defaults](#built-in-defaults) from your hardware at every start, and your own changes, made with **File → Settings**, are saved per user in `~/.config/sysmon/sysmon.ini`. The `sudo smartctl` note in step 4 still applies.

To release a new version, add an entry to `debian/changelog` (`dch -i` from the `devscripts` package does this) before building.

### Manually

1. Install the dependencies. On Raspberry Pi OS (Bookworm or later):

   ```bash
   sudo apt install python3-pyqt6 python3-pyqt6.qtcharts python3-gpiozero python3-rpi-lgpio smartmontools
   ```

   `RPi.GPIO` is provided here by `python3-rpi-lgpio`, a drop-in replacement that works on every Pi model. The original `python3-rpi.gpio` package also works on a Pi 4 or earlier, but does not support the Pi 5.

2. Get the code:

   ```bash
   git clone https://github.com/JeffCurless/sysmon.git
   cd sysmon
   ```

3. Nothing to configure up front: sysmon uses [built-in defaults](#built-in-defaults), and **File → Settings** changes them. See [Configuration](#configuration) if you'd rather edit the settings file by hand.

4. If you want drive temperatures and aren't using a user with passwordless `sudo`, set that up as described below.

### Drive temperatures need `sudo smartctl` without a password

Drive temperatures are read by running `sudo smartctl` once per second for each drive. That only works if `sudo` can run `smartctl` without asking for a password. Raspberry Pi OS's default user already has passwordless `sudo`. For any other user, add a rule with `sudo visudo -f /etc/sudoers.d/smartctl`:

```
<username> ALL=(root) NOPASSWD: /usr/sbin/smartctl
```

If a drive's temperature can't be read, its line on the Temperature chart shows 0.

## Running

```bash
python3 src/sysmon.py    # or ./src/sysmon.py
```

Settings are changed with **File → Settings** and take effect immediately (see [Configuration](#configuration)).

### Example settings files

These are what `~/.config/sysmon/sysmon.ini` might contain; **File → Settings** writes the same thing.

A bare Raspberry Pi 5 booting from an SD card, connected over Wi-Fi:

```ini
[drive]
    temp_ignore = mmcblk0
    perf_ignore = mmcblk0

[network]
    device_ignore = lo, eth0
```

A Pi with an NVMe boot drive and a USB-attached SATA SSD, plus a case fan with its tachometer wire on GPIO 18:

```ini
[drive]
    temp_ignore = mmcblk0

[network]
    device_ignore = lo

[cooling]
    casefan = 18

[smartctl]
    sda = -d sat
```

The Argon ONE UP laptop (CM5 with NVMe; its `eth0` port isn't wired to anything):

```ini
[drive]
    temp_ignore = mmcblk0
    perf_ignore = mmcblk0

[network]
    device_ignore = lo, eth0
```

## The window

The window shows four chart rows, each with 60 seconds of history:

| Row | Chart | Notes |
|-----|-------|-------|
| 1 | **CPU Utilization** | One line per logical CPU core, 0–100% |
| 2 | **Disk I/O** | Read and write bytes/s per drive; auto-scales from Bytes/s through KiB/s, MiB/s, GiB/s |
| 3 | **Network I/O** | Receive and transmit bytes/s per interface; same auto-scaling as disk |
| 4 | **Temperature** | CPU (via `gpiozero`) and each monitored drive (via `smartctl`), 20–80 °C |
| 4 (right half) | **Fan Speed** | Raspberry Pi 5 / CM5 only: CPU fan RPM, plus a case fan if one is configured; 0–6000 RPM |

The disk and network charts rescale automatically. When a new sample is more than 1024 in the current unit, the chart moves up one unit (all history is converted and the title changes, e.g. `Disk I/O (MiB/s)`). When everything in the 60-second window drops below 1 in the current unit, it moves back down one unit per second. At the top unit (GiB/s) the Y axis simply grows to fit.

### Showing, hiding and collapsing charts

- **Click a chart's title** (the `▼ CPU Utilization` text at the top of the chart) to collapse it to a slim `▶ CPU Utilization` bar; click the bar to expand it again. A collapsed chart keeps collecting data, so it shows the full last 60 seconds when expanded again.
- **View menu**: turn each chart on or off. A chart that is turned off stops collecting data; when turned back on it starts empty.
- Which charts are shown and expanded is saved in `~/.config/sysmon/sysmon.conf` and restored at the next start. (Before the app was renamed from oneUpMon, this was `~/.config/oneUpMon/oneUpMon.conf`; those settings are copied over automatically the first time sysmon runs.)

### Menus

- **File → Settings** (`Ctrl+,`) edits the settings: which drives appear on the Temperature and Disk I/O charts, which network interfaces appear, the case fan's GPIO pin, and each drive's `smartctl` device type. **Reset to defaults** fills in the [built-in defaults](#built-in-defaults). Saving applies the changes at once by rebuilding the window (chart history restarts; which charts are shown or collapsed is kept).
- **File → Close** (`Ctrl+Q`) quits.
- **View** shows or hides each chart.
- **Help → Repository** opens the project's GitHub page, <https://github.com/JeffCurless/sysmon>; **Help → About** shows the version, what sysmon does, and where your settings file is.

## Design

### Source files

| File | Purpose |
|------|---------|
| `src/sysmon.py` | GUI application (the main program): window layout, menus, 1-second timer loop, config loading |
| `src/charts.py` | Chart widgets: rolling charts with fixed or auto-scaling Y axes, and the click-the-title collapsing wrapper |
| `src/systemsupport.py` | Hardware data: CPU load, CPU model/temperature/fan, drive stats and temperatures, network stats |
| `src/configfile.py` | INI file reader wrapping `configparser`; never raises, always returns the caller's default |
| `src/fanspeed.py` | Optional GPIO tachometer reader for a case fan |
| `src/appicon.py` | Builds the window icon from `icons/` |
| `src/autoconfig.py` | Built-in defaults from the hardware (NVMe root, Argon ONE UP), and `Settings`, which reads and writes the settings file |
| `src/version.py` | The version number, from `debian/changelog` (the only place it's set; the package build copies it to `/usr/lib/sysmon/app/VERSION`) |
| `src/userconfig.py` | Where the per-user settings file lives, one-time migration from `/etc/sysmon.ini`, and atomic saving |
| `src/settingsdialog.py` | The **File → Settings** dialog |
| `icons/` | `sysmonicon.svg` (the master icon), `sysmon<N>x<N>.png` renders of it at 16–256 px, and `make_icons.py`, which regenerates the PNGs after the SVG changes |
| `sysmon.ini` | Sample settings file |
| `tests/` | pytest suite (see [Testing](#testing)); `tests/fixtures/` holds its data files |
| `debian/` | Debian packaging; build it with `build_package.sh` (see [Installation](#installation)) |

### Data flow

Every second a `QTimer` fires `MonitorWindow.refresh_metrics()`, which calls into `systemsupport.py` and `fanspeed.py` and appends new values to each chart:

```
QTimer (1 s)
  └─ refresh_metrics()
       ├─ CPULoad.getPercentages()        → /proc/stat delta
       ├─ CPUInfo.temperature             → gpiozero CPUTemperature (/sys/class/thermal/thermal_zone0/temp)
       ├─ CPUInfo.CPUFanSpeed             → /sys/devices/platform/cooling_fan/hwmon/*/fan1_input  (Pi 5 only)
       ├─ GetCaseFanSpeed.RPM             → GPIO interrupt timing  (optional)
       ├─ multiDriveStat.readWriteBytes() → /sys/block/<dev>/stat delta × 512
       ├─ multiDriveStat.driveTemp()      → sudo smartctl -A /dev/<drive>
       └─ NetworkLoad.stats               → /sys/class/net/<iface>/statistics/{rx,tx}_bytes delta
```

Each data source handles its own expected errors. A reading that fails is shown as 0 (temperatures, fan) or left as a gap, and never stops the application. `refresh_metrics()` also has a last-resort guard, because PyQt6 aborts the whole application if an exception escapes a timer callback.

**Deltas.** CPU, disk, and network figures are differences between consecutive readings, so each sample is the activity during the last second, not a running total. For disk and network counters:

- A value is only reported between two good readings. If a read fails (including at startup), that second shows 0 and the next good reading becomes the new starting point. This avoids a huge one-off spike from reporting a counter's whole total as one second of traffic.
- If a counter goes backwards (a drive re-attached, or a VPN interface going down and up), it is treated as having restarted from zero.

**Hardware detection.**

- **CPU model**: read once from `/proc/cpuinfo` at startup. The Fan Speed chart only appears on a Raspberry Pi 5 or Compute Module 5.
- **CPU temperature sensor**: opened on first use and retried each second until it works, so a machine without the sensor still starts (the CPU temperature line shows 0).
- **Drives**: auto-discovered from `/sys/block/`, skipping loop, ram, and zram devices.
- **Network interfaces**: auto-discovered from `/sys/class/net/`.
- **Drive temperature**: parsed from `smartctl -A` output by checking, in order: the NVMe `Temperature:` line, SMART attribute `194`, then attribute `190`. Each call has a 5-second timeout.

### Chart classes

- **`RollingChart`**: fixed Y axis. Points older than the 60-sample window are trimmed (binary search for the cut-off); the X axis follows the newest point.
- **`RollingChartDynamic`**: extends `RollingChart` with an automatic unit scale. A `scaleValues` object tracks the current unit (Bytes/s, KiB/s, MiB/s, GiB/s), and once the largest visible value is above 1, the Y axis ceiling is the next power of two above it (minimum 4).
- **`CollapsibleChart`**: makes a chart collapsible. While expanded, the title is drawn inside the chart (`ChartView` turns a click above the plot area into a toggle), so no space is spent on a separate header; while collapsed, the chart is replaced by a 20 px `▶ title` bar. It also updates both titles when a dynamic chart changes unit.

All chart classes live in `src/charts.py`. Charts keep Qt Charts' padding to a minimum and put the legend on the right. Every legend gets the width of the widest one, so the stacked charts' time axes line up. Even at the minimum window size each plot is about 135 px tall, which leaves room for the Y-axis labels.

## Configuration

Settings are per user, in `~/.config/sysmon/sysmon.ini` (or `$XDG_CONFIG_HOME/sysmon/sysmon.ini`). The easiest way to change them is **File → Settings**; you can also edit the file by hand (a sample is provided as `sysmon.ini` in this repository). It uses standard INI format. The file is optional: anything it doesn't set falls back to the [built-in defaults](#built-in-defaults), key by key, and a file that can't be parsed is treated as empty from the point of the error onward.

Older versions used a shared `/etc/sysmon.ini`. If it exists and you have no personal file yet, sysmon copies it to your personal file the first time it runs, and doesn't read it after that.

**Why per user, not `/etc`:** sysmon runs as you and reaches root only through `sudo smartctl`, so sudo, not the settings file, decides what root access you have. A personal file can't give you anything you couldn't already do. A shared file that users could edit would be the risk, because one user could then choose the `smartctl` options another user's sysmon runs as root. As a further safeguard, `[smartctl]` values are limited to `-d TYPE`.

### Built-in defaults

sysmon works these out from the hardware at every start (`src/autoconfig.py`):

- **`lo`** is always hidden from the Network I/O chart.
- If the **root filesystem is on an NVMe drive**, `mmcblk0` (the SD card or eMMC) is hidden from both the Temperature and Disk I/O charts.
- On an **Argon ONE UP**, `eth0` is hidden: the CM5 module has an Ethernet controller, but the ONE UP doesn't wire it to a port. A ONE UP is recognised as a Compute Module 5 with the laptop's built-in keyboard (USB `6080:8060` or `6080:8061`) or built-in display (EDID manufacturer `BOE`, product `0x088b`). The CM5 on its own can't be told apart from other carrier boards.

To see the defaults for your machine, run `python3 src/autoconfig.py` (it prints them as a settings file, with what was detected at the top).

List values are comma-separated; spaces and double quotes around items are ignored, so `lo, eth0` and `"lo,eth0"` are equivalent.

---

### `[drive]`: drive filtering

Controls which block devices appear in the Temperature and Disk I/O charts. Device names must match **exactly** as they appear in `/sys/block/` (e.g. `nvme0n1`, `mmcblk0`, `sda`); `mmcblk` does not match `mmcblk0`.

```ini
[drive]
    temp_ignore = mmcblk0, sda
    perf_ignore = mmcblk0
```

| Key | Default | Description |
|-----|---------|-------------|
| `temp_ignore` | *(none)* | Devices to exclude from the Temperature chart. Useful for devices that don't report a temperature (SD cards, RAID members). |
| `perf_ignore` | *(none)* | Devices to exclude from the Disk I/O chart. |

---

### `[network]`: network interface filtering

Controls which network interfaces appear in the Network I/O chart. Interface names must match exactly as listed in `/sys/class/net/`.

```ini
[network]
    device_ignore = lo, eth0
```

| Key | Default | Description |
|-----|---------|-------------|
| `device_ignore` | *(none)* | Interfaces to exclude. Typically `lo` and any unused physical ports (for example `eth0` on a Pi that only uses Wi-Fi, or on the Argon ONE UP, where `eth0` is not connected). |

---

### `[cooling]`: case fan (optional)

Enables monitoring of a case fan connected to a GPIO pin via its tachometer wire. It appears as a second line on the Fan Speed chart, so it is only shown on a Raspberry Pi 5 / CM5. If this key is absent, only the CPU fan is shown.

```ini
[cooling]
    casefan = 18
```

| Key | Default | Description |
|-----|---------|-------------|
| `casefan` | *(disabled)* | BCM GPIO pin number (0–27) connected to the fan tachometer. An invalid value, or a pin that can't be set up (e.g. already in use), is ignored with a message on the console and only the CPU fan is shown. The pin uses a pull-up and a falling-edge interrupt; RPM is calculated from the time between pulses, assuming 2 pulses per revolution. Pulses less than 5 ms apart are ignored as noise, and the reading drops to 0 if no pulse arrives for 1 second (fan stopped). |

The GPIO pin is released when the window closes.

---

### `[smartctl]`: per-drive smartctl options (optional)

Some drives need a `smartctl` device type to be read correctly; for example, drives behind a USB-SATA bridge need `-d sat`. Add one entry per drive that needs it, or choose the type in **File → Settings**.

```ini
[smartctl]
    sda = -d sat
    sdb = -d sat
```

| Key | Default | Description |
|-----|---------|-------------|
| `<device>` | *(none)* | The key is the device name (e.g. `sda`). The value must be `-d TYPE` (`-dTYPE` and `--device=TYPE` also work), giving `sudo smartctl -d sat -A /dev/sda`. |

Without an entry, the command is `sudo smartctl -A /dev/<device>`. Because `smartctl` runs as root, any other option (for example `-s`, `-t` or `--set`, which change drive settings) is ignored with a warning, and the command runs without it.

## Testing

```bash
sudo apt install python3-pytest python3-pytestqt python3-hypothesis   # or: pip install -r requirements-dev.txt
python3 -m pytest
```

The tests never touch real hardware. Kernel files come from a temporary fake `/proc` and `/sys` built from recorded data in `tests/fixtures/`, `smartctl` and `RPi.GPIO` are replaced with fakes, and Qt runs headless. So the suite also runs on a machine that isn't a Raspberry Pi.

| File | Covers |
|------|--------|
| `test_systemsupport.py` | CPU %, drive and network discovery and deltas (including counter resets and failed reads), `smartctl` parsing for NVMe / SATA / USB-bridge / SD card, CPU model, temperature, and fan |
| `test_configfile.py` | values, lists, defaults, unparseable files, and the deliberate duplicate key in `tests/fixtures/test.ini` |
| `test_fanspeed.py` | RPM calculation, bounce rejection, stopped-fan timeout, GPIO setup and release |
| `test_scaling.py` | unit tiers and Y-axis sizing for the disk and network charts |
| `test_monitor_window.py` | the main window: case fan setup (including bad pins), drive filters, collapsing charts by their title, plot height at the minimum window size, saved view settings and their migration from oneUpMon, migrating `/etc/sysmon.ini`, rebuilding the window after File → Settings (releasing the case fan's GPIO pin first), and the Help → Repository link |
| `test_icons.py` | every icon size exists at its stated size, and the window icon loads them |
| `test_autoconfig.py` | built-in defaults: root disk detection (NVMe, SD, whole disk), Argon ONE UP detection by keyboard or display (and not on a Pi 5 or a plain CM5); writing and reading back settings, including empty lists overriding defaults |
| `test_version.py` | the version comes from `debian/changelog` and matches what dpkg builds the package with |
| `test_userconfig.py` | the per-user settings path, one-time migration from `/etc/sysmon.ini`, and atomic saving |
| `test_settingsdialog.py` | the File → Settings form: showing and editing settings, keeping settings for absent devices, rejecting invalid `smartctl` types, Reset to defaults |
| `test_rolling_chart_properties.py` | [hypothesis](https://hypothesis.readthedocs.io/) property tests: random traffic must always display correctly and stay on-screen (skipped if hypothesis isn't installed) |

Linting and type checking use the project's `.pylintrc` and `mypy.ini`; both currently report no issues:

```bash
pylint src/*.py tests/*.py icons/*.py
mypy src/*.py tests/*.py icons/*.py
```

To run the tests, pylint, and mypy in one go (extra arguments are passed to pytest):

```bash
./run_tests.sh
```
