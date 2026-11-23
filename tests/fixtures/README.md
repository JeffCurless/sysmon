# Test fixtures

Input data for the tests in `tests/`. The tests copy these into a temporary
fake `/proc` and `/sys` tree (see `conftest.py`), so no real hardware is read.

| File | Source |
|------|--------|
| `proc/stat.1`, `proc/stat.2` | **Recorded** from a Raspberry Pi CM5, 1 s apart |
| `proc/cpuinfo.cm5` | **Recorded** from a Raspberry Pi CM5 (serial number zeroed) |
| `smartctl/nvme0n1.txt` | **Recorded**: `sudo smartctl -A /dev/nvme0n1` (NVMe, reports `Temperature:`) |
| `smartctl/mmcblk0.txt` | **Recorded**: `sudo smartctl -A /dev/mmcblk0` (SD card, no SMART support) |
| `smartctl/sata_194.txt` | **Hand-written** in smartctl's ATA format: attributes 190 and 194 both present |
| `smartctl/usb_bridge_190.txt` | **Hand-written** in smartctl's ATA format: only attribute 190 present |

To add a real sample from another drive, save its output here, e.g.
`sudo smartctl -d sat -A /dev/sda > smartctl/<name>.txt`, and add a case to
`test_systemsupport.py::test_drive_temp_parses_smartctl_output`. Zero out any
serial numbers before committing.
