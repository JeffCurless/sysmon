# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

"""Tests for userconfig.py: the per-user settings file, migration from /etc, and saving."""
# pylint: disable=missing-function-docstring
import os

import userconfig


def test_path_uses_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert userconfig.user_config_path() == str(tmp_path / "cfg" / "sysmon" / "sysmon.ini")


def test_path_defaults_to_dot_config(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert userconfig.user_config_path() == str(tmp_path / ".config" / "sysmon" / "sysmon.ini")


def test_migrate_copies_system_file_once(tmp_path):
    system = tmp_path / "etc" / "sysmon.ini"
    system.parent.mkdir()
    system.write_text("[network]\ndevice_ignore = lo\n", encoding="utf-8")
    user = tmp_path / "home" / "sysmon" / "sysmon.ini"
    assert userconfig.migrate(str(user), str(system))
    assert user.read_text(encoding="utf-8") == "[network]\ndevice_ignore = lo\n"

    system.write_text("[network]\ndevice_ignore = eth0\n", encoding="utf-8")
    assert not userconfig.migrate(str(user), str(system))                # user file wins from now on
    assert user.read_text(encoding="utf-8") == "[network]\ndevice_ignore = lo\n"


def test_migrate_without_system_file(tmp_path):
    user = tmp_path / "sysmon" / "sysmon.ini"
    assert not userconfig.migrate(str(user), str(tmp_path / "missing.ini"))
    assert not user.exists()


def test_save_creates_directory_and_replaces_atomically(tmp_path):
    path = tmp_path / "new" / "dir" / "sysmon.ini"
    assert userconfig.save(str(path), "first\n")
    assert userconfig.save(str(path), "second\n")
    assert path.read_text(encoding="utf-8") == "second\n"
    assert oct(path.stat().st_mode & 0o777) == "0o644"
    assert os.listdir(path.parent) == ["sysmon.ini"]                       # no temp files left behind


def test_save_failure_leaves_old_file(tmp_path, capsys):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("", encoding="utf-8")
    assert not userconfig.save(str(blocker / "sysmon.ini"), "text")
    assert "Could not save" in capsys.readouterr().out
