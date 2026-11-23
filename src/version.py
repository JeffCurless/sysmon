# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

"""
sysmon's version number, from a single source: ``debian/changelog``.

The package version comes from the changelog's top entry (dpkg requires it),
so the app reads the same thing:
  * installed: ``debian/rules`` writes ``/usr/lib/sysmon/app/VERSION`` from
    ``dpkg-parsechangelog -S Version`` at build time;
  * from the repository: the top line of ``debian/changelog`` is parsed.

To release a new version, add a ``debian/changelog`` entry; nothing else.
"""
import re
from pathlib import Path

# The app directory: the repository root, or /usr/lib/sysmon/app when installed.
APP_DIR = Path(__file__).resolve().parent.parent

# Top line of debian/changelog, e.g. "sysmon (1.0.1) unstable; urgency=medium".
_CHANGELOG_TOP = re.compile(r"^\S+ \(([^)\s]+)\)")


def version(app_dir: Path = APP_DIR) -> str:
    '''
    sysmon's version, e.g. "1.0.1", or "unknown" if neither source can be read.

    Parameters:
        app_dir - Directory holding ``VERSION`` (installed) or ``debian/``
                  (repository); normally the default.
    '''
    try:
        text = (app_dir / "VERSION").read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    try:
        with open(app_dir / "debian" / "changelog", encoding="utf-8") as f:
            match = _CHANGELOG_TOP.match(f.readline())
        if match:
            return match.group(1)
    except OSError:
        pass
    return "unknown"
