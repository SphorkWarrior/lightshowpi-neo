#!/usr/bin/env python
#
# Licensed under the BSD license.  See full license in LICENSE file.
# http://www.lightshowpi.org/
#
# Author: Todd Giles (todd@lightshowpi.org)
# Author: Tom Enos (tomslick.ca@gmail.com)
#

"""Configuration management for the lightshow.

Configuration files are all located in the <homedir>/config directory. This file contains tools to
manage these configuration files.
"""

import configparser
import ast
import csv
import datetime
try:
    import fcntl
except ImportError:
    fcntl = None
import logging
import os
import os.path
import sys
import warnings
import json
import shlex
import argparse
from collections import defaultdict

log = logging.getLogger(__name__)

# The home directory and configuration directory for the application.
HOME_DIR = os.getenv("SYNCHRONIZED_LIGHTS_HOME")

if not HOME_DIR:
    print("Need to setup SYNCHRONIZED_LIGHTS_HOME environment variable, "
          "see readme")
    sys.exit()

CONFIG_DIR = HOME_DIR + '/config'
LOG_DIR = HOME_DIR + '/logs'


def _lock_file(fp, mode):
    """Acquire file lock when fcntl is available (Linux/Pi)."""
    if fcntl is not None:
        fcntl.lockf(fp, mode)


def _as_list(list_str, delimiter=','):
    """Return a list of items from a delimited string (after stripping whitespace).

    :param list_str: string to turn into a list
    :type list_str: str

    :param delimiter: split the string on this
    :type delimiter: str

    :return: string converted to a list
    :rtype: list
    """
    return [str.strip(item).rstrip() for item in list_str.split(delimiter)]


