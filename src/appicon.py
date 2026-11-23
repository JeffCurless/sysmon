# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

"""
Application icon for sysmon.

The icons live in ../icons relative to this file, both in the repository and
in the installed package (/usr/lib/sysmon/app/{src,icons}).
"""
import os

from PyQt6.QtGui import QIcon

ICON_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'icons'))
# Must match the PNGs written by icons/make_icons.py.
ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256)


def icon_files() -> list[str]:
    '''Return the paths of the SVG icon and every pre-rendered PNG size.'''
    files = [os.path.join(ICON_DIR, 'sysmonicon.svg')]
    files += [os.path.join(ICON_DIR, f'sysmon{n}x{n}.png') for n in ICON_SIZES]
    return files


def app_icon() -> QIcon:
    '''Build the window icon from the SVG plus the pre-rendered PNG sizes.

    The PNGs keep the icon visible even where Qt's SVG plugin isn't installed.
    Missing files are skipped, so this never raises.
    '''
    icon = QIcon()
    for path in icon_files():
        if os.path.isfile(path):
            icon.addFile(path)
    return icon
