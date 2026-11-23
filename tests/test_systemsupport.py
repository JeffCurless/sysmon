"""Tests for systemsupport.py, run against a fake /proc and /sys (see conftest)."""
# pylint: disable=redefined-outer-name,missing-function-docstring,unused-argument,protected-access
# (pytest fixtures are injected as same-named arguments; test names are their docs)
import subprocess
from types import SimpleNamespace

import pytest

from conftest import fixture_text

import systemsupport
from systemsupport import CPUInfo, CPULoad, DriveStats, NetworkLoad, multiDriveStat

# A real /sys/block/nvme0n1/stat line (17 fields) recorded on the CM5.
NVME_STAT = ("   39832    18252  2739839    19219    56729    54936  4723276   137214"
             "        0     9584   158133        0        0        0        0    24929     1699\n")


def stat_line(read_sectors: int, write_sectors: int) -> str:
    '''A /sys/block/<dev>/stat line with the given sector counters (fields 2 and 6).'''
    fields = [0] * 17
    fields[DriveStats.READ_SECTORS] = read_sectors
    fields[DriveStats.WRITE_SECTORS] = write_sectors
    return " ".join(f"{f:8d}" for f in fields) + "\n"


# --------------------------------------------------------------------------
# CPULoad
# --------------------------------------------------------------------------

def test_cpu_load_percentages_from_deltas(fake_root):
    # Fields: user nice system idle iowait irq softirq steal guest guest_nice.
    # Idle time = idle + iowait (fields 4 and 5).
    fake_root.write("proc/stat",
                    "cpu  200 0 200 1400 200 0 0 0 0 0\n"
                    "cpu0 100 0 100 700 100 0 0 0 0 0\n"     # total 1000, idle 800
                    "cpu1 100 0 100 700 100 0 0 0 0 0\n"
                    "intr 12345 0 0\n")
    load = CPULoad()
    assert load.cpuNames == ["cpu0", "cpu1"]   # aggregate "cpu" line excluded
    assert len(load) == 2

    # First call is measured from boot: (1000 - 800) / 1000
    assert load.getPercentages() == {"cpu0": 20.0, "cpu1": 20.0}

    fake_root.write("proc/stat",
                    "cpu0 150 0 150 1500 200 0 0 0 0 0\n"    # +1000 total, +900 idle
                    "cpu1 600 0 400 800 200 0 0 0 0 0\n")    # +1000 total, +200 idle
    assert load.getPercentages() == {"cpu0": 10.0, "cpu1": 80.0}


def test_cpu_load_no_elapsed_time_is_zero_percent(fake_root):
    fake_root.write("proc/stat", "cpu0 100 0 100 700 100 0 0 0 0 0\n")
    load = CPULoad()
    load.getPercentages()
    assert load.getPercentages() == {"cpu0": 0.0}   # identical snapshot, no divide-by-zero


def test_cpu_load_recorded_snapshots(fake_root):
    fake_root.copy_fixture("proc/stat.1", "proc/stat")
    load = CPULoad()
    load.getPercentages()
    fake_root.copy_fixture("proc/stat.2", "proc/stat")
    percentages = load.getPercentages()
    assert list(percentages) == ["cpu0", "cpu1", "cpu2", "cpu3"]
    assert all(0.0 <= p <= 100.0 for p in percentages.values())


# --------------------------------------------------------------------------
# Drives
# --------------------------------------------------------------------------

@pytest.fixture
def block_devices(fake_root):
    '''A /sys/block with real drives plus loop/ram/zram devices that must be skipped.'''
    for dev in ["loop0", "loop7", "ram0", "ram15", "zram0"]:
        fake_root.write(f"sys/block/{dev}/stat", stat_line(0, 0))
    fake_root.write("sys/block/mmcblk0/stat", stat_line(10, 20))
    fake_root.write("sys/block/nvme0n1/stat", NVME_STAT)
    fake_root.write("sys/block/nvme0n1/size", "976773168\n")
    fake_root.write("sys/block/sda/stat", stat_line(0, 0))
    return fake_root


def test_drive_discovery_skips_loop_and_ram(block_devices):
    assert multiDriveStat().drives == ["mmcblk0", "nvme0n1", "sda"]


