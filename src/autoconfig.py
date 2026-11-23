#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

"""
Work out sysmon's default settings from the hardware, and read/write settings.

At every start sysmon layers the user's settings file (see ``userconfig``) over
``render(detect())``, so anything the user hasn't set follows these rules.  The
Settings dialog edits a ``Settings`` and saves it with ``render``.

Rules:
  * ``lo`` is always hidden from the Network I/O chart.
  * If the root filesystem is on an NVMe drive, ``mmcblk0`` (SD card / eMMC) is
    hidden from both the Temperature and Disk I/O charts.
  * On an Argon ONE UP laptop, ``eth0`` is hidden: the CM5 module has an
    Ethernet controller and PHY, but the ONE UP doesn't wire them to a port.

Everything is read through ``systemsupport.PROC_ROOT`` / ``SYS_ROOT``, so the
tests can point it at a fake /proc and /sys.  Nothing here raises: a file that
can't be read simply means "not detected".

Usage:  python3 autoconfig.py                 print the defaults as a settings file
        python3 autoconfig.py --write PATH    write them to PATH (never overwrites)
"""
import argparse
import glob
import os
import sys
from dataclasses import dataclass, field
from datetime import date

import systemsupport
from configfile import ConfigClass

# Built-in hardware of the Argon ONE UP, as seen on a production unit (2026).
# The CM5 module alone can't be told apart from other carrier boards, so a
# ONE UP is a CM5 with its internal keyboard/touchpad or its internal panel.
ONEUP_KEYBOARD_IDS = {("6080", "8060"), ("6080", "8061")}   # USB vendor:product, "AMIRA-KEYBOAR"
ONEUP_PANEL_EDID = ("BOE", 0x088B)                          # EDID manufacturer, product ("ASL_1920_1200")


# Highest GPIO pin on the 40-pin header (BCM numbering), for [cooling] casefan.
MAX_BCM_PIN = 27


@dataclass
class Settings:
    '''
    sysmon's settings: what to hide, the optional case fan pin, and per-drive
    smartctl device types.  ``reasons`` explains generated defaults.
    '''
    temp_ignore: list[str] = field(default_factory=list)
    perf_ignore: list[str] = field(default_factory=list)
    device_ignore: list[str] = field(default_factory=list)
    casefan: int | None = None
    smartctl: dict[str, str] = field(default_factory=dict)      # drive -> device type, e.g. "sat"
    reasons: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, cfg: ConfigClass) -> "Settings":
        '''Read settings back from a loaded ``ConfigClass``; invalid values are dropped.'''
        result = cls(temp_ignore=cfg.getValueAsList("drive", "temp_ignore"),
                     perf_ignore=cfg.getValueAsList("drive", "perf_ignore"),
                     device_ignore=cfg.getValueAsList("network", "device_ignore"))
        pin = cfg.getValue("cooling", "casefan", "")
        if pin.isdigit() and int(pin) <= MAX_BCM_PIN:
            result.casefan = int(pin)
        if cfg.config.has_section("smartctl"):
            for drive, value in cfg.config.items("smartctl", raw=True):
                args = systemsupport.smartctl_device_args(value.replace('"', '').strip())
                if args is not None:
                    result.smartctl[drive] = args[1]
        return result


def _read(path: str) -> str | None:
    '''Contents of a small text file, stripped, or None if it can't be read.'''
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        return None


def root_disk() -> str | None:
    '''
    Name of the disk holding the root filesystem (e.g. ``nvme0n1``), or None.

    Uses the device number from mountinfo rather than the source path, so it
    also works when the root is shown as /dev/root or mounted by PARTUUID.
    '''
    mountinfo = _read(f"{systemsupport.PROC_ROOT}/self/mountinfo")
    if mountinfo is None:
        return None
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) > 4 and fields[4] == "/":
            devnum = fields[2]                       # "MAJ:MIN"
            break
    else:
        return None
    block = os.path.realpath(f"{systemsupport.SYS_ROOT}/dev/block/{devnum}")
    if not os.path.isdir(block):
        return None
    if os.path.exists(os.path.join(block, "partition")):
        block = os.path.dirname(block)               # partition -> its disk
    return os.path.basename(block)


def is_compute_module_5() -> bool:
    '''True if /proc/cpuinfo's Model line names a Compute Module 5.'''
    cpuinfo = _read(f"{systemsupport.PROC_ROOT}/cpuinfo") or ""
    return any("Compute Module 5" in line for line in cpuinfo.splitlines() if line.startswith("Model"))


def has_oneup_keyboard() -> bool:
    '''True if the ONE UP's built-in keyboard/touchpad is on the USB bus.'''
    for dev in glob.glob(f"{systemsupport.SYS_ROOT}/bus/usb/devices/*"):
        ids = (_read(f"{dev}/idVendor"), _read(f"{dev}/idProduct"))
        if ids in ONEUP_KEYBOARD_IDS:
            return True
    return False


