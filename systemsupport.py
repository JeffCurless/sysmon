#!/usr/bin/python3
"""
Hardware data collection for sysmon: CPU load and temperature, CPU fan
speed, per-drive I/O and temperature, and per-interface network throughput.

CPU temperature comes from gpiozero, which supports the Raspberry Pi 5 (and,
presumably, will be the direction for all prior Pi versions).
"""
import glob
import os
import shlex
import subprocess
import time

from gpiozero import CPUTemperature

# Filesystem roots for the kernel interfaces read below.  These are looked up at
# call time, so tests can point them at a directory of recorded fixture files.
PROC_ROOT = "/proc"
SYS_ROOT  = "/sys"


def counter_delta( current: int, previous: int ) -> int:
    '''
    Amount a kernel counter advanced between two readings.  If the counter
    went backwards, the device was re-created (a drive re-attached, a VPN
    interface going down and up) and its counters restarted from zero, so
    everything counted since then is *current*.
    '''
    return current - previous if current >= previous else current

class DriveStats:
    '''
    DriveStat class -

    This class gets the drive statistics from sysfs for the device passed
    in.  There are several statistics can can be obtained.  Note that since
    all of the data is pulled at the same time, it is upto the caller to
    make sure all the stats needed are obtained at the same time.

    See: https://www.kernel.org/doc/html/latest/block/stat.html

    Parameters:
        device - the name of the device to track
    '''

    READ_IOS        = 0
    READ_MERGES     = 1
    READ_SECTORS    = 2
    READ_TICKS      = 3
    WRITE_IOS       = 4
    WRITE_MERGES    = 5
    WRITE_SECTORS   = 6
    WRITE_TICKS     = 7
    IN_FLIGHT       = 8
    IO_TICKS        = 9
    TIME_IN_QUEUE   = 10
    DISCARD_IOS     = 11
    DISCARD_MERGES  = 12
    DISCARD_SECTORS = 13
    DISCARD_TICS    = 14
    FLUSH_IOS       = 15
    FLUSH_TICKS     = 16

    NUM_FIELDS      = 17     # fields in the newest kernels; older ones report 11 or 15

    def __init__( self, device:str ):
        self._device = device
        # Previous good reading, or None if there isn't one yet (or the last
        # read failed).  Deltas are only reported between two good readings.
        self._last : list[int] | None = self._readStats()

    def _readStats( self ) -> list[int] | None:
        '''
        Read the cumulative disk statistics from sysfs.

        The sysfs file is a single line so all counters are coherent at the
        same instant.  Statistics accumulate from the time the device came
        online; ``_getStats`` subtracts consecutive readings.

        Returns:
            The counters, or None if the file could not be read or parsed.
        '''
        try:
            with open( f"{SYS_ROOT}/block/{self._device}/stat", "r", encoding="utf8") as f:
                curStats = f.readline().strip().split(" ")
                return [int(l) for l in curStats if l]
        except (OSError, ValueError) as e:
            print( f"Failure reading disk statistics for {self._device} error {e}" )
            return None

    def _getStats( self ) -> list[int]:
        '''
        Read the device statistics and return how much each counter advanced
        since the previous call.

        If this reading or the previous one failed there is no valid interval,
        so all zeros are returned (rather than the cumulative totals, which
        would show as a huge one-off spike); the next good reading becomes the
        new baseline.

        Returns:
            ``NUM_FIELDS`` deltas, zero-padded for kernels with fewer fields.
        '''
        current = self._readStats()
        if current is None or self._last is None:
            curData = []
        else:
            curData = [ counter_delta(d, p) for d, p in zip( current, self._last ) ]
        self._last = current
        return curData + [0] * (DriveStats.NUM_FIELDS - len(curData))

    @property
    def name(self) -> str:
        '''Device name being tracked (e.g. ``nvme0n1``).'''
        return self._device

    @property
    def readAllStats( self ) -> list[int]:
        '''
        read all of the drive statisics from sysfs for the device.

        Returns
            A list of all of the device stats
        '''
        return self._getStats()

    @property
    def readSectors( self )-> int:
        '''Sectors read since the previous stats read.'''
        return self._getStats()[DriveStats.READ_SECTORS]

    @property
    def writeSectors( self ) -> int:
        '''Sectors written since the previous stats read.'''
        return self._getStats()[DriveStats.WRITE_SECTORS]

    @property
    def discardSectors( self ) -> int:
        '''Sectors discarded since the previous stats read.'''
        return self._getStats()[DriveStats.DISCARD_SECTORS]

    @property
    def readWriteSectors( self ) -> tuple[int,int]:
        '''(read, written) sectors since the previous stats read.'''
        curData = self._getStats()
        return (curData[DriveStats.READ_SECTORS],curData[DriveStats.WRITE_SECTORS])

    @property
    def readWriteBytes( self ) -> tuple[int,int]:
        '''(read, written) bytes since the previous stats read.'''
        curData = self._getStats()
        return (curData[DriveStats.READ_SECTORS]*512,curData[DriveStats.WRITE_SECTORS]*512)

