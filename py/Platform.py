# Copyright (c) 2014 Adafruit Industries
# Author: Tony DiCola
# Modified for LightShowPi - Simplified for Pi 3+ only

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import platform
import re

# Platform identification constants.
UNKNOWN = 0
RASPBERRY_PI = 1

# Supported Pi versions (3, 4, 5)
PI_3 = 3
PI_4 = 4
PI_5 = 5


def platform_detect():
    """Detect if running on Raspberry Pi 3+ and return the platform type.

    Returns:
        RASPBERRY_PI if running on Pi 3, 4, or 5
        UNKNOWN otherwise

    Note: This version only supports Raspberry Pi 3, 4, and 5.
    Pi 1, 2, Zero, and other platforms are not supported.
    """
    pi = pi_version()
    if pi is not None:
        return RASPBERRY_PI

    # Not a supported Raspberry Pi
    return UNKNOWN


def pi_version():
    """Detect the version of the Raspberry Pi.

    Returns:
        3, 4, or 5 for Pi 3, 4, or 5 respectively
        None if not a supported Pi (includes Pi 1, 2, Zero, and non-Pi systems)

    Supported Hardware:
        - Raspberry Pi 3 (Model B, B+, A+) - BCM2835/BCM2837
        - Raspberry Pi 4 (Model B) - BCM2711
        - Raspberry Pi 5 - BCM2712

    Unsupported (will return None):
        - Raspberry Pi 1 (too old, insufficient performance)
        - Raspberry Pi 2 (outdated, insufficient performance)
        - Raspberry Pi Zero (underpowered for real-time audio)
        - Other platforms
    """
    try:
        with open('/proc/cpuinfo', 'r') as infile:
            cpuinfo = infile.read()
    except (FileNotFoundError, PermissionError):
        # Not running on Linux/Pi
        return None

    # Try new format first (Model field - newer kernels/OS)
    # This is more specific and directly tells us which Pi it is
    model = get_pi_model(cpuinfo)
    if model:
        if 'Pi 5' in model or 'Raspberry Pi 5' in model:
            return PI_5
        elif 'Pi 4' in model or 'Raspberry Pi 4' in model:
            return PI_4
        elif 'Pi 3' in model or 'Raspberry Pi 3' in model or 'Zero 2' in model:
            return PI_3
        # If it's Pi 1, 2, Zero, etc. - return None (unsupported)
        return None

    # Fall back to old format (Hardware field - older kernels/OS)
    match = re.search(r'^Hardware\s+:\s+(\w+)$', cpuinfo,
                      flags=re.MULTILINE | re.IGNORECASE)

    if not match:
        return None

    hardware = match.group(1)

    # Pi 4
    if hardware == 'BCM2711':
        return PI_4

    # Pi 5
    if hardware == 'BCM2712':
        return PI_5

    # Pi 3 uses BCM2835 on 4.9+ kernel or BCM2837 on older kernels
    if hardware == 'BCM2835' or hardware == 'BCM2837':
        # BCM2835 is also used by Pi 1 and Zero, so we need to verify
        # this is actually a Pi 3 (not possible without Model field)
        # On older systems without Model field, we have to assume it's supported
        # This is safe because Pi 1/2/Zero users are unlikely to be on modern LightShowPi
        return PI_3

    # Unsupported hardware (Pi 1: BCM2708, Pi 2: BCM2709, etc.)
    return None


def get_pi_model(cpuinfo):
    """Extract Raspberry Pi model from cpuinfo.

    Args:
        cpuinfo: Contents of /proc/cpuinfo

    Returns:
        Model string (e.g., "Pi 3 Model B") or None
    """
    match = re.search(r'^Model\s+:\s+(.+)$', cpuinfo,
                      flags=re.MULTILINE | re.IGNORECASE)
    if match:
        return match.group(1)

    # Fallback: try to get from revision code
    match = re.search(r'^Revision\s+:\s+\w+(\w{2,4})$', cpuinfo,
                      flags=re.MULTILINE | re.IGNORECASE)
    if match:
        revision = match.group(1).lower()
        # Pi 3 revision codes
        if revision in ["82", "83"]:
            return "Pi 3 Model B"
        if revision in ["d3"]:
            return "Pi 3 Model B+"
        if revision in ["e0"]:
            return "Pi 3 Model A+"
        # Pi 4 revision codes
        if revision in ["11", "14", "c3"]:
            return "Pi 4 Model B"
        # Pi 5 revision codes (to be added when known)
        if revision in ["17"]:  # Placeholder, update when Pi 5 codes are known
            return "Pi 5"

    return None


def get_hardware_info():
    """Get detailed hardware information about the Raspberry Pi.

    Returns:
        dict with keys:
            - version: Pi version (3, 4, 5, or None)
            - model: Model string
            - platform: RASPBERRY_PI or UNKNOWN
            - supported: True if hardware is supported
    """
    version = pi_version()

    info = {
        'version': version,
        'model': 'Unknown',
        'platform': RASPBERRY_PI if version else UNKNOWN,
        'supported': version is not None
    }

    if version:
        try:
            with open('/proc/cpuinfo', 'r') as infile:
                cpuinfo = infile.read()
            model = get_pi_model(cpuinfo)
            if model:
                info['model'] = model
        except:
            pass

    return info


# GPIO Header information (all supported Pi models use 40-pin header)
header40 = """Raspberry Pi 3, 4, 5 - 40-pin GPIO Header
                         +=========+
         POWER  3.3VDC   | 1 . . 2 |  5.0VDC   POWER
      I2C SDA1  GPIO  2  | 3 . . 4 |  5.0VDC   POWER
      I2C SCL1  GPIO  3  | 5 . . 6 |  GROUND
      CPCLK0    GPIO  4  | 7 . . 8 |  GPIO 14  TxD UART
                GROUND   | 9 . . 10|  GPIO 15  RxD UART
                GPIO 17  |11 . . 12|  GPIO 18  PCM_CLK/PWM0
                GPIO 27  |13 . . 14|  GROUND
                GPIO 22  |15 . . 16|  GPIO 23
         POWER  3.3VDC   |17 . . 18|  GPIO 24
      SPI MOSI  GPIO 10  |19 .   20|  GROUND
      SPI MISO  GPIO  9  |21 . . 22|  GPIO 25
      SPI SCLK  GPIO 11  |23 . . 24|  GPIO  8  CE0 SPI
                GROUND   |25 . . 26|  GPIO  7  CE1 SPI
 I2C ID EEPROM  SDA0     |27 . . 28|  SCL0     I2C ID EEPROM
        GPCLK1  GPIO  5  |29 . . 30|  GROUND
        CPCLK2  GPIO  6  |31 . . 32|  GPIO 12  PWM0
          PWM1  GPIO 13  |33 . . 34|  GROUND
   PCM_FS/PWM1  GPIO 19  |35 . . 36|  GPIO 16
                GPIO 26  |37 . . 38|  GPIO 20  PCM_DIN
                GROUND   |39 . . 40|  GPIO 21  PCM_DOUT
                         +=========+

Note: All GPIO numbering uses BCM mode (not physical pin numbers)
I2C bus is always bus 1 on Pi 3+
"""


def get_gpio_header():
    """Get GPIO header pinout information.

    Returns:
        String with ASCII art header diagram
    """
    return header40


# I2C bus is always 1 on Pi 3+
def get_i2c_bus():
    """Get the I2C bus number for this Raspberry Pi.

    Returns:
        1 (all Pi 3+ models use I2C bus 1)
    """
    return 1
