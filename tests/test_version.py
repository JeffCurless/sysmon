# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

"""Tests for version.py: one version, from debian/changelog."""
# pylint: disable=missing-function-docstring
import shutil
import subprocess

import pytest

from conftest import REPO_ROOT

import version


def test_repo_version_matches_the_package_version():
    '''Running from the repo shows the same version dpkg builds the package with.'''
    if shutil.which("dpkg-parsechangelog") is None:
        pytest.skip("dpkg-parsechangelog is not installed")
    expected = subprocess.run(["dpkg-parsechangelog", "-S", "Version"], cwd=REPO_ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    assert version.version() == expected


def test_installed_version_file_wins(tmp_path):
    (tmp_path / "VERSION").write_text("2.3.4\n", encoding="utf-8")
    (tmp_path / "debian").mkdir()
    (tmp_path / "debian" / "changelog").write_text("sysmon (1.0.0) unstable; urgency=medium\n",
                                                  encoding="utf-8")
    assert version.version(tmp_path) == "2.3.4"


def test_changelog_top_line(tmp_path):
    (tmp_path / "debian").mkdir()
    (tmp_path / "debian" / "changelog").write_text(
        "sysmon (1.2.0-1) unstable; urgency=medium\n\n  * x\n\nsysmon (1.1.0) unstable\n", encoding="utf-8")
    assert version.version(tmp_path) == "1.2.0-1"


def test_unknown_without_a_source(tmp_path):
    assert version.version(tmp_path) == "unknown"
