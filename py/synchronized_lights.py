#!/usr/bin/env python
#
# Licensed under the BSD license.  See full license in LICENSE file.
# http://www.lightshowpi.org/
#
# Author: Todd Giles (todd@lightshowpi.org)
# Author: Chris Usey (chris.usey@gmail.com)
# Author: Ryan Jennings
# Author: Paul Dunn (dunnsept@gmail.com)
# Author: Tom Enos (tomslick.ca@gmail.com)

"""Play any audio file and synchronize lights to the music

When executed, this script will play an audio file, as well as turn on
and off N channels of lights to the music (by default the first 8 GPIO
channels on the Raspberry Pi), based upon music it is playing. Many
types of audio files are supported (MP3, WAV, FLAC, OGG, etc.).
"""

import configparser
import argparse
import atexit
from collections import deque
import errno
import json
import logging as log
import os
import random
import subprocess
import signal
import stat
import sys
import time
import wave
import curses
import bright_curses
import mutagen
from queue import Queue, Empty
from threading import Thread
import csv

import alsaaudio as aa
import audioread
import numpy as np
from numpy import where, clip, round, nan_to_num

import Platform
import fft
from prepostshow import PrePostShow
import RunningStats


# ============================================================================
# Audio Decoder Compatibility Wrapper
# ============================================================================
class AudioFileWrapper:
    """Wraps audioread to match the old decoder.py API

    Supports many formats including MP3, WAV, FLAC, OGG, M4A/AAC via ffmpeg/GStreamer.
    Uses an in-place bytearray buffer to minimize memory allocations on Pi Zero 2 W.
    """

    def __init__(self, audio_file):
        """Initialize wrapper with audioread file object or path string"""
        if isinstance(audio_file, str):
            self.audio_file = audioread.audio_open(audio_file)
            self._owns_file = True
        else:
            self.audio_file = audio_file
            self._owns_file = True

        # Get audio properties
        self.samplerate = self.audio_file.samplerate
        self.channels = self.audio_file.channels
        self.duration = self.audio_file.duration

        # Create iterator for reading data and in-place bytearray buffer
        self._data_iterator = self.audio_file.read_data()
        self._buffer = bytearray()
        self._finished = False
        self._closed = False

    def close(self):
        """Close the underlying audio file if we opened it"""
        if not self._closed and self._owns_file and self.audio_file:
            self._closed = True
            try:
                self.audio_file.__exit__(None, None, None)
            except Exception:
                pass

    def getframerate(self):
        """Return sample rate"""
        return self.samplerate

    def getnchannels(self):
        """Return number of channels"""
        return self.channels

    def getnframes(self):
        """Return total number of frames"""
        return int(self.duration * self.samplerate)

    def readframes(self, n):
        """Read n frames and return as bytes (int16)

        Args:
            n: Number of frames to read

        Returns:
            bytes: Raw audio data as int16 bytes
        """
        bytes_needed = n * 2 * self.channels

        while len(self._buffer) < bytes_needed and not self._finished:
            try:
                chunk = next(self._data_iterator)
                self._buffer.extend(chunk)
            except StopIteration:
                self._finished = True
                break

        if len(self._buffer) >= bytes_needed:
            data = bytes(self._buffer[:bytes_needed])
            del self._buffer[:bytes_needed]
        else:
            data = bytes(self._buffer)
            self._buffer.clear()

        return data


class decoder:
    """Compatibility module to replace old decoder.py

    Supports MP3, WAV, FLAC, OGG, M4A/AAC and other formats via audioread
    """

    @staticmethod
    def open(filename, force_header=False):
        """Open an audio file and return a decoder-compatible wrapper"""
        audio_file = audioread.audio_open(filename)
        return AudioFileWrapper(audio_file)


# Make sure SYNCHRONIZED_LIGHTS_HOME environment variable is set
HOME_DIR = os.getenv("SYNCHRONIZED_LIGHTS_HOME")

if not HOME_DIR:
    print("Need to setup SYNCHRONIZED_LIGHTS_HOME environment variable, see readme")
    sys.exit()

LOG_DIR = HOME_DIR + '/logs'

# logging levels
levels = {'DEBUG': log.DEBUG,
          'INFO': log.INFO,
          'WARNING': log.WARNING,
          'ERROR': log.ERROR,
          'CRITICAL': log.CRITICAL}

# Arguments
parser = argparse.ArgumentParser()
parser.add_argument('--log', default=None,
                    help='Set the logging level. levels:INFO, DEBUG, WARNING, ERROR, CRITICAL')
parser.add_argument('--config', default=None, help='Config File Override')
parser.add_argument('--mode', default=None,
                    help='Lightshow mode override (playlist, ambient, audio-in, stream-in). Overrides config file.')
parser.add_argument('--loop', action='store_true',
                    help='Continuously play playlist songs in the same process without restarting')

file_group = parser.add_mutually_exclusive_group()
file_group.add_argument('--playlist', default=None,
                        help='Playlist to choose song from.')
