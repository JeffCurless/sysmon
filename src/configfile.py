# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

"""
INI settings reader for sysmon that never raises: missing files, sections or
keys return the caller-supplied default.  Optional default text can be layered
under the file, so the file only needs the keys it wants to change.
"""
import configparser
from typing import Any


class ConfigClass:
    '''
    Read an INI-style settings file.  No method raises: anything missing or
    unreadable falls back to the caller's default.  Read-only; saving is done by
    ``userconfig.save``.
    '''
    def __init__( self, filename: str, defaults: str | None = None ) -> None:
        '''
        Load the INI configuration file at *filename*.  If the file does not
        exist or cannot be parsed, the object is still created and all
        subsequent ``getValue`` / ``getValueAsList`` calls will silently
        return their caller-supplied defaults.

        Parameters:
            filename - Absolute or relative path to the ``.ini`` file.
            defaults - Optional INI text read *before* the file: the file's
                       keys override it key by key, and keys the file lacks
                       fall back to it.
        '''
        self.filename = filename
        self.config   = configparser.ConfigParser()
        # True once the file has actually been read (False if it is missing).
        self.loaded   = False
        if defaults is not None:
            self.loadString( defaults )
        self._openConfig()

    def _openConfig(self) -> None:
        '''
        Read the settings file, if it exists.

        Sets ``loaded`` to True when the file was read.  A file that exists but
        can't be parsed is reported, and still counts as loaded: whatever parsed
        before the error is kept.  A missing file is not an error.
        '''
        try:
            self.loaded = bool(self.config.read( self.filename ))
        except (configparser.Error, OSError, UnicodeDecodeError) as error:
            # The file exists but is malformed; whatever parsed before the
            # error is kept, and the file still counts as the configuration.
            self.loaded = True
            print( f"{error}" )

    def loadString( self, text: str ) -> None:
        '''
        Read configuration from *text* (INI format) instead of a file, for
        example generated defaults when the file is missing.  Like the rest of
        the class, a parse error is reported and otherwise ignored.

        Parameters:
            text - INI-format configuration text.
        '''
        try:
            self.config.read_string( text )
        except configparser.Error as error:
            print( f"{error}" )

    def getValue( self, section : str, key : str, default="" ) -> str:
        '''
        Return the value of *key* in *section*, with double quotes removed and
        surrounding whitespace stripped.

        Parameters:
            section - Section name, e.g. ``cooling``.
            key     - Key name, e.g. ``casefan``.
            default - Returned if the section or key doesn't exist.

        Returns:
            The value as a string, or *default*.
        '''
        value = default
        try:
            value = self.config[section][key].replace('"','').strip()
        except (KeyError, configparser.Error):
            value = default
        return value

    def getValueAsList( self, section : str, name : str, default : list[Any]|None = None) -> list[str]:
        '''
        Return the value of *name* in *section* as a comma-separated list.

        Double quotes and whitespace around items are removed, and empty items
        are dropped, so ``key =`` is an empty list and ``"lo, eth0"`` is
        ``['lo', 'eth0']``.

        Parameters:
            section - Section name, e.g. ``network``.
            name    - Key name, e.g. ``device_ignore``.
            default - Returned if the section or key doesn't exist; None means [].

        Returns:
            A list of strings, or *default*.
        '''
        if default is None:
            value = []
        else:
            value = default

        try:
            temp = self.config[section][name]
            # An empty value ("key =") is an empty list, not [""].
            value = [ n.replace('"','').strip() for n in temp.split(",")]
            value = [ n for n in value if n ]
        except (KeyError, configparser.Error):
            pass
        return value
