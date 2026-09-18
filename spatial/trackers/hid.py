"""Read Linux's HID inventory without opening unrelated input devices."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class HidDevice:
    path: str
    sysfs: Path
    bus: int
    vendor: int
    product: int
    name: str
    unique: str

    def descriptor(self):
        return (self.sysfs / "report_descriptor").read_bytes()


def address(value):
    """Normalize only genuine Bluetooth addresses; never match a device name."""
    if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", value):
        return value.upper()
    return None


def inventory(sysfs="/sys/class/hidraw", devdir="/dev"):
    result = []
    for entry in sorted(Path(sysfs).glob("hidraw*")):
        if not re.fullmatch(r"hidraw\d+", entry.name):
            continue
        device = entry / "device"
        try:
            fields = dict(line.split("=", 1) for line in
                          (device / "uevent").read_text().splitlines() if "=" in line)
            bus, vendor, product = (int(part, 16) for part in fields["HID_ID"].split(":"))
            result.append(HidDevice(str(Path(devdir) / entry.name), device,
                                    bus, vendor, product,
                                    fields.get("HID_NAME", ""), fields.get("HID_UNIQ", "")))
        except (OSError, ValueError, KeyError):
            # Disappearance during enumeration is normal when Bluetooth reconnects.
            continue
    return result


def sony_candidate(device, selected_address):
    selected = address(selected_address)
    if (selected is None or device.bus != 5 or device.vendor != 0x054C or
            address(device.unique) != selected):
        return False
    try:
        # Exactly the Sensor/Custom usage tested by SonyTrackerLinux.
        return device.descriptor().startswith(b"\x05\x20\x09\xe1")
    except OSError:
        return False


def slime_candidate(device):
    # SlimeVR HIDCommon.kt's receiver and directly connected tracker product rules.
    return device.bus == 3 and (
        (device.vendor == 0x1209 and device.product in (0x7690, 0x7692)) or
        (device.vendor == 0x4E76 and device.product & 0xFFF0 == 0xD2D0))
