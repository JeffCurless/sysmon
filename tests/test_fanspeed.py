# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

"""Tests for fanspeed.GetCaseFanSpeed with a mocked RPi.GPIO and a controlled clock."""
# pylint: disable=redefined-outer-name,missing-function-docstring,unused-argument,protected-access
# (pytest fixtures are injected as same-named arguments; test names are their docs)
from unittest import mock

import pytest

import fanspeed
from fanspeed import GetCaseFanSpeed


class Clock:
    '''Stand-in for time.time() that only moves when told to.'''
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        '''Move the clock forward.'''
        self.now += seconds


@pytest.fixture
def gpio(monkeypatch):
    '''A fresh mock GPIO module for each test.'''
    fake = mock.MagicMock(name="GPIO")
    monkeypatch.setattr(fanspeed, "GPIO", fake)
    return fake


@pytest.fixture
def clock(monkeypatch):
    '''Replace fanspeed's time.time() with a controllable clock.'''
    fake = Clock()
    monkeypatch.setattr(fanspeed.time, "time", fake)
    return fake


def pulse(fan: GetCaseFanSpeed, clock: Clock, after: float) -> None:
    '''Simulate a tachometer falling edge *after* seconds from now.'''
    clock.advance(after)
    fan._calcRPM(18)


def test_gpio_setup(gpio, clock):
    fan = GetCaseFanSpeed(18)
    gpio.setmode.assert_called_once_with(gpio.BCM)
    gpio.setup.assert_called_once_with(18, gpio.IN, pull_up_down=gpio.PUD_UP)
    gpio.add_event_detect.assert_called_once_with(18, gpio.FALLING, fan._calcRPM)


def test_rpm_zero_before_any_pulse(gpio, clock):
    assert GetCaseFanSpeed(18).RPM == 0.0


@pytest.mark.parametrize("interval, rpm", [
    (0.010, 3000.0),   # 100 Hz / 2 pulses per rev * 60
    (0.020, 1500.0),
    (0.100, 300.0),
])
def test_rpm_from_pulse_interval(gpio, clock, interval, rpm):
    fan = GetCaseFanSpeed(18)
    pulse(fan, clock, interval)
    pulse(fan, clock, interval)
    assert fan.RPM == pytest.approx(rpm)


def test_bounce_is_ignored(gpio, clock):
    fan = GetCaseFanSpeed(18)
    pulse(fan, clock, 0.010)
    assert fan.RPM == pytest.approx(3000.0)
    pulse(fan, clock, 0.003)              # < 5 ms: rejected
    assert fan.RPM == pytest.approx(3000.0)
    pulse(fan, clock, 0.007)              # measured from the last *accepted* pulse: 10 ms
    assert fan.RPM == pytest.approx(3000.0)


def test_rpm_drops_to_zero_when_fan_stops(gpio, clock):
    fan = GetCaseFanSpeed(18)
    pulse(fan, clock, 0.010)
    clock.advance(fanspeed.WAIT_TIME - 0.01)
    assert fan.RPM == pytest.approx(3000.0)   # still within the timeout
    clock.advance(0.02)
    assert fan.RPM == 0.0                     # no pulse for > WAIT_TIME


def test_close_is_idempotent_and_only_releases_its_pin(gpio, clock):
    fan = GetCaseFanSpeed(18)
    fan.close()
    fan.close()
    gpio.remove_event_detect.assert_called_once_with(18)
    gpio.cleanup.assert_called_once_with(18)


def test_context_manager_closes(gpio, clock):
    with GetCaseFanSpeed(23):
        gpio.cleanup.assert_not_called()
    gpio.cleanup.assert_called_once_with(23)