def edid_id(edid: bytes) -> tuple[str, int] | None:
    '''(manufacturer, product code) from an EDID block, or None if too short.'''
    if len(edid) < 128:
        return None
    packed = (edid[8] << 8) | edid[9]
    manufacturer = "".join(chr(((packed >> shift) & 0x1F) + ord("A") - 1) for shift in (10, 5, 0))
    product = edid[10] | (edid[11] << 8)
    return manufacturer, product


def has_oneup_panel() -> bool:
    '''True if a connected display reports the ONE UP's built-in panel EDID.'''
    for path in glob.glob(f"{systemsupport.SYS_ROOT}/class/drm/*/edid"):
        try:
            with open(path, "rb") as f:
                if edid_id(f.read()) == ONEUP_PANEL_EDID:
                    return True
        except OSError:
            continue
    return False


def is_argon_oneup() -> str | None:
    '''If this is an Argon ONE UP, return what identified it; otherwise None.'''
    if not is_compute_module_5():
        return None
    if has_oneup_keyboard():
        return "built-in keyboard"
    if has_oneup_panel():
        return "built-in display"
    return None


def detect() -> Settings:
    '''Apply the rules (see the module docstring) to this machine.'''
    result = Settings(device_ignore=["lo"])
    disk = root_disk()
    if disk is not None and disk.startswith("nvme"):
        result.temp_ignore.append("mmcblk0")
        result.perf_ignore.append("mmcblk0")
        result.reasons.append(f"root filesystem is on {disk}, so mmcblk0 is hidden")
    oneup = is_argon_oneup()
    if oneup:
        result.device_ignore.append("eth0")
        result.reasons.append(f"Argon ONE UP detected ({oneup}), so its unconnected eth0 is hidden")
    return result


def render(settings: Settings) -> str:
    '''
    The settings as sysmon.ini text.  Empty lists are written as ``key =``
    rather than left out, so they override the built-in defaults.
    '''
    if settings.reasons:
        found = "".join(f"#   - {r}\n" for r in settings.reasons)
        header = f"# Generated by sysmon on {date.today().isoformat()}; detected:\n{found}"
    else:
        header = f"# Written by sysmon on {date.today().isoformat()}.\n"
    casefan = (f"[cooling]\n    casefan = {settings.casefan}" if settings.casefan is not None
               else "#[cooling]\n#    casefan = 18")
    if settings.smartctl:
        smartctl = "[smartctl]\n" + "\n".join(f"    {drive} = -d {dev_type}"
                                                for drive, dev_type in sorted(settings.smartctl.items()))
    else:
        smartctl = "#[smartctl]\n#    sda = -d sat"
    return f"""\
#
# sysmon settings for this user.  Edit them with File -> Settings in sysmon,
# or by hand.  Anything not set here falls back to sysmon's built-in
# defaults, which are worked out from the hardware at every start.
#
{header}#

#
# Drives to leave off the Temperature chart (temp_ignore) and the Disk I/O
# chart (perf_ignore).  SD cards and eMMC (mmcblk0) have no temperature
# sensor.  Use exact device names as listed in /sys/block.
#
[drive]
    temp_ignore = {", ".join(settings.temp_ignore)}
    perf_ignore = {", ".join(settings.perf_ignore)}

#
# Network interfaces to leave off the Network I/O chart, such as the
# loopback interface (lo) or an unused port.  Use exact interface names
# as listed in /sys/class/net.
#
[network]
    device_ignore = {", ".join(settings.device_ignore)}

#
# A case fan with a tachometer on a GPIO pin (BCM numbering, 0-27).
# Only shown on a Raspberry Pi 5 / CM5.
#
{casefan}

#
# smartctl device type for drives that need one (for example "-d sat" for
# a USB-SATA bridge).  Only "-d TYPE" is accepted.
#
{smartctl}
"""


def main(argv: list[str] | None = None) -> int:
    '''
    Print the default settings for this machine, or write them to a new file.

    Parameters:
        argv - Command-line arguments (default: sys.argv[1:]).

    Returns:
        The exit status: 0, or 1 if the file couldn't be written.
    '''
    parser = argparse.ArgumentParser(description="Generate a sysmon.ini for this machine.")
    parser.add_argument("--write", metavar="PATH",
                        help="create PATH with the generated settings (an existing file is left alone)")
    args = parser.parse_args(argv)

    text = render(detect())
    if args.write is None:
        print(text, end="")
        return 0
    try:
        with open(args.write, "x", encoding="utf-8") as f:
            f.write(text)
    except FileExistsError:
        print(f"{args.write} already exists; not changed")
        return 0
    except OSError as error:
        print(f"cannot write {args.write}: {error}", file=sys.stderr)
        return 1
    os.chmod(args.write, 0o644)
    print(f"wrote {args.write}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