class multiDriveStat():
    '''
    This class allow for monitoring multiple drives at the same time. There are
    two mechanisms used to create this class.  The first is with no parameters,
    in this case, the system will automatically grab all of the drives and setup
    to monitor them.

    The second method is to provide this class with a list of drives to monitor.
    In this cased all of the drives that are NOT on that list are filtered out and
    only the drives left will be processed.

    If there is a missing drive from the filter, that drive is eliminated.

    '''
    def __init__(self, driveIgnoreList : list[str]|None=None):
        #
        # Get all drives
        #
        if driveIgnoreList is None:
            driveIgnoreList = []

        # Exclude loop and ram devices, matching the old `grep -v -e loop -e ram`
        self._drives = [ d for d in sorted(os.listdir(f'{SYS_ROOT}/block'))
                         if 'loop' not in d and 'ram' not in d
                         and d not in driveIgnoreList ]
        self._stats = [ DriveStats(_) for _ in self._drives ]

    @property
    def drives(self) -> list[str]:
        '''
        This attribute is used to list all of the drives that are being monitored

        Returns:
            A list of drives
        '''
        return self._drives

    def driveSize( self, _drive: str ) -> int:
        '''
        This function is called to obtain the size of the drive requested.

        Parameters:
            _drive - the drive to lookup

        Returns:
            The size in bytes, or 0 if the drive does not exist
        '''
        try:
            with open(f'{SYS_ROOT}/block/{_drive}/size', encoding='utf-8') as f:
                return int(f.read().strip()) * 512
        except (OSError, ValueError):
            return 0

    def driveTemp(self,_drive:str, extracmd = None) -> float:
        '''
        Get the drive temperature using smart data.  There are three basic temperature
        settings we can read, smart ID 194, 190 and the Temperature: value.  These are
        depenent on the drive, so look for all of them, and depending on the result, we
        get the value.

        Parameters:
            _drive   : The device we wish to scan
            extraCmd : An optional additional command to send to the device.

        Returns:
            The temperature as a float, or zero if there is an error.
        '''
        cmd = ['sudo', 'smartctl']
        try:
            if extracmd:
                cmd += shlex.split(extracmd)
            cmd += ['-A', f'/dev/{_drive}']

            # smartctl's exit status is a bitmask that is often non-zero even
            # when the attributes were read, so the return code is not checked.
            smartOutRaw = subprocess.run( cmd, capture_output=True, text=True,
                                          timeout=5, check=False ).stdout
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print( f"Could not launch {' '.join(cmd)} error is {error}" )
            return 0.0

        smartOut = [ l for l in smartOutRaw.split('\n') if l]
        for smartAttr in ["Temperature:","194","190"]:
            try:
                line = [l for l in smartOut if l.startswith(smartAttr)][0]
                parts = [p for p in line.replace('\t',' ').split(' ') if p]
                if smartAttr == "Temperature:":
                    return float(parts[1])
                return float(parts[9])
            except (IndexError, ValueError):
                pass

        return float(0.0)

    def readWriteSectors( self )-> dict[str,tuple[int,int]]:
        '''
        Obtain the number of sectors read and written since the last
        time this function as called.

        Returns:
             A dictionary of the data, they key is the drive name, the value is the
             read/write tuple.
        '''
        curData = {}
        for _ in self._stats:
            curData[_.name] = _.readWriteSectors
        return curData

    def readWriteBytes( self ) -> dict[str,tuple[int,int]]:
        '''
        Just like the readWriteSectors function but returns the data in Bytes

        '''
        curData = {}
        for _ in self._stats:
            curData[_.name] = _.readWriteBytes
        return curData

