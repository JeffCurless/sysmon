"""Tests for autoconfig.py, run against a fake /proc and /sys (see conftest)."""
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument
import os

import pytest

import autoconfig
from configfile import ConfigClass

# Model lines from /proc/cpuinfo.
CM5 = "Model\t\t: Raspberry Pi Compute Module 5 Lite Rev 1.0\n"
PI5 = "Model\t\t: Raspberry Pi 5 Model B Rev 1.0\n"


def set_root(fake_root, disk: str, partition: str | None, devnum: str) -> None:
    '''Make "/" live on <disk>[/<partition>] with device number *devnum*.'''
    fake_root.write("proc/self/mountinfo",
                    "22 1 0:21 / /proc rw - proc proc rw\n"
                    f"25 1 {devnum} / / rw,noatime - ext4 /dev/root rw\n")
    target = fake_root.mkdir(f"sys/devices/fake/block/{disk}")
    if partition is not None:
        target = fake_root.mkdir(f"sys/devices/fake/block/{disk}/{partition}")
        fake_root.write(f"sys/devices/fake/block/{disk}/{partition}/partition", "2\n")
    link = fake_root.root / "sys" / "dev" / "block" / devnum
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, link)


def add_usb(fake_root, name: str, vendor: str, product: str) -> None:
    fake_root.write(f"sys/bus/usb/devices/{name}/idVendor", vendor + "\n")
    fake_root.write(f"sys/bus/usb/devices/{name}/idProduct", product + "\n")


