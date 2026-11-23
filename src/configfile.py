"""
Read-only INI configuration reader for sysmon that never raises: missing
files, sections, or keys return the caller-supplied default.
"""
import configparser
from typing import Any


class ConfigClass:
    '''
    Handle a .INI style configuration file.  Every function is designed to not
    crash, and always return a default of the items are not present.

    Currently support read-only.
    '''
    def __init__( self, filename: str ) -> None:
        '''
        Load the INI configuration file at *filename*.  If the file does not
        exist or cannot be parsed, the object is still created and all
        subsequent ``getValue`` / ``getValueAsList`` calls will silently
        return their caller-supplied defaults.

        Parameters:
            filename - Absolute or relative path to the ``.ini`` file.
        '''
        self.filename = filename
        self.config   = configparser.ConfigParser()
        # True once the file has actually been read (False if it is missing).
        self.loaded   = False
        self._openConfig()

    def _openConfig(self) -> None:
        '''
        Open,, and read in the configuation file.  If the file does not exist, keep
        trying to reopen the file until the file does exist.  While this approach does
        not always help, it does allow for an application polls the configuration file
        occasionally.

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
        This routine obtains the value of the key within the specified section, if there
        is such a item.

        Parameter:
            section - Name of the section to look for
            key     - Key of the value desired
            default - Value to return if there is no key

        Returns:
            The value of the key from the section read.
        '''
        value = default
        try:
            value = self.config[section][key].replace('"','').strip()
        except (KeyError, configparser.Error):
            value = default
        return value

    def getValueAsList( self, section : str, name : str, default : list[Any]|None = None) -> list[str]:
        '''
        This routine looks for the key in the specified section and returns the data if
        it exists, if not the default value is returned.

        Parameters:
            section - Section to look for
            key     - Key to return the value of
            default - If they key or section does not exist, return this value

        Returns:
            a List of items
        '''
        if default is None:
            value = []
        else:
            value = default

        try:
            temp = self.config[section][name]
            value = [ n.replace('"','').strip() for n in temp.split(",")]
        except (KeyError, configparser.Error):
            pass
        return value

if __name__ == "__main__":
    cfg = ConfigClass( "test.ini" )
    print( f"Value = {cfg.getValue( 'temperature', 'ignore' )}" )
    print( f"Value = {cfg.getValueAsList( 'temperature', 'ignore' )}" )
    print( f"Value = {cfg.getValue( 'performance', 'ignore' )}" )
    print( f"Value = {cfg.getValueAsList( 'performance', 'ignore' )}" )
    print( f"Value = {cfg.getValueAsList( 'Fruit', 'thing', ['this'] )}" )

    drive = cfg.getValue( 'smartctl', 'sda' )
    print( f"drive = {drive}" )

    cfg = ConfigClass( "missingfile.ini" )
