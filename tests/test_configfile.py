# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

"""Tests for configfile.ConfigClass: it must never raise, and must fall back to defaults."""
# pylint: disable=redefined-outer-name,missing-function-docstring,unused-argument,protected-access
# (pytest fixtures are injected as same-named arguments; test names are their docs)
import pytest

from conftest import FIXTURES

from configfile import ConfigClass


@pytest.fixture
def ini(tmp_path):
    '''Write an INI file and return a ConfigClass reading it.'''
    def make(text: str) -> ConfigClass:
        path = tmp_path / "sysmon.ini"
        path.write_text(text, encoding="utf-8")
        return ConfigClass(str(path))
    return make


SAMPLE = '''
[drive]
    temp_ignore = mmcblk0, sda
    perf_ignore = "mmcblk0"

[cooling]
    casefan = 18

[smartctl]
    sda = -d sat
'''


def test_get_value(ini):
    cfg = ini(SAMPLE)
    assert cfg.getValue("cooling", "casefan") == "18"
    assert cfg.getValue("smartctl", "sda") == "-d sat"


def test_get_value_strips_double_quotes(ini):
    assert ini(SAMPLE).getValue("drive", "perf_ignore") == "mmcblk0"


@pytest.mark.parametrize("section, key", [("cooling", "nokey"), ("nosection", "casefan")])
def test_get_value_missing_returns_default(ini, section, key):
    cfg = ini(SAMPLE)
    assert cfg.getValue(section, key) == ""            # built-in default
    assert cfg.getValue(section, key, None) is None    # caller's default


def test_get_value_as_list(ini):
    cfg = ini(SAMPLE)
    assert cfg.getValueAsList("drive", "temp_ignore") == ["mmcblk0", "sda"]
    assert cfg.getValueAsList("drive", "perf_ignore") == ["mmcblk0"]


def test_get_value_as_list_missing_returns_default(ini):
    cfg = ini(SAMPLE)
    assert cfg.getValueAsList("network", "device_ignore") == []
    assert cfg.getValueAsList("network", "device_ignore", ["lo"]) == ["lo"]


def test_percent_sign_does_not_raise(ini):
    # configparser treats '%' as interpolation and raises on access;
    # ConfigClass must swallow that and return the default.
    cfg = ini("[a]\nx = 50%\ny = 1%, 2\n")
    assert cfg.getValue("a", "x", "default") == "default"
    assert cfg.getValueAsList("a", "y", ["default"]) == ["default"]


def test_missing_file_returns_defaults(tmp_path):
    cfg = ConfigClass(str(tmp_path / "does-not-exist.ini"))
    assert cfg.getValue("drive", "temp_ignore", "d") == "d"
    assert cfg.getValueAsList("drive", "temp_ignore") == []


def test_unparseable_file_returns_defaults(ini):
    cfg = ini("this is not an ini file\n")
    assert cfg.getValue("drive", "temp_ignore", "d") == "d"


def test_duplicate_key_in_test_ini(capsys):
    # test.ini deliberately defines [smartctl] sda twice to exercise this path.
    cfg = ConfigClass(str(FIXTURES / "test.ini"))
    assert "already exists" in capsys.readouterr().out
    # Values before the duplicate are kept ...
    assert cfg.getValue("temperature", "ignore") == "mmcblk0"
    assert cfg.getValueAsList("performance", "ignore") == ["mmcblk0"]
    assert cfg.getValue("smartctl", "sda") == "foobar"
    # ... and parsing stops there, so later keys fall back to the default.
    assert cfg.getValue("smartctl", "sdb", "missing") == "missing"


def test_defaults_are_overridden_key_by_key(tmp_path):
    path = tmp_path / "sysmon.ini"
    path.write_text("[network]\ndevice_ignore = wg0\n", encoding="utf-8")
    cfg = ConfigClass(str(path), defaults="[network]\ndevice_ignore = lo\n[drive]\ntemp_ignore = mmcblk0\n")
    assert cfg.loaded
    assert cfg.getValueAsList("network", "device_ignore") == ["wg0"]      # file wins
    assert cfg.getValueAsList("drive", "temp_ignore") == ["mmcblk0"]      # default fills the gap


def test_defaults_used_when_file_missing(tmp_path):
    cfg = ConfigClass(str(tmp_path / "missing.ini"), defaults="[network]\ndevice_ignore = lo\n")
    assert not cfg.loaded
    assert cfg.getValueAsList("network", "device_ignore") == ["lo"]


def test_empty_value_is_an_empty_list(tmp_path):
    path = tmp_path / "sysmon.ini"
    path.write_text("[network]\ndevice_ignore =\n", encoding="utf-8")
    assert ConfigClass(str(path)).getValueAsList("network", "device_ignore") == []