class Configuration(object):
    """Configuration management for the lightshow.

    Configuration files are all located in the <homedir>/config directory. This file contains tools
    to manage these configuration files.
    """

    def __init__(self, param_config=None):
        self.gpio_len = None
        self.playlist = None
        self.playlist_path = None

        # path and file locations
        self.home_dir = os.getenv("SYNCHRONIZED_LIGHTS_HOME")
        self.config_dir = self.home_dir + "/config/"
        self.log_dir = self.home_dir + "/logs/"
        self.state_file = self.config_dir + "state.cfg"
        self._state_mtime = None

        self.param_config = param_config

        # ConfigParsers
        self.config = configparser.RawConfigParser(allow_no_value=True)
        self.state = configparser.RawConfigParser()

        self.state_section = 'do_not_modify'

        self.load_config()

        # Ensure state file has been created
        if not os.path.isfile(self.state_file):
            with open(self.state_file, 'w'):
                pass

        self.load_state(force=True)

        # Initialize all configuration sections
        self.hardware = None
        self.lightshow = None
        self.audio_processing = None
        self.network = None
        self.terminal = None
        self.led = None
        self.configs = None

        self.set_hardware()
        self.set_configs()
        self.set_lightshow()
        self.set_audio_processing()
        self.set_network()
        self.set_terminal()
        self.set_custom_sequences()

    def load_config(self):
        """Load config files into ConfigParser instance"""
        with open(self.config_dir + 'defaults.cfg') as f:
            self.config.read_file(f)

        overrides = list()
        if self.param_config:
            # Handle both absolute paths (e.g., from tests) and relative paths
            if os.path.isabs(self.param_config):
                overrides.append(self.param_config)
            else:
                overrides.append(self.config_dir + self.param_config)
        else:
            overrides.append(self.config_dir + "overrides.cfg")
        self.config.read(overrides)

    # handle the program state / next 3 methods
    def load_state(self, force=False):
        """Reload state from disk only when modified (or when force=True)."""
        try:
            mtime = os.stat(self.state_file).st_mtime_ns
            if not force and self._state_mtime is not None and mtime == self._state_mtime:
                return
        except OSError:
            mtime = None

        with open(self.state_file) as state_fp:
            if fcntl is not None:
                _lock_file(state_fp, fcntl.LOCK_SH)
            self.state.clear()
            self.state.read_file(state_fp, self.state_file)
            if fcntl is not None:
                _lock_file(state_fp, fcntl.LOCK_UN)

        self._state_mtime = mtime

    def get_state(self, name, default=""):
        """
        Get application state

        Return the value of a specific application state variable, or the specified
        default if not able to load it from the state file

        :param name: option to load from state file
        :type name: str

        :param default: return if not able to load option from state file
        :type default: str

        :return: the current state
        :rtype: str
        """
        try:
            return self.state.get(self.state_section, name)
        except (configparser.NoOptionError, configparser.NoSectionError):
            return default

    def update_state(self, name, value):
        """Update the application state (name / value pair)

        :param name: option name to update
        :type name: str

        :param value: value to update option name to
        :type value: str
        """
        value = str(value)
        logging.info('Updating application state {%s: %s}', name, value)

        if not self.state.has_section(self.state_section):
            self.state.add_section(self.state_section)

        self.state.set(self.state_section, name, value)

        with open(self.state_file, 'wt') as state_fp:
            if fcntl is not None:
                _lock_file(state_fp, fcntl.LOCK_EX)
            self.state.write(state_fp)
            if fcntl is not None:
                _lock_file(state_fp, fcntl.LOCK_UN)

        try:
            self._state_mtime = os.stat(self.state_file).st_mtime_ns
        except OSError:
            self._state_mtime = None

    def set_configs(self):

        configs = dict()

        if self.config.get('configs', 'led_config') == "":
            configs["led"] = list()
        else:
            configs["led"] = self.config.get('configs', 'led_config').split(",")

        configs["led_multiprocess"] = self.config.getboolean('configs','led_multiprocess')

        self.configs = Section(configs)

    def set_hardware(self):
        """
        Retrieves the hardware configuration parsing it from the Config Parser as necessary.
        """
        hrdwr = dict()
        devices = dict()
        try:
            devices = json.loads(self.config.get('hardware', 'devices'))
        except Exception as error:
            logging.error("devices not defined or not in JSON format." + str(error))
        hrdwr["devices"] = devices

        for device_type, settings in hrdwr["devices"].items():
            for count in range(len(settings)):
                for k, v in settings[count].items():
                    settings[count][k] = v

        g_pins = self.config.get('hardware', 'gpio_pins')
        try:
            hrdwr["gpio_pins"] = list(map(int, g_pins.split(",")))
        except (AttributeError, ValueError):
            hrdwr["gpio_pins"] = list()

        self.gpio_len = len(hrdwr["gpio_pins"])

        hrdwr["gpio_len"] = len(hrdwr["gpio_pins"])
        hrdwr["physical_gpio_len"] = hrdwr["gpio_len"]

        temp = self.config.get('hardware', 'pin_modes').split(",")
        if len(temp) != 1:
            hrdwr["pin_modes"] = temp
        else:
            hrdwr["pin_modes"] = [temp[0] for _ in range(self.gpio_len)]

        hrdwr["is_pin_pwm"] = [True if pin == "pwm" else False for pin in hrdwr["pin_modes"]]

        hrdwr["pwm_range"] = int(self.config.get('hardware', 'pwm_range'))
        hrdwr["piglow"] = self.config.getboolean('hardware', 'piglow')

        temp = self.config.get('hardware', 'active_low_mode').split(",")
        if len(temp) != 1:
            hrdwr["active_low_mode"] = [self.config._convert_to_boolean(t) for t in temp]
        else:
            hrdwr["active_low_mode"] = [self.config._convert_to_boolean(temp[0]) for _ in range(self.gpio_len)]

        self.hardware = Section(hrdwr)

    def set_terminal(self):
        """
        Retrieves the terminal configuration parsing it from the Config Parser as necessary.
        """
        term = dict()
        term["enabled"] = self.config.getboolean('terminal', 'enabled')
        self.terminal = Section(term)

    def set_custom_sequences(self):
        scs = dict()
        scs["timing"] = self.config.getint('custom_sequences', 'timing')
        scs["brightness_range"] = self.config.getint('custom_sequences', 'brightness_range')
        self.custom_sequences = Section(scs)

    def set_led(self, config_file):
        """
        Retrieves the led configuration parsing it from the Config Parser as necessary.
        """

        self.led_config = configparser.RawConfigParser(allow_no_value=True)
        with open(self.config_dir + config_file) as f:
            self.led_config.read_file(f)

        led = dict()

        lc = self.led_config.get('led', 'led_configuration').upper()
        lconn = self.led_config.get('led', 'led_connection').upper()
        st = self.led_config.get('led', 'strip_type').upper()
        sst = ["APA102", "LPD8806", "WS2801", "WS2811", "WS2812",
               "WS2812B", "NEOPIXEL", "WS2811_400", "APA104",
               "TM1803", "TM1804", "TM1809", "UCS1903", "SM16716",
               "LPD1886", "P9813"]

        if lconn in ["SERIAL", "SPI", "SACN"]:
            led["led_configuration"] = lc
            led["led_connection"] = lconn
        else:
            led["led_configuration"] = None
            led["led_connection"] = None

        if st in sst[0:5] and lconn == "SPI":
            led["strip_type"] = st
        elif lconn in ["SERIAL"] and st in sst:
            led["strip_type"] = st
        else:
            led["strip_type"] = None

        led["enable_multicast"] = self.led_config.getboolean('led', 'enable_multicast')

        # if multicast is enabled setup broadcast flag and broadcast address
        if led["enable_multicast"]:
            led["sacn_address"] = "239.255.0.1"
            led["sacn_broadcast"] = True
        else:
            led["sacn_address"] = self.led_config.get('led', 'sacn_address')
            led["sacn_broadcast"] = False

        led["sacn_port"] = self.led_config.getint('led', 'sacn_port')
        led["universe_boundary"] = self.led_config.getint('led', 'universe_boundary')
        led["universe_start"] = self.led_config.getint('led', 'universe_start')

        c_order = self.led_config.get('led', 'channel_order').upper()
        if c_order in ["RGB", "RBG", "GRB", "GBR", "BRG", "BGR"]:
            led["channel_order"] = c_order
        else:
            led["channel_order"] = "RGB"

        led_count = self.led_config.getint('led', 'led_channel_count')

        g_leds = self.led_config.get('led', 'custom_per_channel')
        try:
            led["custom_per_channel"] = list(map(int, g_leds.split(",")))
            led["led_channel_count"] = len(led["custom_per_channel"])
            led_count = len(led["custom_per_channel"])
        except (AttributeError, ValueError, TypeError):
            led["custom_per_channel"] = list()
            led["led_channel_count"] = led_count

        led["led_channel_configuration"] = self.led_config.get('led', 'led_channel_configuration').upper()

        led_count = self.led_config.getint('led', 'led_channel_count')
        led["led_channel_count"] = led_count
        if led["led_configuration"]:

            led["led_count"] = led_count
            gpio_len = self.hardware.get("gpio_len")

            if led["led_channel_configuration"] == "MIRROR":
                led["led_count"] = gpio_len

            elif led["led_channel_configuration"] == "LEDONLY":
                self.hardware.set_value("gpio_len", led_count)
                self.hardware.set_value("physical_gpio_len", 0)

                if led_count > gpio_len:
                    gpio_pins = self.hardware.get("gpio_pins")
                    gpio_pins.extend([_ + 1000 for _ in range(led_count - gpio_len)])
                    self.hardware.set_value("gpio_pins", gpio_pins)

                    pin_modes = self.hardware.get("pin_modes")
                    pin_modes.extend(["pwm" for _ in range(led_count - gpio_len)])
                    self.hardware.set_value("pin_modes", pin_modes)

                    is_pin_pwm = self.hardware.get("is_pin_pwm")
                    is_pin_pwm.extend([True for _ in range(led_count - gpio_len)])
                    self.hardware.set_value("is_pin_pwm", is_pin_pwm)

            elif led["led_channel_configuration"] == "EXTEND":
                self.hardware.set_value("gpio_len", gpio_len + led_count)
                self.hardware.set_value("physical_gpio_len", gpio_len)

                gpio_pins = self.hardware.get("gpio_pins")
                gpio_pins.extend([_ + 1000 for _ in range(led_count)])
                self.hardware.set_value("gpio_pins", gpio_pins)

                pin_modes = self.hardware.get("pin_modes")
                pin_modes.extend(["pwm" for _ in range(led_count)])
                self.hardware.set_value("pin_modes", pin_modes)

                is_pin_pwm = self.hardware.get("is_pin_pwm")
                is_pin_pwm.extend([True for _ in range(led_count)])
                self.hardware.set_value("is_pin_pwm", is_pin_pwm)
        else:
            led["led_count"] = 0

        led["max_brightness"] = self.led_config.getint('led', 'max_brightness')
        led["per_channel"] = self.led_config.getint('led', 'per_channel')

        led["pattern_color_map"] = self.led_config.get('led', 'pattern_color_map').upper()
        led["pattern_color"] = list(map(int, self.led_config.get('led', 'pattern_color').split(",")))
        led["pattern_type"] = self.led_config.get('led', 'pattern_type').upper()

        device_id = self.led_config.getint('led', 'device_id')
        if 0 <= device_id <= 255:
            led["device_id"] = device_id
        else:
            led["device_id"] = None

        led["device_address"] = self.led_config.get('led', 'device_address')
        led["hardware_id"] = self.led_config.get('led', 'hardware_id')
        if led["hardware_id"] == "":
            led["hardware_id"] = "1D50:60AB"
        led["baud_rate"] = self.led_config.getint('led', 'baud_rate')
        led["update_throttle"] = self.led_config.getint('led', 'update_throttle')

        led["matrix_width"] = self.led_config.getint('led', 'matrix_width')
        led["matrix_height"] = self.led_config.getint('led', 'matrix_height')
        led["matrix_pattern_type"] = self.led_config.get('led', 'matrix_pattern_type').upper().split(",")

        file_name = self.led_config.get('led', 'image_path').replace('$SYNCHRONIZED_LIGHTS_HOME',
                                                                 self.home_dir)
        if os.path.isfile(file_name):
            led["image_path"] = file_name
        else:
            led["image_path"] = self.home_dir + "/config/resources/16x16star8chan.gif"

        led["multiprocess"] = False

        led["beats"] = self.led_config.getint('led', 'beats')

        led["banner_text"] = self.led_config.get('led', 'banner_text').replace('"','')

        self.led = Section(led)

    def set_network(self):
        """
        Retrieves the network configuration parsing it from the Config Parser as necessary.
        """
        ntwrk = dict()
        ntwrk["networking"] = self.config.get('network', 'networking')
        ntwrk["ip_clients"] = list(map(str, self.config.get('network', 'ip_clients').split(",")))
        ntwrk["port"] = self.config.getint('network', 'port')
        ntwrk["buffer"] = self.config.getint('network', 'buffer')
        ntwrk["get_overrides"] = self.config.getboolean('network', 'get_overrides')

        if len(self.config.get('network', 'channels')) == 0:
            channels = [_ for _ in range(self.gpio_len)]
        else:
            channels = list(map(int, self.config.get('network', 'channels').split(",")))

        ntwrk["channels"] = channels

        self.network = Section(ntwrk)

    def set_lightshow(self):
        """
        Retrieve the lightshow configuration loading and parsing it from a file as necessary.
        """
        lghtshw = dict()
        ls = 'lightshow'
        lghtshw["mode"] = self.config.get(ls, 'mode', fallback='playlist')
        lghtshw["use_fifo"] = self.config.getboolean(ls, 'use_fifo', fallback=False)
        lghtshw["fifo"] = "/tmp/audio"
        lghtshw["audio_in_card"] = self.config.get(ls, 'audio_in_card', fallback='default')
        lghtshw["audio_out_card"] = self.config.get(ls, 'audio_out_card', fallback='default')

        if lghtshw["use_fifo"]:
            lghtshw["audio_out_card"] = ""

        lghtshw["input_channels"] = self.config.getint(ls, 'input_channels', fallback=1)
        lghtshw["input_sample_rate"] = self.config.getint(ls, 'input_sample_rate', fallback=48000)

        lghtshw["songname_command"] = self.config.get(ls, 'songname_command', fallback='')

        command_string = self.config.get(ls, 'stream_command_string',
                                         fallback='sudo mpg123 --stdout http://193.34.51.115:80')
        lghtshw["stream_command_string"] = shlex.split(command_string)

        lghtshw["stream_song_delim"] = self.config.get(ls, 'stream_song_delim',
                                                       fallback='ICY-META: StreamTitle=')
        lghtshw["stream_song_exit_count"] = self.config.getint(ls, 'stream_song_exit_count', fallback=0)

        playlist_path = self.config.get(ls, 'playlist_path',
                                        fallback='$SYNCHRONIZED_LIGHTS_HOME/music/sample/.playlist')
        playlist_path = playlist_path.replace('$SYNCHRONIZED_LIGHTS_HOME', self.home_dir)
        if playlist_path:
            lghtshw["playlist_path"] = playlist_path
        else:
            lghtshw["playlist_path"] = "/home/pi/music/.playlist"

        lghtshw["randomize_playlist"] = self.config.getboolean(ls, 'randomize_playlist', fallback=False)

        on_c = "always_on_channels"
        lghtshw[on_c] = list(map(int, self.config.get(ls, on_c, fallback="-1").split(",")))
        off_c = "always_off_channels"
        lghtshw[off_c] = list(map(int, self.config.get(ls, off_c, fallback="-1").split(",")))
        ic = "invert_channels"
        lghtshw[ic] = list(map(int, self.config.get(ls, ic, fallback="-1").split(",")))

        # setup up preshow
        preshow = None
        preshow_configuration = self.config.get(ls, 'preshow_configuration', fallback='')
        preshow_script = self.config.get(ls, 'preshow_script', fallback='')

        if preshow_configuration and not preshow_script:
            try:
                preshow = json.loads(preshow_configuration)
            except (ValueError, TypeError) as error:
                msg = "Preshow_configuration not defined or not in JSON format."
                logging.error(msg + str(error))
        else:
            if preshow_script and os.path.isfile(preshow_script):
                preshow = preshow_script

        lghtshw['preshow'] = preshow

        # setup postshow
        postshow = None
        postshow_configuration = self.config.get(ls, 'postshow_configuration', fallback='')
        postshow_script = self.config.get(ls, 'postshow_script', fallback='')

        if postshow_configuration and not postshow_script:
            try:
                postshow = json.loads(postshow_configuration)
            except (ValueError, TypeError) as error:
                msg = "Postshow_configuration not defined or not in JSON format."
                logging.error(msg + str(error))
        else:
            if postshow_script and os.path.isfile(postshow_script):
                postshow = postshow_script

        lghtshw['postshow'] = postshow
        lghtshw["decay_factor"] = self.config.getfloat(ls, 'decay_factor', fallback=0.0)
        lghtshw["attenuate_pct"] = self.config.getfloat(ls, 'attenuate_pct', fallback=0.0)
        lghtshw["light_delay"] = self.config.getfloat(ls, 'light_delay', fallback=0.0)

        lghtshw["log_level"] = self.config.get(ls, 'log_level', fallback='INFO').upper()

        # Standard Deviation
        lghtshw["SD_low"] = self.config.getfloat(ls, 'SD_low', fallback=0.5)
        lghtshw["SD_high"] = self.config.getfloat(ls, 'SD_high', fallback=0.75)

        self.lightshow = Section(lghtshw)

    def set_audio_processing(self):
        """
        Retrieve the audio processing configuration loading and parsing it from a file as necessary.
        """
        audio_prcssng = dict()
        audio_prcssng["use_gpu"] = self.config.getboolean('audio_processing', 'use_gpu')
        audio_prcssng["chunk_size"] = self.config.getint('audio_processing', 'chunk_size')
        audio_prcssng["min_frequency"] = \
            self.config.getfloat('audio_processing', 'min_frequency')
        audio_prcssng["max_frequency"] = \
            self.config.getfloat('audio_processing', 'max_frequency')
        temp = self.config.get('audio_processing', 'custom_channel_mapping')
        audio_prcssng["custom_channel_mapping"] = \
            list(map(int, temp.split(','))) if temp else 0
        temp = self.config.get('audio_processing', 'custom_channel_frequencies')
        audio_prcssng["custom_channel_frequencies"] = \
            list(map(int, temp.split(','))) if temp else 0

        self.audio_processing = Section(audio_prcssng)

    def get_playlist(self, play_list=None):
        play_list = play_list or self.playlist_path

        # Check if playlist file exists
        if not os.path.exists(play_list):
            log.error(f"Playlist file not found: {play_list}")
            print(f"\n{'='*70}")
            print(f"ERROR: Playlist file not found!")
            print(f"{'='*70}")
            print(f"Looking for: {play_list}")
            print(f"\nPlease either:")
            print(f"  1. Create the playlist file with your songs")
            print(f"  2. Update config/overrides.cfg with a valid playlist_path")
            print(f"  3. Use --playlist option: bin/start_music_and_lights --playlist <file>")
            print(f"\nPlaylist format (tab-separated):")
            print(f"  Song Name<TAB>path/to/song.mp3")
            print(f"{'='*70}\n")
            sys.exit(1)

        try:
            with open(play_list, 'rt') as playlist_fp:
                if fcntl is not None:
                    _lock_file(playlist_fp, fcntl.LOCK_SH)
                playlist = csv.reader(playlist_fp, delimiter='\t')
                songs = list()

                for song in playlist:
                    if not song or (len(song) == 1 and not song[0].strip()):
                        continue
                    if len(song) < 2 or len(song) > 4:
                        log.error('Invalid playlist. Each line should be in the form: '
                                  '<song name><tab><path to song>')
                        log.warning('Removing invalid entry')
                        continue
                    elif len(song) > 2:
                        song[2] = set(song[2].split(',')) if song[2] else set()
                    elif len(song) == 2:
                        song.append(set())

                    songs.append(song)

                if fcntl is not None:
                    _lock_file(playlist_fp, fcntl.LOCK_UN)
        except Exception as e:
            log.error(f"Error reading playlist file: {e}")
            print(f"\nERROR: Failed to read playlist file: {play_list}")
            print(f"Error: {e}\n")
            sys.exit(1)

        self.playlist = songs

        return songs

    def set_playlist(self, songs):
        self.playlist = songs

    def write_playlist(self, songs, playlist=None):
        playlist = playlist or self.playlist_path

        with open(playlist, 'wt') as playlist_fp:
            if fcntl is not None:
                _lock_file(playlist_fp, fcntl.LOCK_EX)
            writer = csv.writer(playlist_fp, delimiter='\t')

            rows_to_write = []
            for song in songs:
                row = list(song)
                if len(row) > 2:
                    if len(row[2]) > 0:
                        row[2] = ",".join(row[2]) if isinstance(row[2], (set, list)) else str(row[2])
                    else:
                        del row[2]
                rows_to_write.append(row)

            writer.writerows(rows_to_write)
            if fcntl is not None:
                _lock_file(playlist_fp, fcntl.LOCK_UN)

        self.playlist = songs


