# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

"""
Chart widgets for sysmon: rolling line charts with fixed or auto-scaling Y axes,
and the collapsible wrapper that lets the user fold a chart away by clicking
its title.
"""
from collections.abc import Callable

from PyQt6.QtCore import Qt, QMargins
from PyQt6.QtGui import QColor, QPainter, QMouseEvent
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QSpacerItem, QVBoxLayout, QWidget, QGridLayout
from PyQt6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis

DATA_WINDOW = 60    # samples kept per chart (one per refresh)


class ChartView(QChartView):
    '''
    A QChartView that reports clicks on the chart's title band (everything
    above the plot area) through *on_title_click*, and shows a pointing-hand
    cursor there so the title reads as clickable.
    '''
    def __init__(self, chart: QChart, parent: QWidget | None = None) -> None:
        super().__init__(chart, parent)
        self.on_title_click: Callable[[], None] | None = None
        self.setMouseTracking(True)

    def _in_title_band(self, event: QMouseEvent) -> bool:
        '''True if *event* is above the plot area, i.e. on the title.'''
        chart = self.chart()
        if chart is None:
            return False
        point = chart.mapFromScene(self.mapToScene(event.position().toPoint()))
        return point.y() < chart.plotArea().top()

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # Qt override; name fixed by Qt
        '''Run the title-click callback for a left click on the title band.'''
        if (event is not None and self.on_title_click is not None
                and event.button() == Qt.MouseButton.LeftButton and self._in_title_band(event)):
            self.on_title_click()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:  # Qt override; name fixed by Qt
        '''Show a pointing hand over a clickable title.'''
        if event is not None:
            if self.on_title_click is not None and self._in_title_band(event):
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.unsetCursor()
        super().mouseMoveEvent(event)