def test_drive_discovery_ignore_list(block_devices):
    assert multiDriveStat(["sda", "mmcblk0"]).drives == ["nvme0n1"]


def test_drive_read_write_bytes_are_deltas(block_devices):
    drives = multiDriveStat(["mmcblk0", "sda"])
    # Counters as recorded: read 2739839, written 4723276 sectors.
    block_devices.write("sys/block/nvme0n1/stat", stat_line(2739839 + 100, 4723276 + 2048))
    assert drives.readWriteBytes() == {"nvme0n1": (100 * 512, 2048 * 512)}
    # No further I/O -> zero
    assert drives.readWriteBytes() == {"nvme0n1": (0, 0)}


def test_drive_stats_unreadable_reports_zero(block_devices):
    stats = DriveStats("nvme0n1")
    (block_devices.root / "sys/block/nvme0n1/stat").unlink()   # device vanished
    assert stats.readWriteBytes == (0, 0)


def test_drive_counter_reset(block_devices):
    # Counters restart from zero when a drive is unplugged and re-attached.
    drives = multiDriveStat(["mmcblk0", "sda"])
    block_devices.write("sys/block/nvme0n1/stat", stat_line(100, 300))
    assert drives.readWriteBytes() == {"nvme0n1": (100 * 512, 300 * 512)}
    block_devices.write("sys/block/nvme0n1/stat", stat_line(150, 300))
    assert drives.readWriteBytes() == {"nvme0n1": (50 * 512, 0)}


def test_drive_unreadable_at_startup_does_not_spike(fake_root):
    stats = DriveStats("sda")                       # no stat file yet
    fake_root.write("sys/block/sda/stat", stat_line(5_000_000, 9_000_000))
    assert stats.readWriteBytes == (0, 0)           # first good reading is only a baseline
    fake_root.write("sys/block/sda/stat", stat_line(5_000_010, 9_000_020))
    assert stats.readWriteBytes == (10 * 512, 20 * 512)


def test_drive_unreadable_mid_run_does_not_spike(block_devices):
    stats = DriveStats("nvme0n1")
    path = block_devices.root / "sys/block/nvme0n1/stat"
    path.unlink()
    assert stats.readWriteBytes == (0, 0)           # failed read
    block_devices.write("sys/block/nvme0n1/stat", stat_line(2739839 + 100, 4723276 + 100))
    assert stats.readWriteBytes == (0, 0)           # baseline re-established
    block_devices.write("sys/block/nvme0n1/stat", stat_line(2739839 + 104, 4723276 + 108))
    assert stats.readWriteBytes == (4 * 512, 8 * 512)


def test_drive_short_stat_line(fake_root):
    # Older kernels report 11 fields (no discard/flush counters).
    fake_root.write("sys/block/sda/stat", " ".join(["0"] * 11) + "\n")
    stats = DriveStats("sda")
    fake_root.write("sys/block/sda/stat", "1 0 8 0 1 0 16 0 0 0 0\n")
    assert stats.readAllStats[:7] == [1, 0, 8, 0, 1, 0, 16]
    assert stats.discardSectors == 0


def test_drive_size(block_devices):
    drives = multiDriveStat()
    assert drives.driveSize("nvme0n1") == 976773168 * 512
    assert drives.driveSize("doesnotexist") == 0


@pytest.fixture
def smart(fake_root):
    '''A multiDriveStat with no drives, for calling driveTemp() directly.'''
    fake_root.mkdir("sys/block")
    return multiDriveStat()


@pytest.fixture
def fake_smartctl(monkeypatch):
    '''Replace subprocess.run in systemsupport; records argv, returns canned stdout.'''
    calls = []

    def install(stdout="", exc=None):
        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            if exc is not None:
                raise exc
            return SimpleNamespace(stdout=stdout, returncode=0)
        monkeypatch.setattr(systemsupport.subprocess, "run", fake_run)
        return calls
    return install


@pytest.mark.parametrize("fixture, expected", [
    ("smartctl/nvme0n1.txt", 40.0),         # recorded: "Temperature:   40 Celsius"
    ("smartctl/sata_194.txt", 35.0),        # attribute 194 raw value "35 (Min/Max 18/48)"
    ("smartctl/usb_bridge_190.txt", 31.0),  # only attribute 190 present
    ("smartctl/mmcblk0.txt", 0.0),          # recorded: SD card, no SMART data
])
def test_drive_temp_parses_smartctl_output(smart, fake_smartctl, fixture, expected):
    fake_smartctl(fixture_text(fixture))
    assert smart.driveTemp("dev") == expected


