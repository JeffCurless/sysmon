#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Application that monitors current CPU and Drive temp, along with fan speed and IO utilization
Requires: PyQt6 (including QtCharts)

"""

import os
import sys
from collections.abc import Callable

from PyQt6.QtCore import Qt, QTimer, QUrl, QSettings
from PyQt6.QtGui import QPalette, QColor, QPainter, QAction, QDesktopServices, QIcon, QMouseEvent
from PyQt6.QtWidgets import (QApplication, QDialog, QDialogButtonBox,
                              QHBoxLayout, QLabel, QMainWindow, QMenu, QMenuBar, QMessageBox,
                              QSizePolicy, QSpacerItem, QTabWidget,
                              QVBoxLayout, QWidget, QGridLayout)
from PyQt6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis

from systemsupport import CPUInfo, CPULoad, multiDriveStat, NetworkLoad
from configfile import ConfigClass
from fanspeed import GetCaseFanSpeed

# --------------------------
# Globals
# --------------------------

MIN_WIDTH   = 1000
MIN_HEIGHT  = 800

DATA_WINDOW = 60

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


class RollingChart(QWidget):
    '''
    A reusable chart widget with one or more QLineSeries and a rolling X window.

    Parameters:
        title       - Chart title.
        series_defs - List of (name, color_qt_str or None) for each line.
        y_min,y_max - Fixed Y axis range.
        window      - Number of points to keep (points are 1 per tick by default).
    '''
    def __init__(self, title: str, series_defs: list[tuple], y_min: float, y_max: float,
                 window: int = DATA_WINDOW, parent: QWidget | None = None):
        super().__init__(parent)
        self.title = title
        self.pointWindow = window
        self.xpos   = window - 1
        self.chart  = QChart()
        self.chart.setTheme(QChart.ChartTheme.ChartThemeDark)

        legend = self.chart.legend()
        assert legend is not None   # a QChart always owns a legend
        legend.setVisible(len(series_defs) > 1)
        legend.setAlignment(Qt.AlignmentFlag.AlignBottom)
        legend.setMaximumHeight(36)
        legend_font = legend.font()
        legend_font.setPointSize(8)
        legend.setFont(legend_font)

        self.series:list[QLineSeries] = []
        for name, color in series_defs:
            s = QLineSeries()
            s.setName(name)
            if color:
                s.setColor(color)  # QColor or string like "#RRGGBB"
            self.series.append(s)
            self.chart.addSeries(s)

        # Setup X Axis... Note, setVisible disables all of this, however whatI
        # want is the tick count etc, but NO lable on the axis.  There does not
        # appear to be a way to do that.
        self.axis_x = QValueAxis()
        self.axis_x.setRange(0, self.pointWindow)
        self.axis_x.setMinorTickCount( 2 )
        self.axis_x.setTickCount( 10 )
        self.axis_x.setLabelFormat("%d")
        self.axis_x.setVisible(False)

        # Setup Y Axis...
        self.axis_y = QValueAxis()
        self.axis_y.setRange(y_min, y_max)
        self.axis_y.setLabelFormat( "%d" )

        self.chart.addAxis(self.axis_x, Qt.AlignmentFlag.AlignBottom)
        self.chart.addAxis(self.axis_y, Qt.AlignmentFlag.AlignLeft)

        for s in self.series:
            s.attachAxis(self.axis_x)
            s.attachAxis(self.axis_y)

        self.view = QChartView(self.chart)
        self.view.setRenderHints(QPainter.RenderHint.Antialiasing)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view, 0, 0)

    def append(self, values: list[float | None]):
        '''
        Append one sample (for each series) at the next x value. Handles rolling window.
        values must match the number of series.

        Parameters:
            values - One number per data series in the chart; None marks a
                    missing sample, which is skipped.
        '''
        self.xpos += 1
        for s, v in zip(self.series, values):
            # Handle NaN by skipping, or plot zero—here we clamp None/NaN to None and skip
            try:
                if v is None:
                    continue
                # If you want to clamp, do it here: v = max(self.axis_y.min(), min(self.axis_y.max(), v))
                s.append(self.xpos, float(v))
            except (TypeError, ValueError) as error:
                # ignore bad data points
                print( f"Bad data point {v!r}: {error}" )

        # Trim series to rolling window
        min_x_to_keep = max(0, self.xpos - self.pointWindow)
        self.axis_x.setRange(min_x_to_keep, self.xpos)

        for s in self.series:
            # Efficient trim: remove points with x < min_x_to_keep
            # QLineSeries doesn't provide O(1) pop from front, so we rebuild if large
            points = s.points()
            if points and points[0].x() < min_x_to_keep:
                # binary search for first index >= min_x_to_keep
                lo, hi = 0, len(points)
                while lo < hi:
                    mid = (lo + hi) // 2
                    if points[mid].x() < min_x_to_keep:
                        lo = mid + 1
                    else:
                        hi = mid
                s.replace(points[lo:])  # keep tail only

    def clear(self) -> None:
        '''Clear all series data and reset the rolling X window.'''
        self.xpos = self.pointWindow - 1
        self.axis_x.setRange(0, self.pointWindow)
        for s in self.series:
            s.clear()

class scaleValues:
    '''
    Tracks the current display scale tier for a dynamic-range chart.

    Each tier is a ``(label, divisor)`` pair — for example
    ``("KiB/s", 1024)`` — stored in *range_y* in ascending order.
    The chart calls ``nextScale`` when incoming values overflow the current
    tier, and ``prevScale`` when the window maximum falls below 1 (meaning
    the previous tier has enough resolution).
    '''
    def __init__( self, range_y: list[tuple[str, int]] ) -> None:
        '''
        Initialise at the lowest (first) scale tier.

        Parameters:
            range_y - Ordered list of ``(label, divisor)`` tuples, from the
                      smallest unit (e.g. ``("Bytes/s", 1)``) to the largest
                      (e.g. ``("GiB/s", 1024**3)``).
        '''
        self.index      = 0
        self.valueRange = range_y

    @property
    def scale( self ) -> int:
        '''
        Divisor for the current tier.

        Returns:
            Integer divisor to apply to raw byte values so they display in
            the current unit (e.g. 1024 when the tier is ``KiB/s``).
        '''
        return self.valueRange[self.index][1]

    def scaleValue(self, value: float) -> float:
        '''
        Convert a raw value to the current display unit.

        Parameters:
            value - Raw value (e.g. bytes per second).

        Returns:
            *value* divided by the current tier's divisor.
        '''
        return value / self.scale

    def nextScale( self ) -> bool:
        '''
        Advance to the next (larger) scale tier if one exists.

        Returns:
            True if the tier changed, False if already at the largest tier.
        '''
        if (self.index + 1) < len(self.valueRange):
            self.index += 1
            return True
        return False

    def prevScale( self ) -> None:
        '''Retreat to the previous (smaller) scale tier if one exists.'''
        if self.index > 0:
            self.index -= 1
        #print( f"Switches scale to {self.valueRange[self.index]}")

    def scaleDown( self, value: float ) -> float:
        '''
        Divide *value* by 1024 (shift one tier down for in-place point
        rescaling when the chart advances a tier).

        Parameters:
            value - Y coordinate in the current tier's units.

        Returns:
            *value* / 1024.
        '''
        return value / 1024

    def scaleUp( self, value: float ) -> float:
        '''
        Multiply *value* by 1024 (shift one tier up for in-place point
        rescaling when the chart retreats a tier).

        Parameters:
            value - Y coordinate in the current tier's units.

        Returns:
            *value* × 1024.
        '''
        return value * 1024

    @property
    def name( self ) -> str:
        '''
        Human-readable label for the current scale tier (e.g. ``"KiB/s"``).

        Returns:
            The label string from the current tier's ``(label, divisor)`` tuple.
        '''
        return self.valueRange[self.index][0]


class RollingChartDynamic(RollingChart):
    '''
    A ``RollingChart`` whose Y axis auto-scales through unit tiers (for example
    Bytes/s -> KiB/s -> MiB/s -> GiB/s) as the data grows and shrinks.

    Parameters:
        title       - Chart title; the current unit is appended, e.g. "Disk I/O (MiB/s)".
        series_defs - List of (name, color_qt_str or None) for each line.
        range_y     - Ordered list of (label, divisor) tiers, see ``scaleValues``.
        window      - Number of points to keep.
    '''
    def __init__(self, title : str, series_defs: list[tuple], range_y : list[tuple],
                 window: int = DATA_WINDOW, parent: QWidget | None = None):
        self.maxY = 512
        super().__init__(title,series_defs,0,self.maxY,window,parent)
        self.title = title
        self.max   = 0
        self.scale = scaleValues(range_y)
        # set by CollapsibleChart to receive title updates
        self.on_title_change: Callable[[str], None] | None = None

    def getBestFit( self, value: float ) -> int:
        '''
        Return the smallest power-of-two Y-axis ceiling that is greater than
        *value*, from a fixed candidate set.  Used to keep the Y axis tidy
        after a scale change.

        Parameters:
            value - Current maximum Y value in the chart (in current tier units).

        Returns:
            An integer ceiling value (4, 8, 16, … 1024, or higher if *value*
            has not yet been rescaled into the 0–1024 range).
        '''
        ceiling = 4
        while ceiling <= value:
            ceiling *= 2
        return ceiling

    def append(self, values: list[float | None]):
        '''
        Append one sample (for each series) at the next x value. Handles rolling window.
        values must match the number of series.

        Parameters:
            values - One number per data series in the chart; None marks a
                    missing sample, which is skipped.
        '''
        scaleUp = False
        self.xpos += 1
        for s, v in zip(self.series, values):
            # Handle NaN by skipping, or plot zero—here we clamp None/NaN to None and skip
            try:
                if v is None:
                    continue
                sv = self.scale.scaleValue(v)
                if sv > 1024:
                    scaleUp = True
                # If you want to clamp, do it here: v = max(self.axis_y.min(), min(self.axis_y.max(), v))
                #if v:
                #    print( f"value : {v} scaled: {sv} " )
                s.append(self.xpos, float(sv))
            except (TypeError, ValueError) as error:
                # ignore bad data points
                print( f"Bad data point {v!r}: {error}" )

        # Trim series to rolling window
        min_x_to_keep = max(0, self.xpos - self.pointWindow)
        self.axis_x.setRange(min_x_to_keep, self.xpos)

        # Only rescale the history if the tier really advanced: at the top
        # tier there is nowhere to go, so values above 1024 just stay as they
        # are and getBestFit() grows the Y axis to fit them.
        if scaleUp:
            scaleUp = self.scale.nextScale()
            if scaleUp:
                self._notify_title()

        maxV = 0.0
        for s in self.series:
            drop = -1
            points = s.points()
            for index, point in enumerate(points):
                if point.x() < min_x_to_keep:
                    drop = index
                if scaleUp:
                    point.setY( self.scale.scaleDown(point.y()))
                maxV = max(maxV, point.y())
            s.replace( points[drop+1:] )

        if maxV > 1:
            self.axis_y.setRange( 0, self.getBestFit(maxV) )

        #print( f"maxV left is {maxV}" )
        if maxV < 1 and self.scale.index > 0:
            self.scale.prevScale()
            self._notify_title()
            for s in self.series:
                points = s.points()
                for point in points:
                    point.setY( self.scale.scaleUp(point.y()))
                s.replace(points)

    def clear(self) -> None:
        '''Clear data and reset scale to the base tier.'''
        self.max = 0
        self.scale.index = 0
        self.axis_y.setRange(0, self.maxY)
        super().clear()
        self._notify_title()

    def _notify_title(self) -> None:
        '''Tell the header (if one is attached) that the unit tier changed.'''
        if self.on_title_change is not None:
            # pylint cannot see that this is assigned a callable after __init__
            self.on_title_change(f"{self.title} ({self.scale.name})")  # pylint: disable=not-callable

class ClickableWidget(QWidget):
    '''A plain QWidget that calls *on_click* when pressed with the mouse.'''
    def __init__(self, on_click: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_click = on_click

    def mousePressEvent(self, a0: QMouseEvent | None) -> None:  # Qt override; name fixed by Qt
        '''Run the click callback.'''
        self._on_click()
        super().mousePressEvent(a0)


class CollapsibleChart(QWidget):
    '''
    A thin wrapper that adds a clickable header strip above a chart widget.

    The header displays an arrow label (▼ expanded, ▶ collapsed).  Clicking
    the header toggles the chart's visibility without stopping data collection
    — the inner chart keeps receiving ``append()`` calls while hidden so it
    displays a live rolling window when re-expanded.

    Parameters:
        chart            - The ``RollingChart`` or ``RollingChartDynamic`` to wrap.
        parent           - Optional parent widget.
        on_toggle        - Optional callback(expanded: bool) called after each toggle.
        initial_expanded - Whether the chart starts expanded (default True).
    '''
    def __init__(self, chart: RollingChart, parent: QWidget | None = None,
                 on_toggle=None, initial_expanded: bool = True) -> None:
        super().__init__(parent)

        self._chart    = chart
        self._expanded = initial_expanded
        self._on_toggle = on_toggle

        # --- Header strip ---
        self._header = ClickableWidget(self._toggle, self)
        self._header.setFixedHeight(20)
        self._header.setStyleSheet("")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)

        self._arrow = QLabel("▼" if initial_expanded else "▶", self._header)
        self._arrow.setStyleSheet("color: #cccccc; font-size: 10px;")

        if isinstance(chart, RollingChartDynamic):
            initial_title = f"{chart.title} ({chart.scale.name})"
            chart.on_title_change = self._update_title
        else:                                # RollingChart (static)
            initial_title = chart.title

        self._title_label = QLabel(initial_title, self._header)
        self._title_label.setStyleSheet("color: #cccccc; font-size: 12px; font-weight: bold;")

        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(4, 0, 4, 0)
        header_layout.setSpacing(0)
        header_layout.addWidget(self._arrow)
        header_layout.addSpacing(6)
        header_layout.addWidget(self._title_label)
        header_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        )

        # --- Outer layout ---
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        outer.addWidget(self._header)
        outer.addWidget(self._chart)

        # Apply initial collapsed state
        if not initial_expanded:
            self._chart.setVisible(False)

    def _toggle(self) -> None:
        '''Toggle chart visibility and update the arrow label.'''
        self._expanded = not self._expanded
        self._chart.setVisible(self._expanded)
        self._arrow.setText("▼" if self._expanded else "▶")
        if self._on_toggle:
            self._on_toggle(self._expanded)

    def setExpanded(self, expanded: bool) -> None:
        '''Programmatically set expanded/collapsed state.'''
        self._expanded = expanded
        self._chart.setVisible(expanded)
        self._arrow.setText("▼" if expanded else "▶")

    def _update_title(self, title: str) -> None:
        '''Update the header title label when the dynamic chart changes scale.'''
        self._title_label.setText(title)

    @property
    def chart(self) -> RollingChart:
        '''The inner chart widget.'''
        return self._chart


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
        self.setWindowIcon(QIcon(os.path.join(os.path.dirname(__file__), 'icon.svg')))
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
    app.setWindowIcon(QIcon(os.path.join(os.path.dirname(__file__), 'icon.svg')))
    w = MonitorWindow(refresh_ms=1000)
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
