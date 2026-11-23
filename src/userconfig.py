# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

"""
Where sysmon's settings live, and how they are saved.

Settings are per user, in ``$XDG_CONFIG_HOME/sysmon/sysmon.ini`` (normally
``~/.config/sysmon/sysmon.ini``, next to the window-layout file sysmon.conf).
Built-in hardware defaults (see ``autoconfig``) fill in anything the file
doesn't set, so the file is optional.

Why per user, not /etc: sysmon runs as the user and reaches root only through
``sudo smartctl``, so sudo is the security boundary.  A personal file can't
grant anything the user couldn't already do; a shared writable file could let
one user steer another user's root smartctl.

/etc/sysmon.ini (used by older versions) is read only once: it seeds a missing
personal file.  Nothing here raises.
"""
import os
import shutil
import tempfile

SYSTEM_CONFIG = "/etc/sysmon.ini"


def user_config_path() -> str:
    '''Path of this user's settings file (it may not exist yet).'''
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "sysmon", "sysmon.ini")


def migrate(user_path: str, system_path: str = SYSTEM_CONFIG) -> bool:
    '''
    Copy *system_path* to *user_path* if the user has no settings file yet.

    Returns:
        True if a file was copied.
    '''
    if os.path.exists(user_path) or not os.path.isfile(system_path):
        return False
    try:
        os.makedirs(os.path.dirname(user_path), exist_ok=True)
        shutil.copyfile(system_path, user_path)
    except OSError as error:
        print(f"Could not copy {system_path} to {user_path}: {error}")
        return False
    print(f"Copied {system_path} to {user_path}; sysmon's settings are now per user")
    return True


def save(path: str, text: str) -> bool:
    '''
    Atomically replace *path* with *text*, creating its directory if needed.

    Returns:
        True on success; on failure the old file is left untouched.
    '''
    directory = os.path.dirname(path) or "."
    tmp_path = None
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".sysmon.ini.", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, path)
        tmp_path = None
        return True
    except OSError as error:
        print(f"Could not save {path}: {error}")
        return False
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