class CPUInfo:
    '''
    This class deals with getting data about a Raspberry PI 5 CPU fan.

    '''
    def __init__( self ) -> None:
        # gpiozero's CPUTemperature reads the sensor as soon as it is created,
        # so it is opened lazily: a machine without the sensor (or a sensor
        # that is briefly unreadable) must not stop the application starting.
        self._cputemp : CPUTemperature | None = None
        # The board model cannot change at runtime, so detect it once.
        self._model = self._cpuModel()

    def _cpuModel( self ) -> int:
        '''
        Check for the cpu model.  Scan cpuinfo to see if we can locate a string that
        matches something we are looking for.

        Return:
            Model of the Raspberry PI.  This treats the Comput Modules the same as
            standard model B's
        '''
        try:
            with open( f"{PROC_ROOT}/cpuinfo", encoding="utf-8" ) as f:
                # Equivalent of `grep Model /proc/cpuinfo`
                data = "".join( l for l in f if "Model" in l )
        except OSError:
            return 0
        if "Compute Module 5" in data or "Raspberry Pi 5" in data:
            return 5
        if "Compute Module 4" in data or "Raspberry Pi 4" in data:
            return 4
        if "Raspberry Pi 3" in data:
            return 3
        return 0

    @property
    def model( self ) -> int:
        '''
        Raspberry Pi model number (5, 4, 3, or 0 if unknown), detected once
        at construction.
        '''
        return self._model

    @property
    def temperature( self ) -> float:
        '''
        Obtain the temperature of the CPU.  This utilizes a GPIO call to obtain
        the CPU temp, via the CPUTemperature object from gpiozero

        The sensor is opened on first use, and opening is retried on each
        call until it succeeds.

        Returns:
            A floating point number representing the temperature in degrees C

        Raises:
            OSError or ValueError if the sensor is missing or unreadable
            (callers treat this as "no reading").
        '''
        if self._cputemp is None:
            self._cputemp = CPUTemperature(
                sensor_file=f'{SYS_ROOT}/class/thermal/thermal_zone0/temp' )
        return self._cputemp.temperature

    @property
    def CPUFanSpeed( self ) -> float:
        '''
        Obtain the speed of the CPU fan.  This is based on monitoring the hardware
        monitor, assuming that fan1_input is the fan connected to the CPU.

        Return:
            The fanspeed as a floating point number
        '''
        speed = 0
        if self._model == 5:
            try:
                matches = glob.glob(f'{SYS_ROOT}/devices/platform/cooling_fan/hwmon/*/fan1_input')
                if matches:
                    with open(matches[0], encoding='utf-8') as f:
                        speed = int(f.read().strip())
            except (OSError, ValueError) as error:
                print( f"Could not determine fan speed, error {error}" )
        return float(speed)

class CPULoad:
    '''
    A class to help with obtaining the CPU load of the system. If there is more information
    needed, we can add to this.

    Note:
       This code automatically attempts to load the data from the system to initialize the
       object with names, and an initial set of data.

       This may result in th first actual call return some not very consistent values, for
       the time period being observed, but that difference is minimal.  In otherwords if we
       the period of time being measured is 1 second, and it's been a minute since this class
       was initialized, the first period reported will be CPU load over the minute, not 1 second,
       and the second period reported will be for a second...

       This is usually not an issue.
    '''
    def __init__( self ) -> None:
        #
        # Get the current data
        #
        self._previousData : dict[str,tuple[int,int]] = self._getRawData()
        self._names : list[str] = []

        #
        # For each CPU, reset the total and idle amount, and create the list
        # of names
        #
        for _item in self._previousData:
            self._previousData[_item] = (0,0)
            self._names.append(_item)

    def _getRawData( self ) -> dict[str,tuple[int,int]]:
        '''
        Obtain the raw CPU data from the system (located in /prop/stat), and
        return just the cpu0 -> cpux values.  No assumption is made on the number of
        cpus.

        Returns:
            A dictionary is returned, the format is name = (total, idle).  The total
            time and idle time are use to determine the percent utilization of the system.
        '''
        result = {}
        with open(f"{PROC_ROOT}/stat", "r",encoding="utf8") as f:
            allLines = f.readlines()
            for line in allLines:
                cpu = line.replace('\t', ' ').strip().split()
                if (len(cpu[0]) > 3) and (cpu[0][:3] == "cpu"):
                    total = 0
                    idle  = 0
                    for _index in range( 1, len(cpu)):
                        total += int(cpu[_index])
                        if _index in (4, 5):
                            idle += int(cpu[_index])
                    result[cpu[0]] = (total,idle)
        return result

    def getPercentages( self ) -> dict[str,float]:
        '''
        Obtain the percent CPU utilization of the system for a period of time.

        This routine gets the current raw data from the system, and then performs
        a delta from the prior time this function was called.  This data is then run
        through the following equation:

             utilization = ((total - idle)/total) * 100

        If the snapshots are taken at relativy consistent intervals, the CPU
        utilization in percent, is reasonably lose to the actual percentage.

        Returns:
            A dictionary consisting of the name of the CPU, and a floating point
            number representing the current utilization of that CPU.
        '''
        results = {}
        current = self._getRawData()
        for _item, (cur_total, cur_idle) in current.items():
            total = cur_total - self._previousData[_item][0]
            idle  = cur_idle  - self._previousData[_item][1]
            percent = 0.0 if total == 0 else ((total - idle) / total) * 100
            results[_item] = round(percent,2)
        self._previousData = current
        return results

    @property
    def cpuNames( self ) -> list[str]:
        '''
        Get a list of CPU names from the system.

        Returns:
            a list of strings
        '''
        return self._names

    def __len__(self) -> int:
        '''
        handle getting the length (or count of CPU's).

        Returns:
            Number of CPU's
        '''
        return len(self._previousData)