def make_edid(manufacturer: str, product: int) -> bytes:
    '''A 128-byte EDID block with just the ID fields filled in.'''
    a, b, c = (ord(ch) - ord("A") + 1 for ch in manufacturer)
    packed = (a << 10) | (b << 5) | c
    edid = bytearray(128)
    edid[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    edid[8], edid[9] = packed >> 8, packed & 0xFF
    edid[10], edid[11] = product & 0xFF, product >> 8
    return bytes(edid)


def add_display(fake_root, connector: str, edid: bytes) -> None:
    path = fake_root.root / "sys" / "class" / "drm" / connector / "edid"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(edid)


@pytest.fixture
def oneup(fake_root):
    '''A fake Argon ONE UP: CM5, NVMe root, built-in keyboard.'''
    fake_root.write("proc/cpuinfo", CM5)
    set_root(fake_root, "nvme0n1", "nvme0n1p2", "259:2")
    add_usb(fake_root, "1-1.7", "6080", "8060")
    return fake_root


# --------------------------------------------------------------------------
# Root disk
# --------------------------------------------------------------------------

@pytest.mark.parametrize("disk, partition, devnum", [
    ("nvme0n1", "nvme0n1p2", "259:2"),
    ("mmcblk0", "mmcblk0p2", "179:2"),
    ("sda", None, "8:0"),                     # filesystem on the whole disk
])
def test_root_disk(fake_root, disk, partition, devnum):
    set_root(fake_root, disk, partition, devnum)
    assert autoconfig.root_disk() == disk


def test_root_disk_unknown_without_mountinfo(fake_root):
    assert autoconfig.root_disk() is None


def test_root_disk_unknown_for_missing_device(fake_root):
    fake_root.write("proc/self/mountinfo", "25 1 0:99 / / rw - overlay overlay rw\n")
    assert autoconfig.root_disk() is None


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

def test_lo_always_ignored(fake_root):
    defaults = autoconfig.detect()
    assert defaults.device_ignore == ["lo"]
    assert not defaults.temp_ignore
    assert not defaults.perf_ignore


def test_nvme_root_hides_mmcblk0(fake_root):
    set_root(fake_root, "nvme0n1", "nvme0n1p2", "259:2")
    defaults = autoconfig.detect()
    assert defaults.temp_ignore == ["mmcblk0"]
    assert defaults.perf_ignore == ["mmcblk0"]
    assert any("nvme0n1" in r for r in defaults.reasons)


def test_sd_root_keeps_mmcblk0(fake_root):
    set_root(fake_root, "mmcblk0", "mmcblk0p2", "179:2")
    defaults = autoconfig.detect()
    assert "mmcblk0" not in defaults.temp_ignore + defaults.perf_ignore


# --------------------------------------------------------------------------
# Argon ONE UP detection
# --------------------------------------------------------------------------

@pytest.mark.parametrize("product", ["8060", "8061"])
def test_oneup_detected_by_keyboard(fake_root, product):
    fake_root.write("proc/cpuinfo", CM5)
    add_usb(fake_root, "1-1.6", "6080", product)
    assert autoconfig.is_argon_oneup() == "built-in keyboard"
    assert autoconfig.detect().device_ignore == ["lo", "eth0"]


def test_oneup_detected_by_panel(fake_root):
    fake_root.write("proc/cpuinfo", CM5)
    add_display(fake_root, "card1-HDMI-A-1", make_edid("ABC", 0x1234))
    add_display(fake_root, "card1-HDMI-A-2", make_edid("BOE", 0x088B))
    assert autoconfig.is_argon_oneup() == "built-in display"


def test_keyboard_on_a_pi5_is_not_a_oneup(fake_root):
    fake_root.write("proc/cpuinfo", PI5)
    add_usb(fake_root, "1-1", "6080", "8060")
    assert autoconfig.is_argon_oneup() is None
    assert "eth0" not in autoconfig.detect().device_ignore


def test_plain_cm5_is_not_a_oneup(fake_root):
    fake_root.write("proc/cpuinfo", CM5)
    add_usb(fake_root, "1-1", "046d", "c52b")
    add_display(fake_root, "card1-HDMI-A-1", make_edid("BOE", 0x0999))
    add_display(fake_root, "card1-HDMI-A-2", b"\x00" * 10)        # truncated EDID
    assert autoconfig.is_argon_oneup() is None


def test_edid_id_decodes_manufacturer_and_product():
    assert autoconfig.edid_id(make_edid("BOE", 0x088B)) == ("BOE", 0x088B)
    assert autoconfig.edid_id(b"short") is None


# --------------------------------------------------------------------------
# Rendering and writing
# --------------------------------------------------------------------------

def test_render_round_trips_through_configclass(oneup, tmp_path):
    cfg = ConfigClass(str(tmp_path / "missing.ini"))
    assert not cfg.loaded
    cfg.loadString(autoconfig.render(autoconfig.detect()))
    assert cfg.getValueAsList("drive", "temp_ignore") == ["mmcblk0"]
    assert cfg.getValueAsList("drive", "perf_ignore") == ["mmcblk0"]
    assert cfg.getValueAsList("network", "device_ignore") == ["lo", "eth0"]
    assert cfg.getValue("cooling", "casefan", None) is None


def test_render_comments_out_empty_lists(fake_root, tmp_path):
    cfg = ConfigClass(str(tmp_path / "missing.ini"))
    cfg.loadString(autoconfig.render(autoconfig.detect()))
    assert cfg.getValueAsList("drive", "temp_ignore") == []
    assert cfg.getValueAsList("network", "device_ignore") == ["lo"]


def test_write_creates_file(oneup, tmp_path, capsys):
    target = tmp_path / "sysmon.ini"
    assert autoconfig.main(["--write", str(target)]) == 0
    assert "device_ignore = lo, eth0" in target.read_text(encoding="utf-8")
    assert "wrote" in capsys.readouterr().out


def test_write_never_overwrites(oneup, tmp_path, capsys):
    target = tmp_path / "sysmon.ini"
    target.write_text("[network]\n    device_ignore = wlan0\n", encoding="utf-8")
    assert autoconfig.main(["--write", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == "[network]\n    device_ignore = wlan0\n"
    assert "already exists" in capsys.readouterr().out


def test_print_to_stdout(oneup, capsys):
    assert autoconfig.main([]) == 0
    assert "[network]" in capsys.readouterr().out
