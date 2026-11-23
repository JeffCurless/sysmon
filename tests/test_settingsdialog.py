# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

"""Tests for the File -> Settings dialog (settingsdialog.py)."""
# pylint: disable=missing-function-docstring,unused-argument
from autoconfig import Settings
from settingsdialog import AUTO, SettingsDialog

DRIVES = ["mmcblk0", "nvme0n1"]
INTERFACES = ["eth0", "lo", "wlan0"]


def make_dialog(qtbot, settings=None, defaults=None):
    if settings is None:
        settings = Settings(temp_ignore=["mmcblk0"], perf_ignore=["mmcblk0"], device_ignore=["lo", "eth0"])
    dialog = SettingsDialog(settings, DRIVES, INTERFACES,
                            defaults=defaults or (lambda: Settings(device_ignore=["lo"])))
    qtbot.addWidget(dialog)
    return dialog


def test_form_shows_settings(qtbot):
    dialog = make_dialog(qtbot, Settings(temp_ignore=["mmcblk0"], device_ignore=["lo"],
                                         casefan=18, smartctl={"nvme0n1": "nvme"}))
    assert not dialog.temp_boxes["mmcblk0"].isChecked()
    assert dialog.perf_boxes["mmcblk0"].isChecked()
    assert dialog.temp_boxes["nvme0n1"].isChecked()
    assert not dialog.net_boxes["lo"].isChecked()
    assert dialog.net_boxes["eth0"].isChecked()
    assert dialog.fan_box.isChecked() and dialog.fan_pin.value() == 18
    assert dialog.smartctl_combos["nvme0n1"].currentText() == "nvme"
    assert dialog.smartctl_combos["mmcblk0"].currentText() == AUTO


def test_unchanged_form_returns_same_settings(qtbot):
    settings = Settings(temp_ignore=["mmcblk0"], perf_ignore=["mmcblk0"], device_ignore=["eth0", "lo"],
                        casefan=4, smartctl={"nvme0n1": "sat"})
    assert make_dialog(qtbot, settings).result_settings() == settings


def test_edits_produce_settings(qtbot):
    dialog = make_dialog(qtbot)
    dialog.net_boxes["eth0"].setChecked(True)          # show eth0
    dialog.net_boxes["wlan0"].setChecked(False)        # hide wlan0
    dialog.perf_boxes["mmcblk0"].setChecked(True)
    dialog.fan_box.setChecked(True)
    dialog.fan_pin.setValue(18)
    dialog.smartctl_combos["nvme0n1"].setCurrentText("sat")
    result = dialog.result_settings()
    assert result.device_ignore == ["lo", "wlan0"]
    assert not result.perf_ignore
    assert result.temp_ignore == ["mmcblk0"]
    assert result.casefan == 18
    assert result.smartctl == {"nvme0n1": "sat"}


def test_absent_devices_are_kept(qtbot):
    settings = Settings(temp_ignore=["sda"], perf_ignore=["sda"], device_ignore=["usb0"],
                        smartctl={"sda": "sat"})
    result = make_dialog(qtbot, settings).result_settings()
    assert "sda" in result.temp_ignore and "sda" in result.perf_ignore
    assert "usb0" in result.device_ignore
    assert result.smartctl == {"sda": "sat"}


def test_invalid_smartctl_type_disables_save(qtbot):
    dialog = make_dialog(qtbot)
    assert dialog.save_button.isEnabled()
    dialog.smartctl_combos["nvme0n1"].setCurrentText("sat -t long")
    assert not dialog.save_button.isEnabled()
    assert "nvme0n1" in dialog.status.text()
    dialog.smartctl_combos["nvme0n1"].setCurrentText("sat,12")
    assert dialog.save_button.isEnabled()


def test_reset_to_defaults(qtbot):
    dialog = make_dialog(qtbot, defaults=lambda: Settings(device_ignore=["lo"], reasons=["test"]))
    dialog.reset_button.click()
    assert dialog.net_boxes["eth0"].isChecked()
    assert not dialog.net_boxes["lo"].isChecked()
    assert dialog.temp_boxes["mmcblk0"].isChecked()
    assert dialog.result_settings().reasons == ["test"]