class NetworkLoad:
    '''
    Tracks per-interface network throughput by reading byte counters from
    ``/sys/class/net/<iface>/statistics/``.

    Each call to ``stats`` returns the bytes transferred since the previous
    call, giving a per-interval throughput snapshot rather than a cumulative
    total.  Interfaces listed in *networkIgnoreList* (e.g. ``lo``) are
    excluded from monitoring.
    '''
    def __init__(self, networkIgnoreList : list[str]|None=None) -> None:
        '''
        Discover all available network interfaces, exclude any in
        *networkIgnoreList*, and record an initial byte-counter snapshot so
        that the first call to ``stats`` can return a meaningful delta.

        Parameters:
            networkIgnoreList - Interface names to exclude (e.g. ``['lo']``).
                                Defaults to an empty list if ``None``.
        '''
        if networkIgnoreList is None:
            networkIgnoreList = []
        self._networks = [ n for n in sorted(os.listdir(f'{SYS_ROOT}/class/net'))
                           if n not in networkIgnoreList ]
        self.prevStats : dict[str, tuple[int,int] | None] = {}
        for net in self._networks:
            self.prevStats[net] = self._getData(net)
    @property
    def names( self ) -> list[str]:
        '''
        Names of the network interfaces being monitored.

        Returns:
            A list of interface name strings (e.g. ``['eth0', 'wlan0']``).
        '''
        return self._networks

    def _getData( self, name : str ) -> tuple[int,int] | None:
        '''
        Read the cumulative (rx_bytes, tx_bytes) counters for *name*.

        Returns:
            The counters, or None if either could not be read or parsed.
        '''
        try:
            with open( f"{SYS_ROOT}/class/net/{name}/statistics/rx_bytes", encoding='utf-8' ) as f:
                readData = int(f.read().strip())
            with open( f"{SYS_ROOT}/class/net/{name}/statistics/tx_bytes", encoding='utf-8' ) as f:
                writeData = int(f.read().strip())
        except (OSError, ValueError) as e:
            print( f"Error reading network counters for {name}: {e}" )
            return None
        return (readData, writeData)

    @property
    def stats(self) -> dict[str,tuple[int,int]]:
        '''
        Bytes transferred on each interface since the last call to this property.

        If this reading or the previous one failed there is no valid interval,
        so ``(0, 0)`` is reported (rather than the cumulative totals, which
        would show as a huge one-off spike); the next good reading becomes the
        new baseline.

        Returns:
            A dictionary mapping interface name to a ``(rx_bytes, tx_bytes)``
            tuple representing bytes received and transmitted during the last
            measurement interval.
        '''
        data = {}
        curstats = {}
        for net in self._networks:
            current = self._getData( net )
            previous = self.prevStats[net]
            curstats[net] = current
            if current is None or previous is None:
                data[net] = (0, 0)
            else:
                data[net] = (counter_delta(current[0], previous[0]),
                             counter_delta(current[1], previous[1]))
        self.prevStats = curstats
        return data

if __name__ == "__main__":

    load = CPULoad()
    print( f"Number of CPU's = {len(load)}" )
    for i in range(3):
        time.sleep( 1 )
        percentage : dict[str,float] = load.getPercentages()
        print( f"percentage: {percentage}" )
        for item, pct in percentage.items():
            print( f"{item} : {pct:.02f}" )

    cpuinfo = CPUInfo()
    print( f"CPU Temperature = {cpuinfo.temperature}" )
    print( f"CPU Fan Speed   = {cpuinfo.CPUFanSpeed}" )
    print( f"CPU Model       = {cpuinfo.model}" )

    test = multiDriveStat()
    print( test.drives )
    for drive in test.drives:
        print( f"Drive {drive} size is {test.driveSize( drive )}" )
    print( test.readWriteSectors() )

    network = NetworkLoad( ['lo','eth0'])
    print( f"Networks Available: {network.names}" )
    while True:
        print( f"Stats = {network.stats}" )
        time.sleep(1)