file_group.add_argument('--file', help='path to the song to play (required if no '
                                       'playlist is designated)')

cache_group = parser.add_mutually_exclusive_group()
cache_group.add_argument('--readcache', type=bool, default=True,
                         help='read light timing from cache if available. Default: true')
cache_group.add_argument('--createcache', action="store_true",
                         help='create light timing cache without audio playback or lightshow.')

# Module-level imports
import hardware_controller

# These will be initialized in main when running as script
args = None
hc = None
cm = None


def _write_now_playing(home_dir, text):
    """Write now_playing.txt directly without spawning a shell subprocess."""
    try:
        log_path = os.path.join(home_dir, "logs", "now_playing.txt")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(text + "\n" if text else "")
    except Exception as e:
        log.debug("Failed to write now_playing.txt: %s", e)


class Lightshow(object):
    def __init__(self):
        self.stream = None
        self.streaming = None
        self.sample_rate = None
        self.num_channels = None
        self.music_file = None
        self.fft_calc = None
        self.light_delay = None
        self.cache_found = None
        self.cache_matrix = None
        self.cache_filename = None
        self.config_filename = None
        self.song_filename = None
        self.terminal = None
        self.sequence_filename = None
        self.sequence_type = 'auto'
        self.sequence_mode_pref = 'auto'
        self.sequence_timing_ms = float(cm.custom_sequences.timing)
        self.sequence_brightness_range = float(cm.custom_sequences.brightness_range)
        self.sequence_matrix = None

        self.output = lambda raw_data: None

        self.mean = np.full(cm.hardware.gpio_len, 12.0, dtype='float32')
        self.std = np.full(cm.hardware.gpio_len, 1.5, dtype='float32')

        self.attenuate_pct = cm.lightshow.attenuate_pct
        self.sd_low = cm.lightshow.SD_low
        self.sd_high = cm.lightshow.SD_high

        self.decay_factor = cm.lightshow.decay_factor
        self.decay = np.zeros(cm.hardware.gpio_len, dtype='float32')
        self.physical_gpio_len = cm.hardware.physical_gpio_len
        self.network = hc.network
        self.server = self.network.networking == "server" or self.network.networking == "serverjson"
        self.client = self.network.networking == "client"

        if cm.lightshow.use_fifo:
            if os.path.exists(cm.lightshow.fifo):
                os.remove(cm.lightshow.fifo)
            os.mkfifo(cm.lightshow.fifo, 0o777)

        self.chunk_size = cm.audio_processing.chunk_size  # Use a multiple of 8

        atexit.register(self.exit_function)

        # Remove traceback on Ctrl-C
        signal.signal(signal.SIGINT, lambda x, y: sys.exit(0))
        signal.signal(signal.SIGTERM, lambda x, y: sys.exit(0))

        if cm.terminal.enabled:
            self.terminal = bright_curses.BrightCurses(cm.terminal)
            curses.wrapper(self.launch_curses)

        # Reset the state to indicate whether we're playing
        cm.update_state('now_playing', "0")

    def exit_function(self):
        """atexit function"""
        if self.music_file:
            try:
                self.music_file.close()
            except Exception:
                pass

        if self.server:
            self.network.set_playing()
            hc.set_array([0. for _ in range(cm.hardware.gpio_len)], False)
            time.sleep(1)
            self.network.unset_playing()

        hc.clean_up()

        # Reset the state to indicate whether we're playing
        cm.update_state('now_playing', "0")

        if cm.lightshow.mode == 'stream-in' and self.streaming:
            try:
                self.streaming.stdin.write(b"q")
            except IOError:
                pass
            try:
                os.kill(self.streaming.pid, signal.SIGINT)
            except OSError:
                pass
            if cm.lightshow.use_fifo and os.path.exists(cm.lightshow.fifo):
                os.unlink(cm.lightshow.fifo)

        _write_now_playing(cm.home_dir, "")

    def update_lights(self, matrix):
        """Update the state of all the lights

        Update the state of all the lights based upon the current
        frequency response matrix

        :param matrix: row of data from cache matrix
        :type matrix: numpy.ndarray | list
        """
        matrix = np.asarray(matrix, dtype=np.float32)

        if self.sequence_type == 'auto':
            denom = self.std * (self.sd_low + self.sd_high)
            denom = np.where(denom > 0.0, denom, 1.0)
            brightness = ((matrix - self.mean + (self.std * self.sd_low)) / denom) * (
                1.0 - (self.attenuate_pct / 100.0)
            )
            brightness = nan_to_num(brightness, copy=False)
            clip(brightness, 0.0, 1.0, out=brightness)

            # calculate light decay rate if used
            if self.decay_factor > 0:
                np.maximum(self.decay, brightness, out=brightness)
                np.maximum(brightness - self.decay_factor, 0.0, out=self.decay)

        elif self.sequence_type == 'csv':
            b_range = self.sequence_brightness_range if self.sequence_brightness_range > 0 else 255.0
            brightness = matrix / b_range
            brightness = nan_to_num(brightness, copy=False)
            clip(brightness, 0.0, 1.0, out=brightness)

        if self.terminal:
            self.terminal.curses_render(brightness)
            return

        # in the instance a single channel is defined convert scalar back into array
        if not hasattr(brightness, "__len__"):
            brightness = np.array([brightness], dtype=np.float32)

        hc.set_array(brightness, True)

        if hc.led:
            if cm.led.led_channel_configuration == "EXTEND":
                leds = brightness[self.physical_gpio_len:]
            else:
                leds = brightness[:cm.hardware.gpio_len]

            for led_instance in hc.led:
                led_instance.write_all(leds)

    def set_audio_device(self):
        if cm.lightshow.audio_out_card != '':
            if cm.lightshow.mode == 'stream-in':
                self.num_channels = 2

            output_device = aa.PCM(aa.PCM_PLAYBACK, aa.PCM_NORMAL, cm.lightshow.audio_out_card,
                                   channels=self.num_channels,
                                   rate=self.sample_rate,
                                   format=aa.PCM_FORMAT_S16_LE,
                                   periodsize=self.chunk_size)

            self.output = lambda raw_data: output_device.write(raw_data)

    def set_audio_source(self):
        stream_reader = None
        outq = None

        if cm.lightshow.mode == 'audio-in':
            # Open the input stream from default input device
            self.streaming = aa.PCM(aa.PCM_CAPTURE, aa.PCM_NORMAL, cm.lightshow.audio_in_card,
                                    channels=self.num_channels,
                                    rate=self.sample_rate,
                                    format=aa.PCM_FORMAT_S16_LE,
                                    periodsize=self.chunk_size)

            stream_reader = lambda: self.streaming.read()[-1]

        elif cm.lightshow.mode == 'stream-in':

            outq = Queue()

            if cm.lightshow.use_fifo:
                self.streaming = subprocess.Popen(cm.lightshow.stream_command_string,
                                                  stdin=subprocess.PIPE,
                                                  stdout=subprocess.PIPE,
                                                  preexec_fn=os.setsid)
                io = os.open(cm.lightshow.fifo, os.O_RDONLY | os.O_NONBLOCK)
                stream_reader = lambda: os.read(io, self.chunk_size)
                outthr = Thread(target=self.enqueue_output, args=(self.streaming.stdout, outq))
            else:
                # Open the input stream from command string
                self.streaming = subprocess.Popen(cm.lightshow.stream_command_string,
                                                  stdin=subprocess.PIPE,
                                                  stdout=subprocess.PIPE,
                                                  stderr=subprocess.PIPE)
                stream_reader = lambda: self.streaming.stdout.read(self.chunk_size)
                outthr = Thread(target=self.enqueue_output, args=(self.streaming.stderr, outq))

            outthr.daemon = True
            outthr.start()

        return stream_reader, outq

    def audio_in(self):
        """Control the lightshow from audio coming in from a real time audio"""

        self.sample_rate = cm.lightshow.input_sample_rate
        self.num_channels = cm.lightshow.input_channels

        stream_reader, outq = self.set_audio_source()

        log.debug("Running in %s mode - will run until Ctrl+C is pressed" % cm.lightshow.mode)
        print("Running in %s mode, use Ctrl+C to stop" % cm.lightshow.mode)

        # setup light_delay.
        chunks_per_sec = ((16 * self.num_channels * self.sample_rate) / 8) / self.chunk_size
        light_delay = int(cm.lightshow.light_delay * chunks_per_sec)
        matrix_buffer = deque([], 1000)

        self.set_audio_device()

        count = 2
        running_stats = RunningStats.Stats(cm.hardware.gpio_len)
        running_stats.preload(self.mean, self.std, count)

        hc.initialize()
        fft_calc = fft.FFT(self.chunk_size,
                           self.sample_rate,
                           cm.hardware.gpio_len,
                           cm.audio_processing.min_frequency,
                           cm.audio_processing.max_frequency,
                           cm.audio_processing.custom_channel_mapping,
                           cm.audio_processing.custom_channel_frequencies,
                           1,
                           cm.audio_processing.use_gpu)

        if self.server:
            self.network.set_playing()

        # Reset the state to indicate whether we're playing
        cm.update_state('now_playing', "1")

        songcount = 0
        zero_matrix = np.zeros(cm.hardware.gpio_len, dtype="float32")

        # Listen on the audio input device until CTRL-C is pressed
        while True:

            if cm.lightshow.mode == 'stream-in':
                try:
                    streamout = outq.get_nowait().strip(b'\n\r').decode("utf-8")
                except Empty:
                    pass
                else:
                    print(streamout)
                    if cm.lightshow.stream_song_delim in streamout:
                        songcount += 1
                        streamout = streamout.replace('\033[2K', '')
                        streamout = streamout.replace(cm.lightshow.stream_song_delim, '')
                        streamout = streamout.replace('"', '')
                        _write_now_playing(cm.home_dir, 'Now Playing "' + streamout + '"')
                        if cm.lightshow.songname_command:
                            os.system(cm.lightshow.songname_command + ' "Now Playing ' + streamout + '"')

                    if cm.lightshow.stream_song_exit_count > 0 and songcount > cm.lightshow.stream_song_exit_count:
                        break

            try:
                data = stream_reader()

            except OSError as err:
                if err.errno == errno.EAGAIN or err.errno == errno.EWOULDBLOCK:
                    continue
            try:
                self.output(data)
            except aa.ALSAAudioError:
                continue

            if len(data) == self.chunk_size:
                audio_samples = np.frombuffer(data, dtype=np.int16)
                audio_max = int(np.max(np.abs(audio_samples))) if len(audio_samples) else 0
                if audio_max < 250:
                    matrix = zero_matrix
                else:
                    matrix = fft_calc.calculate_levels(data)
                    running_stats.push(matrix)
                    self.mean = running_stats.mean()
                    self.std = running_stats.std()

                matrix_buffer.appendleft(matrix)

                if len(matrix_buffer) > light_delay:
                    matrix = matrix_buffer[light_delay]
                    self.update_lights(matrix)

    def load_custom_config(self):
        """
        Load custom configuration settings for file config_filename
        """
        self.sequence_mode_pref = 'auto'
        self.sequence_timing_ms = float(cm.custom_sequences.timing)
        self.sequence_brightness_range = float(cm.custom_sequences.brightness_range)

        if os.path.isfile(self.config_filename):
            config = configparser.RawConfigParser(allow_no_value=True)
            with open(self.config_filename) as f:
                config.read_file(f)

                if config.has_section('custom_lightshow'):
                    lsc = "custom_lightshow"

                    if config.has_option(lsc, 'sequence_mode'):
                        mode_val = config.get(lsc, 'sequence_mode')
                        if mode_val:
                            self.sequence_mode_pref = mode_val.strip().lower()

                    if config.has_option(lsc, 'sequence_timing'):
                        try:
                            self.sequence_timing_ms = float(config.get(lsc, 'sequence_timing'))
                        except (ValueError, TypeError):
                            pass

                    if config.has_option(lsc, 'sequence_brightness_range'):
                        try:
                            self.sequence_brightness_range = float(config.get(lsc, 'sequence_brightness_range'))
                        except (ValueError, TypeError):
                            pass

                    always_on = "always_on_channels"
                    if config.has_option(lsc, always_on) and config.get(lsc, always_on):
                        cm.lightshow.always_on_channels = list(map(int, config.get(lsc, always_on).split(",")))

                    always_off = "always_off_channels"
                    if config.has_option(lsc, always_off) and config.get(lsc, always_off):
                        cm.lightshow.always_off_channels = list(map(int, config.get(lsc, always_off).split(",")))

                    inverted = "invert_channels"
                    if config.has_option(lsc, inverted) and config.get(lsc, inverted):
                        cm.lightshow.invert_channels = list(map(int, config.get(lsc, inverted).split(",")))

                    aon = []
                    aoff = []
                    invert = []

                    always_on = "network_always_on"
                    if config.has_option(lsc, always_on) and config.get(lsc, always_on):
                        aon = list(map(int, config.get(lsc, always_on).split(",")))

                    always_off = "network_always_off"
                    if config.has_option(lsc, always_off) and config.get(lsc, always_off):
                        aoff = list(map(int, config.get(lsc, always_off).split(",")))

                    inverted = "network_invert"
                    if config.has_option(lsc, inverted) and config.get(lsc, inverted):
                        invert = list(map(int, config.get(lsc, inverted).split(",")))

                    hc.set_custom_overrides(always_off=aoff, always_on=aon, inverted=invert)

                    attenuate = "attenuate_pct"
                    if config.has_option(lsc, attenuate) and config.get(lsc, attenuate):
                        self.attenuate_pct = float(config.get(lsc, attenuate))

                    # setup up custom preshow
                    has_preshow_configuration = config.has_option(lsc, 'preshow_configuration')
                    has_preshow_script = config.has_option(lsc, 'preshow_script')

                    if has_preshow_configuration or has_preshow_script:
                        preshow = None
                        try:
                            preshow_configuration = config.get(lsc, 'preshow_configuration')
                        except configparser.NoOptionError:
                            preshow_configuration = None
                        try:
                            preshow_script = config.get(lsc, 'preshow_script')
                        except configparser.NoOptionError:
                            preshow_script = None

                        if preshow_configuration and not preshow_script:
                            try:
                                preshow = json.loads(preshow_configuration)
                            except (ValueError, TypeError) as error:
                                msg = "Preshow_configuration not defined or not in JSON format."
                                log.error(msg + str(error))
                        else:
                            if preshow_script and os.path.isfile(preshow_script):
                                preshow = preshow_script

                        cm.lightshow.preshow = preshow

                    # setup postshow
                    has_postshow_configuration = config.has_option(lsc, 'postshow_configuration')
                    has_postshow_script = config.has_option(lsc, 'postshow_script')

                    if has_postshow_configuration or has_postshow_script:
                        postshow = None
                        postshow_configuration = config.get(lsc, 'postshow_configuration') if has_postshow_configuration else None
                        postshow_script = config.get(lsc, 'postshow_script') if has_postshow_script else None

                        if postshow_configuration and not postshow_script:
                            try:
                                postshow = json.loads(postshow_configuration)
                            except (ValueError, TypeError) as error:
                                msg = "Postshow_configuration not defined or not in JSON format."
                                log.error(msg + str(error))
                        else:
                            if postshow_script and os.path.isfile(postshow_script):
                                postshow = postshow_script

                        cm.lightshow.postshow = postshow

                if config.has_section('custom_audio_processing'):
                    if config.has_option('custom_audio_processing', 'min_frequency'):
                        cm.audio_processing.min_frequency = \
                            config.getfloat('custom_audio_processing', 'min_frequency')

                    if config.has_option('custom_audio_processing', 'max_frequency'):
                        cm.audio_processing.max_frequency = \
                            config.getfloat('custom_audio_processing', 'max_frequency')

                    if config.has_option('custom_audio_processing', 'custom_channel_mapping'):
                        temp = config.get('custom_audio_processing', 'custom_channel_mapping')
                        cm.audio_processing.custom_channel_mapping = \
                            list(map(int, temp.split(','))) if temp else 0

                    if config.has_option('custom_audio_processing', 'custom_channel_frequencies'):
                        temp = config.get('custom_audio_processing', 'custom_channel_frequencies')
                        cm.audio_processing.custom_channel_frequencies = \
                            list(map(int, temp.split(','))) if temp else 0

    def _load_sequence_matrix(self):
        """Pre-load custom CSV sequence into a float32 numpy array for zero-allocation playback."""
        if not self.sequence_filename or not os.path.isfile(self.sequence_filename):
            return False

        try:
            rows = []
            target_channels = cm.hardware.gpio_len
            with open(self.sequence_filename, 'r', encoding='utf-8') as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith('#'):
                        continue
                    vals = [float(x) for x in stripped.split(',') if x.strip() != '']
                    if not vals:
                        continue
                    if len(vals) < target_channels:
                        vals.extend([0.0] * (target_channels - len(vals)))
                    elif len(vals) > target_channels:
                        vals = vals[:target_channels]
                    rows.append(vals)

            if not rows:
                return False

            self.sequence_matrix = np.asarray(rows, dtype=np.float32)
            return True
        except Exception as e:
            log.error("Failed to load custom sequence %s: %s", self.sequence_filename, e)
            return False

    def setup_audio(self):
        """Setup audio file and ALSA output"""
        force_header = any(ax in self.song_filename for ax in [".mp4", ".m4a", ".m4b"])

        self.music_file = decoder.open(self.song_filename, force_header)

        self.sample_rate = self.music_file.getframerate()
        self.num_channels = self.music_file.getnchannels()

        # Reset chunk_size per song before adjusting for sequence mode
        self.chunk_size = cm.audio_processing.chunk_size

        if self.sequence_type == 'csv':
            # Calculate chunk size for custom sequence (clamped for smooth ALSA playback)
            seq_chunk = int(self.sample_rate * (self.sequence_timing_ms / 1000.0))
            self.chunk_size = max(512, min(4096, seq_chunk))
        else:
            self.fft_calc = fft.FFT(self.chunk_size,
                                    self.sample_rate,
                                    cm.hardware.gpio_len,
                                    cm.audio_processing.min_frequency,
                                    cm.audio_processing.max_frequency,
                                    cm.audio_processing.custom_channel_mapping,
                                    cm.audio_processing.custom_channel_frequencies,
                                    self.num_channels,
                                    cm.audio_processing.use_gpu)

        # setup output device
        self.set_audio_device()

        chunks_per_sec = ((16 * self.num_channels * self.sample_rate) / 8) / self.chunk_size
        self.light_delay = int(cm.lightshow.light_delay * chunks_per_sec)

        # Output a bit about what we're about to play to the logs
        num_frames = str(self.music_file.getnframes() / self.sample_rate)
        log.info("Playing: " + self.song_filename + " (" + num_frames + " sec) [mode=" + self.sequence_type + "]")
        print("Playing: " + self.song_filename, flush=True)

    def setup_cache(self):
        """Setup the cache_matrix, std and mean

        loading them from a file if it exists, otherwise create empty arrays to be filled
        """
        self.cache_matrix = np.empty(shape=[0, cm.hardware.gpio_len], dtype=np.float32)
        self.cache_found = False

        self.cache_found = self.fft_calc.compare_config(self.cache_filename)

        if args.readcache:
            try:
                if not self.cache_found or not os.path.isfile(self.cache_filename):
                    raise IOError()

                loaded = np.loadtxt(self.cache_filename, dtype=np.float32)
                if loaded.ndim == 1:
                    loaded = loaded.reshape(-1, cm.hardware.gpio_len)
                if len(loaded) < 3:
                    raise IOError()

                # Index 0 is std, index 1 is mean, index 2+ is cache_matrix (zero-copy slice)
                self.std = np.array(loaded[0], dtype=np.float32)
                self.mean = np.array(loaded[1], dtype=np.float32)
                self.cache_matrix = loaded[2:]

                log.debug("std: " + str(self.std) + ", mean: " + str(self.mean))
            except IOError:
                self.cache_found = False
                self.cache_matrix = np.empty(shape=[0, cm.hardware.gpio_len], dtype=np.float32)
                msg = "Cached sync data song_filename not found: '"
                log.warning(msg + self.cache_filename + "'.  One will be generated.")

    def save_cache(self):
        """
        Save matrix, std, and mean to cache_filename for use during future playback
        """
        if len(self.cache_matrix) == 0:
            return

        mean = np.empty(cm.hardware.gpio_len, dtype='float32')
        std = np.empty(cm.hardware.gpio_len, dtype='float32')

        for pin in range(0, cm.hardware.gpio_len):
            col = self.cache_matrix[:, pin]
            pos = col[col > 0]
            if len(pos) > 0:
                std[pin] = np.std(pos)
                mean[pin] = np.mean(pos)
            else:
                std[pin] = 1.5
                mean[pin] = 12.0

        # Combine std, mean, and cache_matrix in a single allocation
        full_cache = np.vstack([std, mean, self.cache_matrix])

        # Save the cache using compact float formatting (faster I/O and smaller file on SD card)
        np.savetxt(self.cache_filename, full_cache, fmt='%.6g')

        # Save fft config
        self.fft_calc.save_config()

        cm_len = str(len(full_cache))
        log.info("Cached sync data written to '." + self.cache_filename + "' [" + cm_len + " rows]")
        log.info("Cached config data written to '." + self.fft_calc.config_filename)

    def get_song(self):
        """
        Determine the next file to play
        """
        cm.load_state()
        play_now = int(cm.get_state('play_now', "0"))
        song_to_play = int(cm.get_state('song_to_play', "0"))
        self.song_filename = args.file
        self.sequence_type = 'auto'
        self.sequence_matrix = None

        if args.playlist is not None and args.file is None:
            most_votes = [None, None, []]
            songs = cm.get_playlist(args.playlist)
            for song in songs:
                if len(song[2]) > 0:
                    if len(song[2]) >= len(most_votes[2]):
                        most_votes = song

            if most_votes[0] is not None:
                log.info("Most Votes: " + str(most_votes))
                current_song = most_votes

                # Update playlist with latest votes
                for song in songs:
                    if current_song[0:3] == song[0:3] and len(song) == 3:
                        song.append("playing!")

                # Update playlist file
                cm.write_playlist(songs, args.playlist)

            else:
                # Get a "play now" requested song
                if 0 < play_now <= len(songs):
                    current_song = songs[play_now - 1]
                # Get random song
                elif cm.lightshow.randomize_playlist:
                    current_song = songs[random.randrange(0, len(songs))]
                # Play next song in the lineup
                else:
                    if not (song_to_play <= len(songs) - 1):
                        song_to_play = 0

                    current_song = songs[song_to_play]

                    if (song_to_play + 1) <= len(songs) - 1:
                        next_song = (song_to_play + 1)
                    else:
                        next_song = 0

                    cm.update_state('song_to_play', str(next_song))

            # Get filename to play and store the current song playing in state cfg
            self.song_filename = current_song[1]
            cm.update_state('current_song', str(songs.index(current_song)))

        self.song_filename = self.song_filename.replace("$SYNCHRONIZED_LIGHTS_HOME", cm.home_dir)

        filename = os.path.abspath(self.song_filename)

        self.sequence_filename = filename.rsplit('.', 1)[0] + '.csv'
        self.config_filename = \
            os.path.dirname(filename) + "/." + os.path.basename(self.song_filename) + ".cfg"
        self.cache_filename = \
            os.path.dirname(filename) + "/." + os.path.basename(self.song_filename) + ".sync"

        _write_now_playing(cm.home_dir, "")
        try:
            metadata = mutagen.File(self.song_filename, easy=True)
        except Exception:
            metadata = None

        if metadata is not None:
            if "title" in metadata and "artist" in metadata:
                now_playing = "Now Playing " + metadata["title"][0] + " by " + metadata["artist"][0]
            elif "title" in metadata:
                now_playing = "Now Playing " + metadata["title"][0]
            else:
                now_playing = "Now Playing " + os.path.basename(self.song_filename)
            if cm.lightshow.songname_command:
                os.system(cm.lightshow.songname_command + " \"" + now_playing + "\"")
        else:
            now_playing = "Now Playing " + os.path.basename(self.song_filename)
        _write_now_playing(cm.home_dir, now_playing)

    def play_song(self):
        """Play the next song from the play list (or --file argument)."""

        # get the next song to play
        self.get_song()

        # If we're server tell the client to reset Overrides
        hc.set_custom_overrides(always_off=[], always_on=[], inverted=[])

        # load custom configuration from file (including sequence_mode preference)
        self.load_custom_config()

        # Determine sequence_type ('auto' for FFT or 'csv' for custom sequence)
        if self.sequence_mode_pref == 'fft':
            self.sequence_type = 'auto'
        elif self.sequence_mode_pref == 'custom':
            if self._load_sequence_matrix():
                self.sequence_type = 'csv'
            else:
                log.warning("Custom sequence requested for %s but valid CSV not found; falling back to FFT",
                            self.song_filename)
                self.sequence_type = 'auto'
        else:
            # 'auto' default: use CSV if present, otherwise FFT
            if os.path.isfile(self.sequence_filename) and self._load_sequence_matrix():
                self.sequence_type = 'csv'
            else:
                self.sequence_type = 'auto'

        # Initialize Lights
        self.network.set_playing()
        hc.initialize()

        # Handle the pre/post show
        cm.load_state()
        play_now = int(cm.get_state('play_now', "0"))

        self.network.unset_playing()

        if not play_now:
            result = PrePostShow('preshow', hc).execute()

            if result == PrePostShow.play_now_interrupt:
                cm.load_state()
                play_now = int(cm.get_state('play_now', "0"))

        self.network.set_playing()

        # Ensure play_now is reset before beginning playback
        if play_now:
            cm.update_state('play_now', "0")
            play_now = 0

        # setup audio file and output device
        self.setup_audio()

        if self.sequence_type == 'auto':
            self.setup_cache()

        matrix_buffer = deque([], 1000)
        cache_rows = []
        zero_matrix = np.zeros(cm.hardware.gpio_len, dtype='float32')

        # Process audio song_filename
        row = 0
        frames_played = 0
        data = self.music_file.readframes(self.chunk_size)

        if args.createcache and self.sequence_type == 'auto':
            total_frames = max(1, self.music_file.getnframes() / 100)

            counter = 0
            percentage = 0

            while data != b'':
                matrix = self.fft_calc.calculate_levels(data)
                cache_rows.append(matrix)
                data = self.music_file.readframes(self.chunk_size)

                if counter > total_frames:
                    percentage += 1
                    counter = 0
                    sys.stdout.write("\rGenerating sync file for :%s %d%%" % (self.song_filename,
                                                                              min(99, percentage)))
                    sys.stdout.flush()

                counter += self.chunk_size

            sys.stdout.write("\rGenerating sync file for :%s %d%%\n" % (self.song_filename, 100))
            sys.stdout.flush()

            self.cache_matrix = np.asarray(cache_rows, dtype=np.float32) if cache_rows else np.empty((0, cm.hardware.gpio_len), dtype=np.float32)
            cache_rows = []
            data = b''
            self.cache_found = False
            play_now = False
            print("saving sync file")

        # Update state with currently playing song name
        cm.update_state('now_playing', os.path.basename(self.song_filename))

        bytes_per_frame = max(1, 2 * self.num_channels)

        try:
            while data != b'' and not play_now:
                # output data to sound device
                self.output(data)

                if self.sequence_type == 'auto':
                    matrix = None
                    if self.cache_found and args.readcache:
                        if row < len(self.cache_matrix):
                            matrix = self.cache_matrix[row]
                        else:
                            log.warning("Ran out of cached FFT values, will update the cache.")
                            self.cache_found = False

                    if matrix is None:
                        matrix = self.fft_calc.calculate_levels(data)
                        cache_rows.append(matrix)

                    matrix_buffer.appendleft(matrix)

                    if len(matrix_buffer) > self.light_delay:
                        matrix = matrix_buffer[self.light_delay]
                        self.update_lights(matrix)

                elif self.sequence_type == 'csv':
                    # Use exact audio frame position to prevent timing drift
                    elapsed_ms = (frames_played * 1000.0) / self.sample_rate
                    seq_idx = int(elapsed_ms / self.sequence_timing_ms) if self.sequence_timing_ms > 0 else row
                    if self.sequence_matrix is not None and 0 <= seq_idx < len(self.sequence_matrix):
                        matrix = self.sequence_matrix[seq_idx]
                    else:
                        matrix = zero_matrix

                    matrix_buffer.appendleft(matrix)
                    if len(matrix_buffer) > self.light_delay:
                        matrix = matrix_buffer[self.light_delay]
                        self.update_lights(matrix)

                frames_played += len(data) // bytes_per_frame
                data = self.music_file.readframes(self.chunk_size)
                row += 1

                # Poll state file every 8 chunks (~5 times/sec); load_state() only reads disk if mtime changed
                if (row & 7) == 0:
                    cm.load_state()
                    play_now = int(cm.get_state('play_now', "0"))
        finally:
            if self.music_file:
                self.music_file.close()
                self.music_file = None

        if not self.cache_found and not play_now and self.sequence_type == 'auto':
            if cache_rows:
                if len(self.cache_matrix) > 0:
                    self.cache_matrix = np.vstack([self.cache_matrix, np.asarray(cache_rows, dtype=np.float32)])
                else:
                    self.cache_matrix = np.asarray(cache_rows, dtype=np.float32)
            self.save_cache()

        # check for postshow
        self.network.unset_playing()

        # Reset the state to indicate whether we're playing
        cm.update_state('now_playing', "0")

        if not play_now:
            PrePostShow('postshow', hc).execute()

        # We're done, turn it all off and clean up things ;)
        hc.clean_up()

    def network_client(self):
        """Network client support"""
        log.info("Network client mode starting")
        print("Network client mode starting...")
        print("press CTRL<C> to end")

        hc.initialize()

        try:
            while True:
                hc.receive_and_process()

        except KeyboardInterrupt:
            log.info("CTRL<C> pressed, stopping")
            print("stopping")

            hc.clean_up()

    def ambient_mode(self):
        """Ambient mode - hold lights steady without playing music"""
        log.info("Ambient mode starting")
        print("=" * 70)
        print("AMBIENT MODE - Lights On (No Music)")
        print("=" * 70)
        print("Press CTRL+C to stop")
        print()

        # Initialize hardware
        hc.initialize()

        # Get ambient configuration
        try:
            channel_str = cm.config.get('ambient', 'channels', fallback='')
            if channel_str:
                channels = [int(ch.strip()) for ch in channel_str.split(',')]
            else:
                channels = list(range(cm.hardware.gpio_len))

            brightness = cm.config.getfloat('ambient', 'brightness', fallback=1.0)
            brightness = max(0.0, min(1.0, brightness))

            log.info(f"Ambient mode: channels={channels}, brightness={brightness}")
            print(f"Channels: {channels}")
            print(f"Brightness: {brightness*100:.0f}%")
            print()
        except Exception as e:
            log.error(f"Error reading ambient configuration: {e}")
            print(f"Error in configuration: {e}")
            print("Using defaults: all channels at 100%")
            channels = list(range(cm.hardware.gpio_len))
            brightness = 1.0

        brightness_array = [0.0] * cm.hardware.gpio_len
        for ch in channels:
            if 0 <= ch < cm.hardware.gpio_len:
                brightness_array[ch] = brightness
            else:
                log.warning(f"Channel {ch} out of range, skipping")

        log.info("Setting lights to ambient pattern...")
        hc.set_array(brightness_array)

        cm.update_state('now_playing', "Ambient Mode")

        print("Lights set - holding steady")
        print("Press CTRL+C to stop")

        try:
            while True:
                if self.server:
                    hc.network.broadcast(brightness_array)
                time.sleep(1)

        except KeyboardInterrupt:
            log.info("CTRL+C pressed, stopping ambient mode")
            print("\nStopping...")
        finally:
            cm.update_state('now_playing', "0")
            hc.clean_up()
            print("Ambient mode stopped")

    def launch_curses(self, screen):
        self.terminal.init(screen)

    def enqueue_output(self, out, queue):
        for line in iter(out.readline, b''):
            queue.put(line)
        out.close()


