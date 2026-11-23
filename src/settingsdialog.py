# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

"""
The File -> Settings dialog: a form over ``autoconfig.Settings``.

The form can only produce valid settings: drives and network interfaces are
checkboxes for the devices present now, the case fan pin is limited to 0-27,
and a smartctl device type must pass ``systemsupport.smartctl_device_args``
before Save is enabled.  Settings for devices that aren't present now (an
unplugged USB drive, say) are kept as they are.
"""
from collections.abc import Callable

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout,
                             QGroupBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget)

import autoconfig
from autoconfig import MAX_BCM_PIN, Settings
from systemsupport import smartctl_device_args

AUTO = "(auto)"
SMARTCTL_TYPES = [AUTO, "sat", "nvme", "usbjmicron", "usbcypress", "usbprolific", "usbsunplus", "scsi"]


class SettingsDialog(QDialog):
    '''
    Edit sysmon's settings.  After ``exec()`` returns Accepted, read the
    result with ``result_settings()``.

    Parameters:
        settings   - The current settings, shown when the dialog opens.
        drives     - Drives present now (unfiltered), e.g. ["mmcblk0", "nvme0n1"].
        interfaces - Network interfaces present now (unfiltered).
        defaults   - Called by "Reset to defaults"; normally ``autoconfig.detect``.
        parent     - Optional parent widget.
    '''
    def __init__(self, settings: Settings, drives: list[str], interfaces: list[str],
                 defaults: Callable[[], Settings] = autoconfig.detect,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("sysmon Settings")
        self._drives = drives
        self._interfaces = interfaces
        self._defaults = defaults
        self._loaded = settings

        layout = QVBoxLayout(self)

        # --- Drives ---
        drive_box = QGroupBox("Drives")
        grid = QGridLayout(drive_box)
        for col, heading in enumerate(["Drive", "Temperature", "Disk I/O", "smartctl type"]):
            label = QLabel(f"<b>{heading}</b>")
            grid.addWidget(label, 0, col)
        self.temp_boxes: dict[str, QCheckBox] = {}
        self.perf_boxes: dict[str, QCheckBox] = {}
        self.smartctl_combos: dict[str, QComboBox] = {}
        for row, drive in enumerate(drives, start=1):
            grid.addWidget(QLabel(drive), row, 0)
            self.temp_boxes[drive] = QCheckBox("show")
            self.perf_boxes[drive] = QCheckBox("show")
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(SMARTCTL_TYPES)
            combo.setToolTip("Device type passed to smartctl as '-d TYPE', e.g. sat for a USB-SATA "
                             "bridge. Leave as (auto) unless the drive's temperature reads 0.")
            combo.currentTextChanged.connect(self._validate)
            self.smartctl_combos[drive] = combo
            grid.addWidget(self.temp_boxes[drive], row, 1)
            grid.addWidget(self.perf_boxes[drive], row, 2)
            grid.addWidget(combo, row, 3)
        if not drives:
            grid.addWidget(QLabel("No drives found."), 1, 0, 1, 4)
        layout.addWidget(drive_box)

        # --- Network ---
        net_box = QGroupBox("Network I/O: interfaces to show")
        net_layout = QHBoxLayout(net_box)
        self.net_boxes: dict[str, QCheckBox] = {}
        for name in interfaces:
            self.net_boxes[name] = QCheckBox(name)
            net_layout.addWidget(self.net_boxes[name])
        if not interfaces:
            net_layout.addWidget(QLabel("No network interfaces found."))
        net_layout.addStretch(1)
        layout.addWidget(net_box)

        # --- Cooling ---
        fan_box = QGroupBox("Case fan (Raspberry Pi 5 / CM5 only)")
        fan_layout = QHBoxLayout(fan_box)
        self.fan_box = QCheckBox("Tachometer on GPIO pin (BCM)")
        self.fan_pin = QSpinBox()
        self.fan_pin.setRange(0, MAX_BCM_PIN)
        self.fan_box.toggled.connect(self.fan_pin.setEnabled)
        fan_layout.addWidget(self.fan_box)
        fan_layout.addWidget(self.fan_pin)
        fan_layout.addStretch(1)
        layout.addWidget(fan_box)

        # --- Status line and buttons ---
        self.status = QLabel()
        self.status.setStyleSheet("color: #ff8080;")
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel
                                   | QDialogButtonBox.StandardButton.RestoreDefaults)
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        reset = buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        assert save is not None and reset is not None
        self.save_button = save
        self.reset_button = reset
        reset.setText("Reset to defaults")
        reset.clicked.connect(self.reset_to_defaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load(settings)

    def _load(self, settings: Settings) -> None:
        '''Show *settings* in the form.'''
        self._loaded = settings
        for drive in self._drives:
            self.temp_boxes[drive].setChecked(drive not in settings.temp_ignore)
            self.perf_boxes[drive].setChecked(drive not in settings.perf_ignore)
            self.smartctl_combos[drive].setCurrentText(settings.smartctl.get(drive, AUTO))
        for name in self._interfaces:
            self.net_boxes[name].setChecked(name not in settings.device_ignore)
        self.fan_box.setChecked(settings.casefan is not None)
        self.fan_pin.setEnabled(settings.casefan is not None)
        self.fan_pin.setValue(settings.casefan if settings.casefan is not None else 0)
        self._validate()

    def reset_to_defaults(self) -> None:
        '''Replace the form's contents with the built-in hardware defaults.'''
        self._load(self._defaults())

    def _validate(self, _text: str = "") -> None:
        '''Enable Save only when every smartctl type is valid.'''
        bad = [drive for drive, combo in self.smartctl_combos.items()
               if combo.currentText().strip() not in ("", AUTO)
               and smartctl_device_args(f"-d {combo.currentText().strip()}") is None]
        for drive, combo in self.smartctl_combos.items():
            combo.setStyleSheet("color: #ff8080;" if drive in bad else "")
        self.status.setText(f"Invalid smartctl type for {', '.join(bad)}" if bad else "")
        self.save_button.setEnabled(not bad)

    def result_settings(self) -> Settings:
        '''The settings shown in the form, keeping entries for devices not present now.'''
        loaded = self._loaded
        absent_drive = [d for d in loaded.temp_ignore if d not in self._drives]
        result = Settings(
            temp_ignore=[d for d in self._drives if not self.temp_boxes[d].isChecked()] + absent_drive,
            perf_ignore=[d for d in self._drives if not self.perf_boxes[d].isChecked()]
                        + [d for d in loaded.perf_ignore if d not in self._drives],
            device_ignore=[n for n in self._interfaces if not self.net_boxes[n].isChecked()]
                          + [n for n in loaded.device_ignore if n not in self._interfaces],
            casefan=self.fan_pin.value() if self.fan_box.isChecked() else None,
            smartctl={d: t for d, t in loaded.smartctl.items() if d not in self._drives},
            reasons=list(loaded.reasons),
        )
        for drive, combo in self.smartctl_combos.items():
            dev_type = combo.currentText().strip()
            if dev_type not in ("", AUTO):
                result.smartctl[drive] = dev_type
        return result
