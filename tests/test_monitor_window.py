"""
Tests that build the real MonitorWindow (headless) against fake hardware:
a fake /proc and /sys, a fake smartctl, a mock GPIO module, and a temporary
QSettings location so the user's saved layout is never touched.
"""
# pylint: disable=redefined-outer-name,missing-function-docstring,unused-argument,protected-access
from types import SimpleNamespace
from unittest import mock

import pytest
from PyQt6.QtCore import QPointF, QSettings, Qt

from conftest import REPO_ROOT, fixture_text

import fanspeed
import sysmon
import systemsupport


@pytest.fixture
def gpio(monkeypatch):
    '''A mock RPi.GPIO module installed into fanspeed.'''
    fake = mock.MagicMock(name="GPIO")
    monkeypatch.setattr(fanspeed, "GPIO", fake)
    return fake


@pytest.fixture
def make_window(fake_root, gpio, tmp_path, monkeypatch, qtbot):
    '''Factory: build a MonitorWindow on a fake Pi 5 using the given sysmon.ini text.'''
    fake_root.copy_fixture("proc/cpuinfo.cm5", "proc/cpuinfo")
    fake_root.copy_fixture("proc/stat.1", "proc/stat")
    for dev in ("mmcblk0", "nvme0n1"):
        fake_root.write(f"sys/block/{dev}/stat", " ".join(["0"] * 17) + "\n")
    for iface in ("lo", "wlan0"):
        fake_root.write(f"sys/class/net/{iface}/statistics/rx_bytes", "0\n")
        fake_root.write(f"sys/class/net/{iface}/statistics/tx_bytes", "0\n")
    fake_root.write("sys/devices/platform/cooling_fan/hwmon/hwmon5/fan1_input", "2500\n")

    nvme = fixture_text("smartctl/nvme0n1.txt")
    monkeypatch.setattr(systemsupport.subprocess, "run",
                        lambda cmd, **_: SimpleNamespace(stdout=nvme, returncode=0))
    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope,
                      str(tmp_path / "settings"))

    def make(ini_text: str = "", config_path=None):
        if config_path is None:
            config_path = tmp_path / "sysmon.ini"
            config_path.write_text(ini_text, encoding="utf-8")
        window = sysmon.MonitorWindow(config_path=str(config_path))
        qtbot.addWidget(window)          # closed (timer stopped, GPIO released) at teardown
        return window
    return make


def series_names(chart):
    assert chart is not None
    return [s.name() for s in chart.series]


# --------------------------------------------------------------------------
# [cooling] casefan
# --------------------------------------------------------------------------

def test_no_case_fan(make_window):
    window = make_window("")
    assert window.caseFan is None
    assert series_names(window.fan_chart) == ["CPU Fan"]


def test_valid_case_fan(make_window, gpio):
    window = make_window("[cooling]\ncasefan = 18\n")
    assert window.caseFan is not None
    assert series_names(window.fan_chart) == ["CPU Fan", "Case Fan"]
    gpio.setup.assert_called_once_with(18, gpio.IN, pull_up_down=gpio.PUD_UP)


@pytest.mark.parametrize("value", ["abc", "", "18.5", "-3"])
def test_invalid_case_fan_pin_does_not_crash(make_window, capsys, value):
    window = make_window(f"[cooling]\ncasefan = {value}\n")   # used to raise ValueError
    assert window.caseFan is None
    assert series_names(window.fan_chart) == ["CPU Fan"]
    assert "casefan" in capsys.readouterr().out                # the user is told why


def test_case_fan_gpio_failure_does_not_crash(make_window, gpio, capsys):
    # e.g. the pin is already in use, or GPIO access is not permitted
    gpio.add_event_detect.side_effect = RuntimeError("Failed to add edge detection")
    window = make_window("[cooling]\ncasefan = 18\n")
    assert window.caseFan is None
    assert series_names(window.fan_chart) == ["CPU Fan"]
    assert "Failed to add edge detection" in capsys.readouterr().out


def test_case_fan_reading_reaches_chart(make_window):
    window = make_window("[cooling]\ncasefan = 18\n")
    window.show()
    window.refresh_metrics()
    assert window.fan_chart is not None
    cpu_fan, case_fan = window.fan_chart.series
    assert cpu_fan.points()[-1].y() == 2500.0
    assert case_fan.points()[-1].y() == 0.0      # no tachometer pulses yet


# --------------------------------------------------------------------------
# [drive] filters: exact device names
# --------------------------------------------------------------------------

def test_drive_filters_use_exact_names(make_window):
    window = make_window("[drive]\ntemp_ignore = mmcblk0\nperf_ignore = mmcblk\n")
    assert series_names(window.cpu_chart) == ["CPU", "nvme0n1"]
    # "mmcblk" is not a prefix match, so mmcblk0 is still on the Disk I/O chart
    assert "mmcblk0 Read" in series_names(window.io_chart)


def test_sample_config_hides_sd_card_and_unused_interfaces(make_window):
    window = make_window(config_path=REPO_ROOT / "sysmon.ini")
    assert series_names(window.cpu_chart) == ["CPU", "nvme0n1"]
    assert series_names(window.io_chart) == ["nvme0n1 Read", "nvme0n1 Write"]
    assert series_names(window.network_chart) == ["wlan0 Read", "wlan0 Write"]


# --------------------------------------------------------------------------
# Collapsing charts by clicking their title
# --------------------------------------------------------------------------