if __name__ == "__main__":
    temp_args = parser.parse_args()
    if temp_args.createcache:
        parser.set_defaults(readcache=False)

    args = parser.parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)
    log.basicConfig(filename=LOG_DIR + '/music_and_lights.play.dbg',
                    format='[%(asctime)s] %(levelname)s {%(pathname)s:%(lineno)d} - %(message)s',
                    level=log.INFO)

    # Initialize hardware controller
    hc = hardware_controller.Hardware(param_config=args.config)

    # Get copy of configuration manager
    cm = hc.cm

    # Set default playlist if not specified
    if not args.playlist:
        args.playlist = cm.lightshow.playlist_path

    # Set log level
    if args.log:
        level = levels.get(args.log.upper())
    elif cm.lightshow.log_level != "":
        level = levels.get(cm.lightshow.log_level.upper())
    else:
        level = levels.get('INFO')
    log.getLogger().setLevel(level)

    lightshow = Lightshow()

    # Determine mode - command line overrides config file
    mode = args.mode if args.mode else cm.lightshow.mode

    if mode == "ambient":
        log.info("Starting in AMBIENT mode (lights on, no music)")
        lightshow.ambient_mode()

    elif "-in" in mode:
        lightshow.audio_in()

    elif lightshow.client:
        lightshow.network_client()

    else:
        if args.file is None and args.playlist is None:
            print("One of --playlist or --file must be specified")
            sys.exit()
        if args.loop and args.playlist and not args.createcache:
            while True:
                lightshow.play_song()
        else:
            lightshow.play_song()