class RollingChart(QWidget):
    '''
    A chart widget with one or more lines and a rolling X window, and a
    fixed Y axis range.

    Parameters:
        title       - Chart title.
        series_defs - List of (name, color) for each line; color is a QColor,
                      a "#RRGGBB" string, or None for the theme's colour.
        y_min,y_max - Y axis range.
        window      - Number of samples to keep (one per refresh).
        parent      - Optional parent widget.
    '''
    def __init__(self, title: str, series_defs: list[tuple], y_min: float, y_max: float,
                 window: int = DATA_WINDOW, parent: QWidget | None = None):
        super().__init__(parent)
        self.title = title
        self.pointWindow = window
        self.xpos   = window - 1
        self.chart  = QChart()
        self.chart.setTheme(QChart.ChartTheme.ChartThemeDark)

        # Keep the chrome around the plot thin: Qt's default margins, layout
        # padding and a bottom legend left only ~25% of each chart for the plot.
        self.chart.setMargins(QMargins(2, 2, 2, 2))
        chart_layout = self.chart.layout()
        assert chart_layout is not None   # a QChart always owns a layout
        chart_layout.setContentsMargins(0, 0, 0, 0)
        self.chart.setBackgroundRoundness(0)

        # The title sits inside the chart; CollapsibleChart makes it clickable.
        title_font = self.chart.titleFont()
        title_font.setPointSize(9)
        title_font.setBold(True)
        self.chart.setTitleFont(title_font)
        self.chart.setTitleBrush(QColor("#cccccc"))
        self.chart.setTitle(title)

        legend = self.chart.legend()
        assert legend is not None   # a QChart always owns a legend
        legend.setVisible(len(series_defs) > 1)
        legend.setAlignment(Qt.AlignmentFlag.AlignRight)
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

        self.view = ChartView(self.chart)
        self.view.setRenderHints(QPainter.RenderHint.Antialiasing)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view, 0, 0)

    @property
    def on_title_click(self) -> Callable[[], None] | None:
        '''Callback run when the chart's title is clicked (None: title not clickable).'''
        return self.view.on_title_click

    @on_title_click.setter
    def on_title_click(self, callback: Callable[[], None] | None) -> None:
        self.view.on_title_click = callback

    def setTitleText(self, text: str) -> None:
        '''Change the title drawn inside the chart.'''
        self.chart.setTitle(text)

    def legendWidth(self) -> float:
        '''Width the legend needs for its longest entry (0 if the legend is hidden).'''
        legend = self.chart.legend()
        assert legend is not None   # a QChart always owns a legend
        if not legend.isVisible():
            return 0.0
        return legend.effectiveSizeHint(Qt.SizeHint.PreferredSize).width()

    def setLegendWidth(self, width: float) -> None:
        '''Fix the legend at *width* pixels, so stacked charts' plots line up.'''
        legend = self.chart.legend()
        assert legend is not None   # a QChart always owns a legend
        legend.setMinimumWidth(width)
        legend.setMaximumWidth(width)

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
        Convert *value* into the next larger unit (divide by 1024).  Used to
        rescale the plotted history when the chart moves up a tier.

        Parameters:
            value - Y value in the current tier's unit.

        Returns:
            *value* / 1024.
        '''
        return value / 1024

    def scaleUp( self, value: float ) -> float:
        '''
        Convert *value* into the next smaller unit (multiply by 1024).  Used to
        rescale the plotted history when the chart moves down a tier.

        Parameters:
            value - Y value in the current tier's unit.

        Returns:
            *value* x 1024.
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
        series_defs - List of (name, color) for each line, as for ``RollingChart``.
        range_y     - Ordered list of (label, divisor) tiers, see ``scaleValues``.
        window      - Number of samples to keep.
        parent      - Optional parent widget.
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
        The Y axis ceiling for *value*: the smallest power of two, at least 4,
        that is greater than *value*.

        Parameters:
            value - Largest Y value in the chart, in the current tier's unit.

        Returns:
            The ceiling (4, 8, 16, ...).
        '''
        ceiling = 4
        while ceiling <= value:
            ceiling *= 2
        return ceiling

    def append(self, values: list[float | None]):
        '''
        Append one sample for each series at the next x value, trim points that
        have left the window, and rescale if needed.

        Values are converted to the current unit.  If any converted value is over
        1024, the chart moves up one tier and the whole history is converted; if
        everything in the window is below 1, it moves down one tier.  The Y axis
        is then sized with ``getBestFit``.

        Parameters:
            values - One raw value (e.g. bytes/s) per series; None marks a
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
        '''
        Report the new title, e.g. "Disk I/O (MiB/s)", to ``on_title_change``
        (set by ``CollapsibleChart``) after the unit tier changed.
        '''
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
    Wraps a chart so the user can collapse it by clicking its title.

    While expanded, the title is drawn inside the chart ("▼ Disk I/O (MiB/s)")
    so it costs no extra space; clicking it collapses the chart.  While
    collapsed, the chart is replaced by a slim "▶ Disk I/O (MiB/s)" bar, and
    clicking that expands it again.  Data collection never stops — the inner
    chart keeps receiving ``append()`` calls while hidden, so it shows a live
    rolling window when re-expanded.

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

        if isinstance(chart, RollingChartDynamic):
            self._title = f"{chart.title} ({chart.scale.name})"
            chart.on_title_change = self._update_title
        else:                                # RollingChart (static)
            self._title = chart.title
        chart.on_title_click = self._toggle

        # --- Header strip, shown only while collapsed ---
        self._header = ClickableWidget(self._toggle, self)
        self._header.setFixedHeight(20)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)

        self._arrow = QLabel(self._header)
        self._arrow.setStyleSheet("color: #cccccc; font-size: 10px;")
        self._title_label = QLabel(self._title, self._header)
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
        outer.setSpacing(0)
        outer.addWidget(self._header)
        outer.addWidget(self._chart)

        self._apply_state()

    def _apply_state(self) -> None:
        '''Show the chart (title inside it) or the collapsed bar, per ``_expanded``.'''
        self._arrow.setText("▼" if self._expanded else "▶")
        self._chart.setTitleText(f"▼ {self._title}")
        self._chart.setVisible(self._expanded)
        self._header.setVisible(not self._expanded)

    def _toggle(self) -> None:
        '''Toggle between expanded and collapsed.'''
        self._expanded = not self._expanded
        self._apply_state()
        if self._on_toggle:
            self._on_toggle(self._expanded)

    def setExpanded(self, expanded: bool) -> None:
        '''Programmatically set expanded/collapsed state.'''
        self._expanded = expanded
        self._apply_state()

    def _update_title(self, title: str) -> None:
        '''Update both titles when the dynamic chart changes unit tier.'''
        self._title = title
        self._title_label.setText(title)
        self._chart.setTitleText(f"▼ {title}")

    @property
    def chart(self) -> RollingChart:
        '''The inner chart widget.'''
        return self._chart
