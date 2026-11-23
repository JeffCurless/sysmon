#!/usr/bin/python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

"""
Hardware data collection for sysmon: CPU load, model, temperature and fan
speed, per-drive I/O and temperature, and per-interface network throughput.

Kernel files are read through ``PROC_ROOT`` and ``SYS_ROOT`` so the tests can
point them at a fake /proc and /sys.  CPU temperature comes from gpiozero;
drive temperatures from ``sudo smartctl``.
"""
import glob
import os
import re
import shlex
import subprocess
import time

from gpiozero import CPUTemperature

# Filesystem roots for the kernel interfaces read below.  These are looked up at
# call time, so tests can point them at a directory of recorded fixture files.
PROC_ROOT = "/proc"
SYS_ROOT  = "/sys"

# smartctl device types, e.g. sat, sat,12, nvme, usbjmicron,0, megaraid,3, areca,1/2
_SMARTCTL_TYPE = re.compile(r"[A-Za-z0-9_+,/-]{1,32}")


def smartctl_device_args( value: str | None ) -> list[str] | None:
    '''
    Validate a ``[smartctl]`` setting.  smartctl runs as root (through sudo),
    so the only option accepted is the device type: ``-d TYPE``, ``-dTYPE`` or
    ``--device=TYPE``.  Anything else (drive-changing options such as ``-s``,
    ``--set`` or ``-t``, extra arguments, or odd characters) is rejected.

    Returns:
        ``["-d", TYPE]`` for a valid setting, otherwise None.
    '''
    if not value:
        return None
    try:
        words = shlex.split( value )
    except ValueError:
        return None
    if len(words) == 2 and words[0] == "-d":
        dev_type = words[1]
    elif len(words) == 1 and words[0].startswith("--device="):
        dev_type = words[0][len("--device="):]
    elif len(words) == 1 and words[0].startswith("-d") and len(words[0]) > 2:
        dev_type = words[0][2:]
    else:
        return None
    if not _SMARTCTL_TYPE.fullmatch( dev_type ) or dev_type.startswith("-"):
        return None
    return ["-d", dev_type]


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
    Per-interval I/O statistics for one block device, from
    ``/sys/block/<device>/stat``.

    Each read returns how much every counter advanced since the previous read
    (see ``_getStats``).  The file is read once per call, so all the counters in
    one result describe the same interval; read everything you need from one
    ``readAllStats`` call rather than calling several properties in a row.

    See: https://www.kernel.org/doc/html/latest/block/stat.html

    Parameters:
        device - Device name as listed in /sys/block, e.g. ``nvme0n1``.
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
        Advance one interval and return every counter's change since the
        previous read.

        Returns:
            ``NUM_FIELDS`` integers, in the kernel's field order.
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
    I/O statistics and temperatures for every drive at once.

    Drives are discovered from /sys/block.  Loop and RAM devices (anything
    whose name contains ``loop`` or ``ram``, including zram) are always left
    out, as is every drive named in *driveIgnoreList*.

    Parameters:
        driveIgnoreList - Drive names to leave out, e.g. ``['mmcblk0']``.
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
        # Drives whose rejected [smartctl] setting has already been reported.
        self._badSmartctl: set[str] = set()

    @property
    def drives(self) -> list[str]:
        '''Names of the drives being monitored, sorted.'''
        return self._drives

    def driveSize( self, _drive: str ) -> int:
        '''
        Size of *_drive* in bytes.

        Parameters:
            _drive - Drive name, e.g. ``nvme0n1``.

        Returns:
            The size in bytes, or 0 if it can't be read.
        '''
        try:
            with open(f'{SYS_ROOT}/block/{_drive}/size', encoding='utf-8') as f:
                return int(f.read().strip()) * 512
        except (OSError, ValueError):
            return 0

    def driveTemp(self,_drive:str, extracmd = None) -> float:
        '''
        Read a drive's temperature from ``sudo smartctl -A /dev/<drive>``.

        The NVMe ``Temperature:`` line is used if present, then SMART attribute
        194, then 190; drives differ in which they report.

        Parameters:
            _drive   - Drive name, e.g. ``sda``.
            extracmd - The drive's ``[smartctl]`` setting.  Only ``-d TYPE`` is
                       accepted (see ``smartctl_device_args``); anything else is
                       reported once and ignored.

        Returns:
            The temperature in degrees C, or 0.0 if it can't be read.
        '''
        cmd = ['sudo', 'smartctl']
        try:
            if extracmd:
                device_args = smartctl_device_args( extracmd )
                if device_args is not None:
                    cmd += device_args
                elif _drive not in self._badSmartctl:
                    self._badSmartctl.add( _drive )
                    print( f"Ignoring [smartctl] {_drive} = {extracmd!r}: only '-d TYPE' is allowed" )
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
        Sectors read and written by each drive since the previous call.

        Returns:
            A dict mapping drive name to a ``(read, written)`` tuple.
        '''
        curData = {}
        for _ in self._stats:
            curData[_.name] = _.readWriteSectors
        return curData

    def readWriteBytes( self ) -> dict[str,tuple[int,int]]:
        '''
        Bytes read and written by each drive since the previous call
        (sectors x 512).

        Returns:
            A dict mapping drive name to a ``(read, written)`` tuple.
        '''
        curData = {}
        for _ in self._stats:
            curData[_.name] = _.readWriteBytes
        return curData

class CPUInfo:
    '''The CPU's board model, temperature, and (on a Pi 5 / CM5) fan speed.'''
    def __init__( self ) -> None:
        # gpiozero's CPUTemperature reads the sensor as soon as it is created,
        # so it is opened lazily: a machine without the sensor (or a sensor
        # that is briefly unreadable) must not stop the application starting.
        self._cputemp : CPUTemperature | None = None
        # The board model cannot change at runtime, so detect it once.
        self._model = self._cpuModel()

    def _cpuModel( self ) -> int:
        '''
        Work out the Raspberry Pi model from the ``Model`` line of /proc/cpuinfo.
        Compute Modules count as their board equivalent (CM5 is 5, CM4 is 4).

        Returns:
            5, 4 or 3, or 0 if the model is unknown or cpuinfo can't be read.
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
        CPU temperature in degrees C, read through gpiozero's CPUTemperature.

        The sensor is opened on first use, and opening is retried on each call
        until it succeeds.

        Returns:
            The temperature as a float.

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
        CPU fan speed in RPM, from the Pi 5 cooling fan's hwmon ``fan1_input``.

        Returns:
            The speed as a float; 0.0 on other models, or if it can't be read.
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
    Per-core CPU utilization, from /proc/stat.

    The first reading is taken when the object is created, so the first call
    to ``getPercentages`` covers the time since then, however long that was;
    later calls each cover the time since the previous call.
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
        Read the per-core counters (``cpu0``, ``cpu1``, ...) from /proc/stat;
        the all-CPU ``cpu`` line is skipped.  Any number of cores is supported.

        Returns:
            A dict mapping core name to ``(total, idle)`` jiffies since boot.
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
        Utilization of each core since the previous call, as a percentage:

            utilization = (total - idle) / total * 100

        where *total* and *idle* are how much those counters advanced.  Called at
        regular intervals, this gives each interval's utilization.

        Returns:
            A dict mapping core name to its utilization (0-100) as a float.
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
        '''Core names, in /proc/stat order (e.g. ``['cpu0', 'cpu1']``).'''
        return self._names

    def __len__(self) -> int:
        '''Number of CPU cores.'''
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
