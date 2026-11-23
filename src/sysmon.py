#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Application that monitors current CPU and Drive temp, along with fan speed and IO utilization
Requires: PyQt6 (including QtCharts)

"""

import math
import os
import sys

from PyQt6.QtCore import Qt, QTimer, QUrl, QSettings
from PyQt6.QtGui import QPalette, QColor, QAction, QDesktopServices
from PyQt6.QtWidgets import (QApplication, QDialog, QDialogButtonBox,
                              QHBoxLayout, QLabel, QMainWindow, QMenu, QMenuBar, QMessageBox,
                              QTabWidget,
                              QVBoxLayout, QWidget, QGridLayout)

from systemsupport import CPUInfo, CPULoad, multiDriveStat, NetworkLoad
from configfile import ConfigClass
from fanspeed import GetCaseFanSpeed
from charts import DATA_WINDOW, RollingChart, RollingChartDynamic, CollapsibleChart
from appicon import app_icon

# --------------------------
# Globals
# --------------------------

MIN_WIDTH   = 1000
MIN_HEIGHT  = 800

APP_NAME = "sysmon"
REPO_URL = "https://github.com/JeffCurless/systemmonitor"
OLD_APP_NAME = "oneUpMon"    # name before the rename; its settings are migrated once

MAX_BCM_PIN = 27    # highest GPIO pin on the 40-pin header (BCM numbering)


def apply_dark_palette(app: QApplication) -> None:
    '''Apply a dark colour palette to the entire Qt application.'''
    palette = QPalette()
    dark   = QColor(30,  30,  30)
    mid    = QColor(45,  45,  45)
    light  = QColor(220, 220, 220)
    accent = QColor(85,  153, 221)
    palette.setColor(QPalette.ColorRole.Window,          dark)
    palette.setColor(QPalette.ColorRole.WindowText,      light)
    palette.setColor(QPalette.ColorRole.Base,            QColor(39, 39, 39))
    palette.setColor(QPalette.ColorRole.AlternateBase,   mid)
    palette.setColor(QPalette.ColorRole.ToolTipBase,     QColor(255, 255, 220))
    palette.setColor(QPalette.ColorRole.ToolTipText,     QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Text,            light)
    palette.setColor(QPalette.ColorRole.Button,          mid)
    palette.setColor(QPalette.ColorRole.ButtonText,      light)
    palette.setColor(QPalette.ColorRole.BrightText,      QColor(255, 80, 80))
    palette.setColor(QPalette.ColorRole.Link,            accent)
    palette.setColor(QPalette.ColorRole.Highlight,       accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, dark)
    app.setPalette(palette)


class MonitorWindow(QMainWindow):
    '''
    Creating a window to monitor various system aspects.

    Parameters:
        refresh_ms  - Time between refreshes of data on screen, in milliseconds, the
                      default is 1 second.
        window      - How much data do we want to store in the graph?  Each data point
                      is a data refresh period.
        Parent      - Owning parent of this window... default is None.
    '''
    def __init__(self, refresh_ms: int = 1000, keepWindow: int = DATA_WINDOW, parent: QWidget | None = None,
                 config_path: str = "/etc/sysmon.ini"):
        super().__init__(parent)

        self.settings = QSettings(APP_NAME, APP_NAME)
        self._migrate_old_settings()

        # Get all the filters loaded
        self.config = ConfigClass(config_path)
        self.driveTempFilter = self.config.getValueAsList( 'drive', 'temp_ignore' )
        self.drivePerfFilter = self.config.getValueAsList( 'drive', 'perf_ignore' )

        # Get supporting objects
        self.cpuinfo    = CPUInfo()
        self.cpuload    = CPULoad()
        self.caseFan = self._open_case_fan(self.config.getValue( 'cooling', 'casefan', None ))
        self.multiDrive = multiDriveStat()

        self.setWindowTitle("System Monitor")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)

        central = QWidget(self)
        grid = QGridLayout(central)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self.setCentralWidget(central)

        # Charts
        self.use_chart = RollingChart(
            title="CPU Utilization",
            series_defs=[ (name, None) for name in self.cpuload.cpuNames ],
            y_min=0, y_max=100,
            window=keepWindow
            )

        series = [("CPU", None)]
        for name in self.multiDrive.drives:
            if not name in self.driveTempFilter:
                series.append( (name,None) )

        self.cpu_chart = RollingChart(
            title="Temperature (°C)",
            series_defs= series,
            y_min=20, y_max=80,
            window=keepWindow
            )

        self.fan_chart: RollingChart | None = None
        if self.cpuinfo.model == 5:
            if self.caseFan is None:
                series = [("CPU Fan",None)]
            else:
                series = [("CPU Fan",None),("Case Fan",None)]

            self.fan_chart = RollingChart(
                title="Fan Speed",
                series_defs=series,
                y_min=0,y_max=6000,
                window=keepWindow
            )

        series = []
        for name in self.multiDrive.drives:
            if not name in self.drivePerfFilter:
                series.append( (f"{name} Read", None) )
                series.append( (f"{name} Write", None ) )

        self.io_chart = RollingChartDynamic(
            title="Disk I/O",
            series_defs=series,
            range_y=[("Bytes/s", 1),("KiB/s", 1024),("MiB/s", 1024*1024),("GiB/s", 1024*1024*1024)],
            window=keepWindow,
        )

        self.networkFilter   = self.config.getValueAsList( 'network', 'device_ignore')
        self.network = NetworkLoad(self.networkFilter)
        series = []
        for name in self.network.names:
            series.append( (f"{name} Read", None) )
            series.append( (f"{name} Write", None) )

        self.network_chart = RollingChartDynamic(
            title="Network I/O",
            series_defs=series,
            range_y=[("Bytes/s", 1),("KiB/s", 1024),("MiB/s", 1024*1024),("GiB/s", 1024*1024*1024)],
            window=keepWindow,
        )

        # Layout: wrap each chart in a CollapsibleChart header, then grid.
        # Restore expand/collapse state from settings.
        def make_section(chart, key):
            def on_toggle(expanded, k=key):
                self.settings.setValue(f"expanded/{k}", expanded)
            return CollapsibleChart(chart, on_toggle=on_toggle, initial_expanded=True)

        self.use_section     = make_section(self.use_chart,     'cpu_utilization')
        self.io_section      = make_section(self.io_chart,      'disk_io')
        self.network_section = make_section(self.network_chart, 'network_io')
        self.cpu_section     = make_section(self.cpu_chart,     'temperature')

        # Give every legend the width of the widest one, so the stacked charts'
        # plots (and so their time axes) end at the same x position.
        charts = [c for c in (self.use_chart, self.io_chart, self.network_chart,
                              self.cpu_chart, self.fan_chart) if c is not None]
        legend_width = math.ceil(max(c.legendWidth() for c in charts))
        for c in charts:
            c.setLegendWidth(legend_width)

        # Row 3: temperature + optional fan side-by-side in an HBox container
        row3 = QWidget()
        row3_layout = QHBoxLayout(row3)
        row3_layout.setContentsMargins(0, 0, 0, 0)
        row3_layout.setSpacing(8)
        row3_layout.addWidget(self.cpu_section)
        if self.fan_chart:
            self.fan_section = make_section(self.fan_chart, 'fan_speed')
            row3_layout.addWidget(self.fan_section)
        else:
            self.fan_section = None

        grid.addWidget(self.use_section,     0, 0, 1, 2)
        grid.addWidget(self.io_section,      1, 0, 1, 2)
        grid.addWidget(self.network_section, 2, 0, 1, 2)
        grid.addWidget(row3,                 3, 0, 1, 2)

        # ── Menu bar ──────────────────────────────────────────────────────────────
        menubar = self.menuBar()
        assert menubar is not None   # QMainWindow creates its menu bar on demand

        file_menu = self._add_menu(menubar, '&File')
        close_act = QAction('&Close', self)
        close_act.setShortcut('Ctrl+Q')
        close_act.triggered.connect(self.close)
        file_menu.addAction(close_act)

        view_menu = self._add_menu(menubar, '&View')

        def make_toggle(section, chart, key):
            def _toggle(checked):
                self.settings.setValue(f"visible/{key}", checked)
                if checked:
                    if chart is not None:
                        chart.clear()
                    section.setExpanded(True)
                    self.settings.setValue(f"expanded/{key}", True)
                    section.setVisible(True)
                else:
                    section.setVisible(False)
            return _toggle

        # Collect (section, visible, expanded) to apply after first paint.
        # Applying visibility/collapse before the chart has been painted causes
        # QChartView to skip initialising its axes and legend rendering.
        deferred_state = []

        for label, section, chart, key in [
            ('CPU &Utilization', self.use_section,     self.use_chart,     'cpu_utilization'),
            ('&Disk I/O',        self.io_section,      self.io_chart,      'disk_io'),
            ('&Network I/O',     self.network_section, self.network_chart, 'network_io'),
            ('&Temperature',     self.cpu_section,     self.cpu_chart,     'temperature'),
        ]:
            visible  = self.settings.value(f"visible/{key}",  True, type=bool)
            expanded = self.settings.value(f"expanded/{key}", True, type=bool)
            deferred_state.append((section, visible, expanded))
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(visible)
            act.triggered.connect(make_toggle(section, chart, key))
            view_menu.addAction(act)

        if self.fan_section:
            visible  = self.settings.value("visible/fan_speed",  True, type=bool)
            expanded = self.settings.value("expanded/fan_speed", True, type=bool)
            deferred_state.append((self.fan_section, visible, expanded))
            act = QAction('&Fan Speed', self)
            act.setCheckable(True)
            act.setChecked(visible)
            act.triggered.connect(make_toggle(self.fan_section, self.fan_chart, 'fan_speed'))
            view_menu.addAction(act)

        help_menu = self._add_menu(menubar, '&Help')
        help_act = QAction('&Configuration Help', self)
        help_act.triggered.connect(self._show_help)
        help_menu.addAction(help_act)
        repo_act = QAction('&Repository', self)
        repo_act.triggered.connect(lambda: QDesktopServices.openUrl(
            QUrl(REPO_URL)))
        help_menu.addAction(repo_act)
        about_act = QAction('&About', self)
        about_act.triggered.connect(self._show_about)
        help_menu.addAction(about_act)

        # Get the initial information from the system
        self.refresh_metrics()

        # Timer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_metrics)
        self.timer.start(refresh_ms)

        # Apply saved visibility/collapse state after the first paint so that
        # QChartView finishes initialising axes and legend before being hidden.
        def _apply_saved_state():
            for section, visible, expanded in deferred_state:
                section.setExpanded(expanded)
                section.setVisible(visible)
        QTimer.singleShot(0, _apply_saved_state)

    def _migrate_old_settings(self) -> None:
        '''
        Copy the saved chart layout from the app's old name (oneUpMon) the
        first time it runs as sysmon.  Existing sysmon settings always win,
        and the old file is left in place.
        '''
        if self.settings.allKeys():
            return
        old = QSettings(OLD_APP_NAME, OLD_APP_NAME)
        for key in old.allKeys():
            self.settings.setValue(key, old.value(key))
        self.settings.sync()

    @staticmethod
    def _add_menu(menubar: QMenuBar, title: str) -> QMenu:
        '''Add a menu to *menubar* (Qt's stubs allow None; it never is).'''
        menu = menubar.addMenu(title)
        assert menu is not None
        return menu

    @staticmethod
    def _open_case_fan(pin_setting: str | None) -> GetCaseFanSpeed | None:
        '''
        Start the optional case-fan tachometer from the ``[cooling] casefan``
        setting.  A bad setting, or a GPIO failure (pin in use, no permission),
        is reported and the case fan is left out instead of stopping startup.

        Returns:
            The tachometer reader, or None if no (usable) case fan is configured.
        '''
        if pin_setting is None:
            return None
        try:
            pin = int(pin_setting)
        except ValueError:
            print( f"Ignoring [cooling] casefan = {pin_setting!r}: not a GPIO pin number" )
            return None
        if not 0 <= pin <= MAX_BCM_PIN:
            print( f"Ignoring [cooling] casefan = {pin}: BCM GPIO pins are 0-{MAX_BCM_PIN}" )
            return None
        try:
            return GetCaseFanSpeed(pin)
        except (RuntimeError, ValueError, OSError) as error:
            print( f"Ignoring [cooling] casefan = {pin}: could not set up GPIO: {error}" )
            return None

    def closeEvent(self, event) -> None:
        '''Stop polling and release GPIO resources deterministically on exit.'''
        self.timer.stop()
        if self.caseFan is not None:
            self.caseFan.close()
        super().closeEvent(event)

    def _show_about(self):
        QMessageBox.about(
            self,
            'About sysmon',
            '<b>sysmon</b> — System Monitor<br><br>'
            'A graphical system monitor for <b>Raspberry Pi</b> based systems.<br><br>'
            'Displays live rolling charts for CPU utilization, disk I/O,<br>'
            'network I/O, temperatures, and fan speed — updating every<br>'
            'second with 60 seconds of history.<br><br>'
            'Configuration file: <tt>/etc/sysmon.ini</tt>'
        )

    def _show_help(self):
        dlg = QDialog(self)
        dlg.setWindowTitle('Configuration Help — /etc/sysmon.ini')
        dlg.resize(680, 420)
        dlg.setStyleSheet("""
            QDialog          { background: #1e1e1e; }
            QTabWidget::pane { border: 1px solid #444; background: #272727; }
            QTabBar::tab     { background: #2d2d2d; color: #999;
                               padding: 6px 18px; margin-right: 2px; }
            QTabBar::tab:selected { background: #272727; color: #e8e8e8;
                                    border-top: 2px solid #5599dd; }
            QLabel           { color: #cccccc; }
            QPushButton      { color: #cccccc; background: #3c3c3c;
                               border: 1px solid #555; padding: 4px 20px;
                               border-radius: 3px; }
            QPushButton:hover { background: #4a4a4a; }
        """)

        tabs = QTabWidget(dlg)

        def add_tab(tab_name, intro, params):
            # params: list of (key_syntax, description, example_or_None)
            page = QWidget()
            vbox = QVBoxLayout(page)
            vbox.setContentsMargins(16, 14, 16, 14)
            vbox.setSpacing(0)

            intro_lbl = QLabel(intro)
            intro_lbl.setWordWrap(True)
            intro_lbl.setStyleSheet(
                "color: #888; font-size: 11px; font-style: italic; margin-bottom: 12px;")
            vbox.addWidget(intro_lbl)

            for key, desc, example in params:
                key_lbl = QLabel(key)
                key_lbl.setStyleSheet(
                    "color: #e8e8e8; font-family: monospace; font-size: 13px; "
                    "font-weight: bold; margin-top: 12px;")
                vbox.addWidget(key_lbl)

                desc_lbl = QLabel(desc)
                desc_lbl.setWordWrap(True)
                desc_lbl.setStyleSheet(
                    "color: #aaaaaa; font-size: 11px; margin-left: 16px; margin-top: 3px;")
                vbox.addWidget(desc_lbl)

                if example:
                    ex_lbl = QLabel(example)
                    ex_lbl.setStyleSheet(
                        "color: #7ec8e3; font-family: monospace; font-size: 11px; "
                        "margin-left: 16px; margin-top: 5px; background: #2a2a3a; "
                        "padding: 4px 10px; border-radius: 3px;")
                    ex_lbl.setTextInteractionFlags(
                        Qt.TextInteractionFlag.TextSelectableByMouse)
                    vbox.addWidget(ex_lbl)

            vbox.addStretch()
            tabs.addTab(page, tab_name)

        add_tab(
            "[drive]",
            "Controls which block devices appear in charts. "
            "Values are comma-separated device names, exactly as listed in "
            "/sys/block (not prefixes): 'mmcblk0' hides the SD card, 'mmcblk' does not.",
            [
                ("temp_ignore = <devices>",
                 "Exclude these drives from the Temperature chart. "
                 "Use for devices with no thermal sensor: SD cards, USB storage, RAID members.",
                 "temp_ignore = mmcblk0, sda"),
                ("perf_ignore = <devices>",
                 "Exclude these drives from the Disk I/O chart.",
                 "perf_ignore = mmcblk0"),
            ],
        )

        add_tab(
            "[network]",
            "Controls which network interfaces appear in the Network I/O chart. "
            "Values are exact interface names (not prefixes).",
            [
                ("device_ignore = <interfaces>",
                 "Comma-separated interface names to exclude. "
                 "Typically 'lo' (loopback) and any unused physical ports.",
                 "device_ignore = lo, eth0"),
            ],
        )

        add_tab(
            "[cooling]",
            "Optional GPIO tachometer for a case fan. "
            "Omit this section entirely if no external fan is fitted.",
            [
                ("casefan = <BCM GPIO pin>",
                 "GPIO pin number (0-27) for the fan tachometer wire. "
                 "The RPM reading appears on the Fan Speed chart alongside the CPU fan "
                 "(Raspberry Pi 5 only). An invalid pin is ignored with a message.",
                 "casefan = 18"),
            ],
        )

        add_tab(
            "[smartctl]",
            "Pass extra flags to smartctl for specific drives. Required for drives behind "
            "USB-to-SATA bridges that need a SAT passthrough hint to report SMART data.",
            [
                ("<device> = <flags>",
                 "Key is the bare device name (without /dev/). "
                 "Value is appended to 'smartctl -A /dev/<device>'.",
                 "sda = -d sat"),
            ],
        )

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.close)

        outer = QVBoxLayout(dlg)
        outer.addWidget(tabs)
        outer.addWidget(buttons)

        dlg.exec()

    def refresh_metrics(self):
        '''
        Timer slot, called once per refresh period.  Each data source in
        ``_refresh_metrics`` catches the specific errors it is expected to
        raise; this wrapper is the last-resort guard, because PyQt6 aborts the
        whole application if an exception escapes a slot.
        '''
        try:
            self._refresh_metrics()
        except Exception as error:  # pylint: disable=broad-exception-caught
            print( f"Unexpected error refreshing metrics: {error!r}" )

    def _refresh_metrics(self):
        '''Collect one sample from every data source and append it to the charts.'''

        # Obtain the current fan speed
        fan_speed: list[float | None]
        if self.cpuinfo.model == 5:
            try:
                if self.caseFan is not None:
                    fan_speed = [self.cpuinfo.CPUFanSpeed,self.caseFan.RPM]
                else:
                    fan_speed = [self.cpuinfo.CPUFanSpeed]
            except (OSError, ValueError) as e:
                print( f"error getting fan speed: {e}" )
                # One placeholder per fan series (1 without a case fan, 2 with)
                fan_speed = [None] * (1 if self.caseFan is None else 2)
        else:
            fan_speed = [None,None]

        # Setup the temperature for the CPU and Drives
        temperatures: list[float | None] = []
        try:
            temperatures.append( float(self.cpuinfo.temperature) )
        except (OSError, ValueError):
            temperatures.append( 0.0 )

        # Obtain the drive temperatures.  Each drive is handled separately so a
        # failure only zeroes that drive's series and the list stays aligned
        # with the chart's series (CPU first, then each non-filtered drive).
        for _drive in self.multiDrive.drives:
            if _drive in self.driveTempFilter:
                continue
            try:
                extraCmd = self.config.getValue( 'smartctl', _drive, None )
                temperatures.append( self.multiDrive.driveTemp( _drive, extraCmd ))
            except (OSError, ValueError) as e:
                print( f"error getting temperature for {_drive}: {e}" )
                temperatures.append( 0.0 )

        # Obtain the NVMe Device read and write rates
        try:
            rwData: list[float | None] = []
            drives = self.multiDrive.readWriteBytes()
            for drive, (read_bytes, write_bytes) in drives.items():
                if drive not in self.drivePerfFilter:
                    rwData.append( float(read_bytes))
                    rwData.append( float(write_bytes))
        except (IndexError, TypeError, ValueError):
            rwData = [ None, None ]

        # obtain network device read and writes rates
        try:
            netData: list[float | None] = []
            networks = self.network.stats
            for rx_bytes, tx_bytes in networks.values():
                netData.append( float(rx_bytes))
                netData.append( float(tx_bytes))
        except (KeyError, TypeError, ValueError):
            netData = [None,None]

        # Get the CPU load precentages
        values: list[float | None]
        try:
            p = self.cpuload.getPercentages()
            values = [p[name] for name in self.cpuload.cpuNames]
        except (OSError, KeyError, IndexError, ValueError):
            values = [ None for name in self.cpuload.cpuNames ]

        # Append to charts (skip hidden sections to pause data collection)
        if self.cpu_section.isVisible():
            self.cpu_chart.append(temperatures)
        if self.fan_chart and self.fan_section and self.fan_section.isVisible():
            self.fan_chart.append(fan_speed)
        if self.io_section.isVisible():
            self.io_chart.append(rwData)
        if self.network_section.isVisible():
            self.network_chart.append(netData)
        if self.use_section.isVisible():
            self.use_chart.append(values)

def main():
    '''Create the Qt application and monitor window, then run the event loop.'''
    os.environ['QT_QPA_PLATFORMTHEME'] = 'fusion'
    app = QApplication(sys.argv)
    apply_dark_palette(app)
    app.setWindowIcon(app_icon())
    w = MonitorWindow(refresh_ms=1000)
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
