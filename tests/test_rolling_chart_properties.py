"""
Property-based tests for RollingChartDynamic: hypothesis generates many random
traffic sequences and checks rules that must hold after every sample.
"""
# pylint: disable=redefined-outer-name,missing-function-docstring,unused-argument,protected-access
# (pytest fixtures are injected as same-named arguments; test names are their docs)
import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings, strategies as st  # pylint: disable=wrong-import-position,import-error

from sysmon import RollingChartDynamic  # pylint: disable=wrong-import-position

TIERS = [("Bytes/s", 1), ("KiB/s", 1024), ("MiB/s", 1024**2), ("GiB/s", 1024**3)]
WINDOW = 20          # smaller than the app's 60 so examples roll the window quickly

# Bursty traffic: mostly idle or modest, with occasional huge jumps, and the
# odd missing sample (None).  Rates are bytes per second, up to far beyond the
# top (GiB/s) tier.
MAX_RATE = 2**60
rate = st.one_of(
    st.just(0),
    st.integers(0, 2_000),
    st.integers(0, 50 * 1024**2),
    st.integers(0, MAX_RATE),
    st.none(),
)
sample = st.tuples(rate, rate)


@settings(max_examples=300, deadline=None)
@given(samples=st.lists(sample, min_size=1, max_size=3 * WINDOW))
def test_dynamic_chart_invariants(qapp, samples):
    chart = RollingChartDynamic("Net", [("Rx", None), ("Tx", None)], TIERS, window=WINDOW)
    titles: list[str] = []
    chart.on_title_change = titles.append
    raw: list[dict[int, int]] = [{}, {}]   # per series: x position -> raw bytes/s value that was appended

    for values in samples:
        chart.append(list(values))
        for i, v in enumerate(values):
            if v is not None:
                raw[i][chart.xpos] = v

        divisor = chart.scale.scale
        all_y = []
        for i, series in enumerate(chart.series):
            points = series.points()
            # 1. The window never holds more than WINDOW + 1 points.
            assert len(points) <= WINDOW + 1
            for p in points:
                # 2. Displayed value x current unit == the raw value that went in.
                assert p.y() * divisor == pytest.approx(raw[i][int(p.x())], rel=1e-9, abs=1e-6)
                all_y.append(p.y())
        # 3. Every visible point is on-screen.
        if all_y:
            assert chart.axis_y.max() >= max(all_y)
        # 4. The header title always names the current unit.
        if titles:
            assert titles[-1] == f"Net ({chart.scale.name})"


def test_top_tier_overflow_keeps_history(qapp):
    # Minimal case found by hypothesis: at the top tier (GiB/s) a sample above
    # 1 TiB/s cannot advance the tier, and used to divide the history by 1024.
    chart = RollingChartDynamic("Net", [("Rx", None)], TIERS, window=WINDOW)
    for v in [1025, 1024**2 + 1, 1024**3 + 1, 1024**4 + 1]:
        chart.append([v])
    assert chart.scale.name == "GiB/s"
    first = chart.series[0].points()[0]
    assert first.y() * chart.scale.scale == pytest.approx(1025)
