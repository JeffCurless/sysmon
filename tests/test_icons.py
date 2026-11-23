# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

"""Tests for the icon set in icons/ and the window icon built from it (appicon.py)."""
import pytest
from PyQt6.QtGui import QImage

from conftest import REPO_ROOT

import appicon

ICON_DIR = REPO_ROOT / "icons"


def test_icon_dir_matches_repo_layout():
    '''appicon finds ../icons relative to src/.'''
    assert appicon.ICON_DIR == str(ICON_DIR)


def test_every_icon_file_exists():
    '''The SVG and every PNG size are committed.'''
    missing = [path for path in appicon.icon_files() if not (ICON_DIR / path).is_file()]
    assert not missing


@pytest.mark.parametrize("size", appicon.ICON_SIZES)
def test_png_is_its_named_size(qapp, size):  # pylint: disable=unused-argument
    '''sysmon<N>x<N>.png really is N x N pixels.'''
    image = QImage(str(ICON_DIR / f"sysmon{size}x{size}.png"))
    assert not image.isNull()
    assert (image.width(), image.height()) == (size, size)


def test_app_icon_has_every_size(qapp):  # pylint: disable=unused-argument
    '''The window icon can produce a pixmap at each size.'''
    icon = appicon.app_icon()
    assert not icon.isNull()
    for size in appicon.ICON_SIZES:
        assert not icon.pixmap(size, size).isNull()