def title_point(chart):
    '''A point (in view coordinates) on the chart's title, above the plot area.'''
    plot = chart.chart.plotArea()
    scene = chart.chart.mapToScene(QPointF(plot.center().x(), plot.top() / 2))
    return chart.view.mapFromScene(scene)


def plot_point(chart):
    '''A point (in view coordinates) in the middle of the plot area.'''
    scene = chart.chart.mapToScene(chart.chart.plotArea().center())
    return chart.view.mapFromScene(scene)


def test_clicking_title_collapses_and_bar_expands(make_window, qtbot):
    window = make_window("")
    window.show()
    section = window.io_section
    qtbot.waitExposed(window)
    assert window.io_chart.isVisible()
    assert not section._header.isVisible()        # no header strip while expanded

    qtbot.mouseClick(window.io_chart.view.viewport(), Qt.MouseButton.LeftButton,
                     pos=title_point(window.io_chart))
    assert not window.io_chart.isVisible()
    assert section._header.isVisible()            # the slim bar replaces the chart
    assert section._arrow.text() == "▶"
    assert window.settings.value("expanded/disk_io", type=bool) is False

    before = len(window.io_chart.series[0].points())
    window.refresh_metrics()                      # collapsed charts keep collecting
    assert len(window.io_chart.series[0].points()) == before + 1

    qtbot.mouseClick(section._header, Qt.MouseButton.LeftButton)
    assert window.io_chart.isVisible()
    assert not section._header.isVisible()
    assert window.settings.value("expanded/disk_io", type=bool) is True


def test_clicking_plot_area_does_not_collapse(make_window, qtbot):
    window = make_window("")
    window.show()
    qtbot.waitExposed(window)
    qtbot.mouseClick(window.use_chart.view.viewport(), Qt.MouseButton.LeftButton,
                     pos=plot_point(window.use_chart))
    assert window.use_chart.isVisible()


def test_title_is_drawn_in_chart_and_follows_unit_tier(make_window):
    window = make_window("")
    chart = window.io_chart
    assert chart.chart.title() == "▼ Disk I/O (Bytes/s)"
    assert window.use_chart.chart.title() == "▼ CPU Utilization"

    chart.append([5 * 1024 * 1024] * len(chart.series))   # 5 MiB/s forces a larger unit
    assert chart.scale.name != "Bytes/s"
    assert chart.chart.title() == f"▼ Disk I/O ({chart.scale.name})"
    assert window.io_section._title_label.text() == f"Disk I/O ({chart.scale.name})"


def test_stacked_charts_line_up(make_window, qtbot):
    '''Legends share one width, so the full-width charts' plots start and end together.'''
    window = make_window("")
    window.resize(sysmon.MIN_WIDTH, sysmon.MIN_HEIGHT)
    window.show()
    qtbot.waitExposed(window)
    stacked = (window.use_chart, window.io_chart, window.network_chart)
    widths = {round(c.chart.legend().geometry().width()) for c in stacked}
    rights = {round(c.chart.plotArea().right()) for c in stacked}
    assert len(widths) == 1
    assert len(rights) == 1


def test_plot_area_is_tall_enough_at_minimum_size(make_window, qtbot):
    '''At the smallest window size each plot must leave room for a readable Y scale.'''
    window = make_window("")
    window.resize(sysmon.MIN_WIDTH, sysmon.MIN_HEIGHT)
    window.show()
    qtbot.waitExposed(window)
    for chart in (window.use_chart, window.io_chart, window.network_chart, window.cpu_chart, window.fan_chart):
        assert chart is not None
        assert chart.chart.plotArea().height() >= 100, chart.title


# --------------------------------------------------------------------------
# Settings: renamed from oneUpMon to sysmon
# --------------------------------------------------------------------------

def test_settings_saved_under_new_name(make_window, tmp_path):
    window = make_window("")
    window.settings.setValue("visible/disk_io", False)
    window.settings.sync()
    assert (tmp_path / "settings" / "sysmon" / "sysmon.conf").exists()


def test_old_oneupmon_settings_are_migrated(make_window, tmp_path):
    old = tmp_path / "settings" / "oneUpMon" / "oneUpMon.conf"
    old.parent.mkdir(parents=True)
    old.write_text("[visible]\ndisk_io=false\n[expanded]\ntemperature=false\n", encoding="utf-8")
    window = make_window("")
    assert window.settings.value("visible/disk_io", True, type=bool) is False
    assert window.settings.value("expanded/temperature", True, type=bool) is False
    assert old.exists()                                 # the old file is left alone


def test_existing_sysmon_settings_win_over_old(make_window, tmp_path):
    for name, value in (("oneUpMon", "false"), ("sysmon", "true")):
        path = tmp_path / "settings" / name / f"{name}.conf"
        path.parent.mkdir(parents=True)
        path.write_text(f"[visible]\ndisk_io={value}\n", encoding="utf-8")
    window = make_window("")
    assert window.settings.value("visible/disk_io", type=bool) is True


def test_help_repository_opens_systemmonitor_repo(make_window, monkeypatch):
    opened = []
    monkeypatch.setattr(sysmon.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    window = make_window("")
    menubar = window.menuBar()
    assert menubar is not None
    help_menu = next(a.menu() for a in menubar.actions() if a.text() == "&Help")
    assert help_menu is not None
    repo = next(a for a in help_menu.actions() if a.text() == "&Repository")
    repo.trigger()
    assert opened == ["https://github.com/JeffCurless/systemmonitor"]
