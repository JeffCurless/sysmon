"""
Shared pytest fixtures for sysmon.

Nothing here touches real hardware: kernel files come from a temporary fake
/proc and /sys tree, and RPi.GPIO is replaced with a mock, so the suite also
runs on a non-Pi machine (e.g. CI).
"""
import os
import shutil
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Make the top-level modules (systemsupport, configfile, ...) importable.
sys.path.insert(0, str(REPO_ROOT))

# Qt must be headless; must be set before pytest-qt creates the QApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["QT_QPA_PLATFORMTHEME"] = ""

# RPi.GPIO cannot be imported off a Pi (ImportError, or RuntimeError on x86),
# so install a stand-in module before anything imports fanspeed.
try:
    import RPi.GPIO  # pylint: disable=unused-import
except (ImportError, RuntimeError):
    _gpio = mock.MagicMock(name="RPi.GPIO")
    _rpi = types.ModuleType("RPi")
    setattr(_rpi, "GPIO", _gpio)
    sys.modules["RPi"] = _rpi
    sys.modules["RPi.GPIO"] = _gpio


class FakeRoot:
    '''A temporary directory standing in for the filesystem root.'''

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, relpath: str, content: str) -> Path:
        '''Create (or overwrite) ``<root>/<relpath>`` with *content*.'''
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def copy_fixture(self, fixture: str, relpath: str) -> Path:
        '''Copy ``tests/fixtures/<fixture>`` to ``<root>/<relpath>``.'''
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / fixture, path)
        return path

    def mkdir(self, relpath: str) -> Path:
        '''Create an empty directory ``<root>/<relpath>``.'''
        path = self.root / relpath
        path.mkdir(parents=True, exist_ok=True)
        return path


@pytest.fixture
def fake_root(tmp_path, monkeypatch) -> FakeRoot:
    '''
    Point systemsupport at a fake /proc and /sys under *tmp_path*.
    Tests populate it with ``fake_root.write(...)`` / ``copy_fixture(...)``.

    It starts with only a CPU thermal sensor (45.0 C), as on a real Pi;
    tests for a missing sensor delete it.
    '''
    import systemsupport  # pylint: disable=import-outside-toplevel
    monkeypatch.setattr(systemsupport, "PROC_ROOT", str(tmp_path / "proc"))
    monkeypatch.setattr(systemsupport, "SYS_ROOT", str(tmp_path / "sys"))
    root = FakeRoot(tmp_path)
    root.write("sys/class/thermal/thermal_zone0/temp", "45000\n")
    return root


def fixture_text(name: str) -> str:
    '''Contents of ``tests/fixtures/<name>``.'''
    return (FIXTURES / name).read_text(encoding="utf-8")