class Section(object):
    def __init__(self, config):
        self.config = config
        self.set_values(self.config)

    def set_config(self, config):
        self.config = config
        self.set_values(self.config)

    def get_config(self):
        return self.config

    def set_value(self, key, value):
        setattr(self, key, value)

    def set_values(self, dict_of_items):
        """Create class instance variables from key, value pairs

        :param dict_of_items: a dict containing key, value pairs to set
        :type dict_of_items: dict
        """
        for key, value in dict_of_items.items():
            setattr(self, key, value)

    def get(self, item):
        """Get class instance variables from string

        :param item:
        :type item: str

        :return: object of item type
        :rtype: object
        """
        return getattr(self, item)


if __name__ == "__main__":
    # prints the current configuration

    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=None, help='Config File Override')
    args = parser.parse_args()

    cm = Configuration(param_config=args.config)

    if args.config:
        print("Configuration File:", args.config, "\n")
    elif os.path.isfile(CONFIG_DIR + '/overrides.cfg'):
        print("Configuration File: overrides.cfg\n")
    else:
        print("Configuration File: defaults.cfg\n")

    print("Home directory set:", HOME_DIR)
    print("Config directory set:", CONFIG_DIR)
    print("Logs directory set:", LOG_DIR)

    print("\nHardware Configuration")
    for h_key, h_value in cm.hardware.config.items():
        print(h_key, "=", h_value)

    print("\nLightshow Configuration")
    for l_key, l_value in cm.lightshow.config.items():
        print(l_key, "=", l_value)

    print("\nAudio Processing Configuration")
    for a_key, a_value in cm.audio_processing.config.items():
        print(a_key, "=", a_value)

    print("\nNetwork Configuration")
    for nkey, nvalue in cm.network.config.items():
        print(nkey, "=", nvalue)

    print("\nLED Configuration")
    for lc in cm.configs.led:
        cm.set_led(config_file=lc)
        for led_key, led_value in cm.led.config.items():
            print(led_key, "=", led_value)

    print("\nTerminal Configuration")
    for tkey, tvalue in cm.terminal.config.items():
        print(tkey, "=", tvalue)