def test_drive_temp_prefers_194_over_190(smart, fake_smartctl):
    # sata_194.txt has 190 = 35 and 194 = 35; make them differ to see which wins.
    text = fixture_text("smartctl/sata_194.txt").replace(
        "-       35 (Min/Max 18/48)", "-       42 (Min/Max 18/48)")
    fake_smartctl(text)
    assert smart.driveTemp("sda") == 42.0


def test_drive_temp_non_numeric_value_falls_through(smart, fake_smartctl):
    fake_smartctl("194 Temperature_Celsius     0x0022   035   048   000    Old_age   Always       -       -\n")
    assert smart.driveTemp("sda") == 0.0


def test_drive_temp_command_line(smart, fake_smartctl):
    calls = fake_smartctl("")
    smart.driveTemp("nvme0n1")
    smart.driveTemp("sda", "-d sat")
    assert calls == [["sudo", "smartctl", "-A", "/dev/nvme0n1"],
                     ["sudo", "smartctl", "-d", "sat", "-A", "/dev/sda"]]


def test_drive_temp_malformed_extra_flags(smart, fake_smartctl):
    calls = fake_smartctl("")
    assert smart.driveTemp("sda", '"unterminated') == 0.0
    assert not calls   # never launched


@pytest.mark.parametrize("exc", [
    FileNotFoundError("sudo"),
    subprocess.TimeoutExpired("smartctl", 5),
])
def test_drive_temp_launch_failure(smart, fake_smartctl, exc):
    fake_smartctl(exc=exc)
    assert smart.driveTemp("sda") == 0.0


# --------------------------------------------------------------------------
# CPUInfo
# --------------------------------------------------------------------------

@pytest.mark.parametrize("model_line, expected", [
    ("Raspberry Pi 5 Model B Rev 1.0", 5),
    ("Raspberry Pi Compute Module 5 Rev 1.0", 5),
    ("Raspberry Pi 4 Model B Rev 1.4", 4),
    ("Raspberry Pi Compute Module 4 Rev 1.0", 4),
    ("Raspberry Pi 3 Model B Plus Rev 1.3", 3),
    ("Some Other Board", 0),
])
def test_cpu_model(fake_root, model_line, expected):
    fake_root.write("proc/cpuinfo", f"processor\t: 0\nModel\t\t: {model_line}\n")
    assert CPUInfo().model == expected


def test_cpu_model_recorded_cm5(fake_root):
    fake_root.copy_fixture("proc/cpuinfo.cm5", "proc/cpuinfo")
    assert CPUInfo().model == 5


def test_cpu_model_missing_cpuinfo(fake_root):
    assert CPUInfo().model == 0


def test_cpu_model_detected_once(fake_root):
    fake_root.write("proc/cpuinfo", "Model\t: Raspberry Pi 5 Model B Rev 1.0\n")
    info = CPUInfo()
    fake_root.write("proc/cpuinfo", "Model\t: Raspberry Pi 4 Model B Rev 1.4\n")
    assert info.model == 5


def test_cpu_temperature(fake_root):
    fake_root.write("sys/class/thermal/thermal_zone0/temp", "47350\n")
    assert CPUInfo().temperature == pytest.approx(47.35)


THERMAL = "sys/class/thermal/thermal_zone0/temp"


def test_cpu_info_without_temperature_sensor(fake_root):
    (fake_root.root / THERMAL).unlink()
    fake_root.write("proc/cpuinfo", "Model\t: Raspberry Pi 5 Model B Rev 1.0\n")
    info = CPUInfo()                     # used to raise FileNotFoundError
    assert info.model == 5               # everything else still works
    with pytest.raises(OSError):
        _ = info.temperature             # callers already treat OSError as "no reading"


def test_cpu_temperature_sensor_appears_later(fake_root):
    (fake_root.root / THERMAL).unlink()
    info = CPUInfo()
    fake_root.write(THERMAL, "51200\n")
    assert info.temperature == pytest.approx(51.2)


