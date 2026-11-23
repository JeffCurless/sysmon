"""Example-based tests for scaleValues and RollingChartDynamic's unit-tier scaling."""
# pylint: disable=redefined-outer-name,missing-function-docstring,unused-argument,protected-access
# (pytest fixtures are injected as same-named arguments; test names are their docs)
import pytest

from charts import RollingChartDynamic, scaleValues

TIERS = [("Bytes/s", 1), ("KiB/s", 1024), ("MiB/s", 1024**2), ("GiB/s", 1024**3)]


# --------------------------------------------------------------------------
# scaleValues (plain Python, no Qt needed)
# --------------------------------------------------------------------------

def test_scale_values_starts_at_lowest_tier():
    s = scaleValues(TIERS)
    assert (s.name, s.scale) == ("Bytes/s", 1)
    assert s.scaleValue(2048) == 2048


def test_scale_values_next_and_prev_stop_at_the_ends():
    s = scaleValues(TIERS)
    s.prevScale()
    assert s.name == "Bytes/s"          # already at the bottom
    assert [s.nextScale() for _ in range(5)] == [True, True, True, False, False]
    assert (s.name, s.scale) == ("GiB/s", 1024**3)   # stops at the top
    assert s.scaleValue(3 * 1024**3) == 3
    s.prevScale()
    assert s.name == "MiB/s"


def test_scale_down_and_up_are_inverse():
    s = scaleValues(TIERS)
    assert s.scaleDown(5120) == 5
    assert s.scaleUp(s.scaleDown(12345.0)) == 12345.0


# --------------------------------------------------------------------------
# RollingChartDynamic
# --------------------------------------------------------------------------

class RecordingChart(RollingChartDynamic):
    '''A dynamic chart that records every title it announces.'''
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.titles: list[str] = []
        self.on_title_change = self.titles.append


@pytest.fixture
def chart(qapp):
    '''A two-series dynamic chart, 60-point window.'''
    return RecordingChart("Disk I/O", [("Read", None), ("Write", None)], TIERS, window=60)


def ys(series):
    '''Y values of a QLineSeries.'''
    return [p.y() for p in series.points()]


@pytest.mark.parametrize("value, ceiling", [
    (0, 4), (3.9, 4), (4, 8), (100, 128), (1000, 1024), (1023.9, 1024),
    (1024, 2048),    # used to fall back to 4, putting the point off-screen
    (5000, 8192),
])
def test_best_fit(chart, value, ceiling):
    assert chart.getBestFit(value) == ceiling


def test_small_values_stay_in_bytes(chart):
    chart.append([500, 1000])
    assert chart.scale.name == "Bytes/s"
    assert ys(chart.series[0]) == [500]
    assert chart.axis_y.max() == 1024
    assert chart.titles == []


def test_scales_up_and_rescales_history(chart):
    chart.append([512, 0])
    chart.append([4 * 1024 * 1024, 0])       # 4 MiB/s: one tier per tick
    assert chart.scale.name == "KiB/s"
    assert ys(chart.series[0]) == [0.5, 4096]
    assert chart.titles == ["Disk I/O (KiB/s)"]
    assert chart.axis_y.max() >= 4096         # the point is on-screen

    chart.append([4 * 1024 * 1024, 0])
    assert chart.scale.name == "MiB/s"
    assert ys(chart.series[0]) == pytest.approx([0.5 / 1024, 4, 4])


def test_scales_back_down_when_traffic_stops(chart):
    chart.append([10 * 1024 * 1024, 0])      # -> KiB/s
    chart.append([10 * 1024 * 1024, 0])      # -> MiB/s
    assert chart.scale.name == "MiB/s"
    tiers = []
    for _ in range(70):
        chart.append([0, 0])
        tiers.append(chart.scale.name)
    # The big samples leave the 60-point window after 61 ticks (the window
    # maximum is measured before trimming, so it notices one tick later);
    # then the chart steps down one tier per tick with no skips.
    first_change = tiers.index("KiB/s")
    assert first_change <= 62
    assert tiers[:first_change] == ["MiB/s"] * first_change
    assert tiers[first_change:first_change + 2] == ["KiB/s", "Bytes/s"]
    assert set(tiers[first_change + 1:]) == {"Bytes/s"}


def test_idle_chart_does_not_announce_titles(chart):
    for _ in range(5):
        chart.append([0, 0])
    assert chart.titles == []                # no rescale churn while idle


def test_none_samples_are_skipped(chart):
    chart.append([None, 100])
    assert ys(chart.series[0]) == []
    assert ys(chart.series[1]) == [100]


def test_window_trims_old_points(chart):
    for i in range(100):
        chart.append([i, i])
    xs = [p.x() for p in chart.series[0].points()]
    assert len(xs) == 61                      # window + the newest point
    assert xs[-1] - xs[0] == 60


def test_clear_resets_to_base_tier(chart):
    chart.append([10 * 1024 * 1024, 0])
    chart.clear()
    assert chart.scale.name == "Bytes/s"
    assert ys(chart.series[0]) == []
    assert chart.titles[-1] == "Disk I/O (Bytes/s)"
