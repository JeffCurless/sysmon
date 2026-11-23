#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

"""
Render the PNG icon set from sysmonicon.svg.

Desktop menus and panels look up an application icon in the freedesktop
"hicolor" theme, which has one directory per pixel size.  Most Debian
desktops can scale the SVG, but small PNGs are sharper and are what the
icon theme specification recommends (48x48 at minimum), so the package ships
both.  Run this after changing the SVG and commit the regenerated PNGs:

    python3 icons/make_icons.py

Uses Qt's SVG image plugin (Debian package qt6-svg-plugins).
"""
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QGuiApplication, QImageReader

ICON_DIR = Path(__file__).resolve().parent
SOURCE = ICON_DIR / "sysmonicon.svg"
# The standard hicolor sizes; keep in step with src/appicon.py and debian/rules.
SIZES = (16, 22, 24, 32, 48, 64, 128, 256)


def png_name(size: int) -> str:
    '''Return the file name used for the *size* x *size* PNG.'''
    return f"sysmon{size}x{size}.png"


def main() -> int:
    '''Write sysmon<N>x<N>.png for every size in SIZES.'''
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _app = QGuiApplication(sys.argv)
    for size in SIZES:
        reader = QImageReader(str(SOURCE))
        reader.setScaledSize(QSize(size, size))
        image = reader.read()
        if image.isNull():
            print(f"cannot render {SOURCE}: {reader.errorString()}", file=sys.stderr)
            return 1
        out = ICON_DIR / png_name(size)
        if not image.save(str(out), "PNG"):
            print(f"cannot write {out}", file=sys.stderr)
            return 1
        print(f"wrote {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