def test_cpu_temperature_unparseable_at_startup(fake_root):
    fake_root.write(THERMAL, "garbage\n")
    info = CPUInfo()
    with pytest.raises((OSError, ValueError)):
        _ = info.temperature
    fake_root.write(THERMAL, "40000\n")
    assert info.temperature == pytest.approx(40.0)


FAN = "sys/devices/platform/cooling_fan/hwmon/hwmon5/fan1_input"


def test_cpu_fan_speed_pi5(fake_root):
    fake_root.write("proc/cpuinfo", "Model\t: Raspberry Pi 5 Model B Rev 1.0\n")
    fake_root.write(FAN, "2345\n")
    assert CPUInfo().CPUFanSpeed == 2345.0


def test_cpu_fan_speed_not_pi5(fake_root):
    fake_root.write("proc/cpuinfo", "Model\t: Raspberry Pi 4 Model B Rev 1.4\n")
    fake_root.write(FAN, "2345\n")
    assert CPUInfo().CPUFanSpeed == 0.0


@pytest.mark.parametrize("content", [None, "garbage\n"])
def test_cpu_fan_speed_unreadable(fake_root, content):
    fake_root.write("proc/cpuinfo", "Model\t: Raspberry Pi 5 Model B Rev 1.0\n")
    if content is not None:
        fake_root.write(FAN, content)
    assert CPUInfo().CPUFanSpeed == 0.0


# --------------------------------------------------------------------------
# NetworkLoad
# --------------------------------------------------------------------------

def set_counters(fake_root, iface, rx, tx):
    '''Write rx_bytes / tx_bytes for *iface*.'''
    fake_root.write(f"sys/class/net/{iface}/statistics/rx_bytes", f"{rx}\n")
    fake_root.write(f"sys/class/net/{iface}/statistics/tx_bytes", f"{tx}\n")


def test_network_discovery_and_ignore_list(fake_root):
    for iface in ["wlan0", "eth0", "lo", "wg0"]:
        set_counters(fake_root, iface, 0, 0)
    assert NetworkLoad().names == ["eth0", "lo", "wg0", "wlan0"]
    assert NetworkLoad(["lo", "eth0"]).names == ["wg0", "wlan0"]


def test_network_stats_are_deltas(fake_root):
    set_counters(fake_root, "wlan0", 1_000_000, 500_000)
    net = NetworkLoad()
    set_counters(fake_root, "wlan0", 1_250_000, 510_000)
    assert net.stats == {"wlan0": (250_000, 10_000)}
    assert net.stats == {"wlan0": (0, 0)}


def test_network_unreadable_at_startup_does_not_spike(fake_root):
    fake_root.mkdir("sys/class/net/wlan0")          # interface exists, counters unreadable
    net = NetworkLoad()
    set_counters(fake_root, "wlan0", 7_000_000_000, 3_000_000_000)
    assert net.stats == {"wlan0": (0, 0)}           # first good reading is only a baseline
    set_counters(fake_root, "wlan0", 7_000_001_000, 3_000_000_500)
    assert net.stats == {"wlan0": (1_000, 500)}


def test_network_unreadable_mid_run_does_not_spike(fake_root):
    set_counters(fake_root, "wlan0", 1_000_000, 1_000_000)
    net = NetworkLoad()
    (fake_root.root / "sys/class/net/wlan0/statistics/rx_bytes").write_text("garbage\n")
    assert net.stats == {"wlan0": (0, 0)}           # failed read
    set_counters(fake_root, "wlan0", 1_002_000, 1_001_000)
    assert net.stats == {"wlan0": (0, 0)}           # baseline re-established
    set_counters(fake_root, "wlan0", 1_002_300, 1_001_400)
    assert net.stats == {"wlan0": (300, 400)}


def test_network_counter_reset(fake_root):
    # Counters restart from zero when an interface is re-created (e.g. wg0
    # down/up); the delta must not go negative.
    set_counters(fake_root, "wg0", 9_000_000, 9_000_000)
    net = NetworkLoad()
    set_counters(fake_root, "wg0", 1_000, 2_000)   # came back with fresh counters
    assert net.stats == {"wg0": (1_000, 2_000)}    # bytes counted since the reset
    set_counters(fake_root, "wg0", 1_500, 2_000)
    assert net.stats == {"wg0": (500, 0)}          # normal deltas resume
