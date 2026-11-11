#
# Licensed under the BSD license.  See full license in LICENSE file.
# http://www.lightshowpi.org/
#
# Author: Todd Giles (todd@lightshowpi.org)
# Author: Tom Enos (tomslick.ca@gmail.com)

"""FFT methods for computing / analyzing frequency response of audio.

Initial FFT code inspired from the code posted here:
http://www.raspberrypi.org/phpBB3/viewtopic.php?t=35838&p=454041

Optimizations from work by Scott Driscoll:
http://www.instructables.com/id/Raspberry-Pi-Spectrum-Analyzer-with-RGB-LED-Strip-/

Third party dependencies:

numpy: for array support - http://www.numpy.org/
"""

import configparser
import logging
import math
import os.path
import numpy as np


class FFT(object):
    def __init__(self,
                 chunk_size,
                 sample_rate,
                 num_bins,
                 min_frequency,
                 max_frequency,
                 custom_channel_mapping,
                 custom_channel_frequencies,
                 input_channels=2,
                 use_gpu=True):
        """
        :param chunk_size: chunk size of audio data
        :type chunk_size: int

        :param sample_rate: audio file sample rate
        :type sample_rate: int

        :param num_bins: length of gpio to process
        :type num_bins: int

        :param input_channels: number of audio input channels to process for (default=2)
        :type input_channels: int

        :param min_frequency: lowest frequency for which a channel will be activated
        :type min_frequency: float

        :param max_frequency: max frequency for which a channel will be activated.
        :type max_frequency: float

        :param custom_channel_mapping: custom map of channels to different frequencies
        :type custom_channel_mapping: list | int

        :param custom_channel_frequencies: custom list of frequencies that should be
                                        utilized for each channel
        :type custom_channel_frequencies: list | int

        """

        self.chunk_size = int(chunk_size)
        self.sample_rate = int(sample_rate)
        self.num_bins = int(num_bins)
        self.input_channels = int(input_channels)
        self.window = np.hanning(self.chunk_size).astype(np.float32) if self.chunk_size > 0 else np.empty(0, dtype=np.float32)
        self.min_frequency = min_frequency
        self.max_frequency = max_frequency
        self.custom_channel_mapping = custom_channel_mapping
        self.custom_channel_frequencies = custom_channel_frequencies
        self.frequency_limits = self.calculate_channel_frequency()
        self.config = configparser.RawConfigParser(allow_no_value=True)
        self.config_filename = ""

        # GPU acceleration has been removed - always use CPU-based numpy FFT
        if use_gpu:
            logging.warning("GPU acceleration (rpi_audio_levels) has been removed. "
                          "Using CPU-based numpy FFT instead. Modern Raspberry Pi "
                          "hardware is fast enough for real-time processing. "
                          "Set use_gpu=False in config to suppress this warning.")
        self.use_gpu = False  # Always use CPU

        fl = np.asarray(self.frequency_limits, dtype=np.float64)
        piff_arr = ((fl * self.chunk_size) / self.sample_rate).astype(int)

        for a in range(len(piff_arr)):
            if piff_arr[a][0] == piff_arr[a][1]:
                piff_arr[a][1] += 1
        self.piff = [(int(row[0]), int(row[1])) for row in piff_arr]

    def calculate_piff(self, val, chunk_size, sample_rate):
        return int(chunk_size * val / sample_rate)

    def calculate_levels(self, data):
        """Calculate frequency response for each channel defined in frequency_limits

        :param data: decoder.frames(), audio data for fft calculations
        :type data: bytes | bytearray | memoryview | numpy.ndarray

        :return: frequency response levels per channel
        :rtype: numpy.ndarray
        """
        # Create a numpy view without copying where possible
        if isinstance(data, np.ndarray):
            data_stereo = data.astype(np.int16, copy=False).ravel()
        else:
            data_stereo = np.frombuffer(data, dtype=np.int16)

        if self.input_channels == 2:
            # Pull out the even values (left channel) as a zero-copy strided view
            samples = data_stereo[::2]
        else:
            samples = data_stereo

        n_samples = len(samples)
        if n_samples == 0 or not np.any(samples):
            return np.zeros(self.num_bins, dtype=np.float32)

        # Reuse precomputed Hanning window unless chunk length differs
        if n_samples != len(self.window):
            self.window = np.hanning(n_samples).astype(np.float32)

        windowed = samples * self.window

        # Apply real FFT and drop Nyquist bin using a zero-copy slice instead of np.delete
        spectrum = np.fft.rfft(windowed)[:-1]
        if len(spectrum) == 0:
            return np.zeros(self.num_bins, dtype=np.float32)

        # Compute power spectrum directly without square-root overhead of abs()
        power = spectrum.real ** 2 + spectrum.imag ** 2

        cache_matrix = np.zeros(self.num_bins, dtype=np.float32)
        if n_samples == self.chunk_size:
            for i, (start, end) in enumerate(self.piff):
                psum = float(power[start:end].sum())
                if psum > 0.0:
                    cache_matrix[i] = math.log10(psum)
        else:
            scale = n_samples / self.sample_rate
            for i, (f_low, f_high) in enumerate(self.frequency_limits):
                start = int(f_low * scale)
                end = int(f_high * scale)
                if start == end:
                    end += 1
                psum = float(power[start:end].sum())
                if psum > 0.0:
                    cache_matrix[i] = math.log10(psum)

        return cache_matrix

    def calculate_channel_frequency(self):
        """Calculate frequency values

        Calculate frequency values for each channel,
        taking into account custom settings.

        :return: frequency values for each channel
        :rtype: list
        """

        # How many channels do we need to calculate the frequency for
        if self.custom_channel_mapping != 0 and len(self.custom_channel_mapping) == self.num_bins:
            logging.debug("Custom Channel Mapping is being used: %s",
                          str(self.custom_channel_mapping))
            channel_length = max(self.custom_channel_mapping)
        else:
            logging.debug("Normal Channel Mapping is being used.")
            channel_length = self.num_bins

        logging.debug("Calculating frequencies for %d channels.", channel_length)
        octaves = (math.log(self.max_frequency / self.min_frequency)) / math.log(2)
        logging.debug("octaves in selected frequency range ... %s", octaves)
        octaves_per_channel = octaves / channel_length
        frequency_limits = []
        frequency_store = []

        frequency_limits.append(self.min_frequency)

        if self.custom_channel_frequencies != 0 and (
                len(self.custom_channel_frequencies) >= channel_length + 1):
            logging.debug("Custom channel frequencies are being used")
            frequency_limits = self.custom_channel_frequencies
        else:
            logging.debug("Custom channel frequencies are not being used")
            multiplier = 10 ** (3 / (10 * (1 / octaves_per_channel)))
            for _ in range(1, self.num_bins + 1):
                frequency_limits.append(frequency_limits[-1] * multiplier)

        for pin in range(0, channel_length):
            frequency_store.append((frequency_limits[pin], frequency_limits[pin + 1]))
            logging.debug("channel %d is %6.2f to %6.2f ", pin, frequency_limits[pin],
                          frequency_limits[pin + 1])

        # we have the frequencies now lets map them if custom mapping is defined
        if self.custom_channel_mapping != 0 and len(self.custom_channel_mapping) == self.num_bins:
            frequency_map = []

            for pin in range(0, self.num_bins):
                mapped_channel = self.custom_channel_mapping[pin] - 1
                mapped_frequency_set = frequency_store[mapped_channel]
                logging.debug("mapped channel: %s will hold LOW: %s HIGH: %s",
                              mapped_channel, mapped_frequency_set[0], mapped_frequency_set[1])
                frequency_map.append(mapped_frequency_set)

            return frequency_map
        else:
            return frequency_store

    def compare_config(self, cache_filename):
        """
        Compare the current configuration used to generate fft to a saved
        copy of the data that was used to generate the fft data for an
        existing cache file
        
        :param cache_filename: path and name of cache file
        :type cache_filename: str
        """
        self.config_filename = cache_filename.replace(".sync", ".cfg")

        if not os.path.isfile(self.config_filename):
            logging.warning("No cached config data found")
            return False
        else:
            has_config = True
            with open(self.config_filename) as f:
                self.config.read_file(f)

        fft_cache = dict()
        fft_current = dict()

        try:
            fft_cache["chunk_size"] = self.config.getint("fft", "chunk_size")
            fft_cache["sample_rate"] = self.config.getint("fft", "sample_rate")
            fft_cache["num_bins"] = self.config.getint("fft", "num_bins")
            fft_cache["min_frequency"] = self.config.getfloat("fft", "min_frequency")
            fft_cache["max_frequency"] = self.config.getfloat("fft", "max_frequency")

            temp = [int(channel) for channel in
                    self.config.get("fft", "custom_channel_mapping").split(',')]
            if len(temp) == 1:
                fft_cache["custom_channel_mapping"] = temp[0]
            else:
                fft_cache["custom_channel_mapping"] = temp

            temp = [int(channel) for channel in
                    self.config.get("fft", "custom_channel_frequencies").split(',')]
            if len(temp) == 1:
                fft_cache["custom_channel_frequencies"] = temp[0]
            else:
                fft_cache["custom_channel_frequencies"] = temp

            fft_cache["input_channels"] = self.config.getint("fft", "input_channels")
        except configparser.Error:
            has_config = False

        fft_current["chunk_size"] = self.chunk_size
        fft_current["sample_rate"] = self.sample_rate
        fft_current["num_bins"] = self.num_bins
        fft_current["min_frequency"] = self.min_frequency
        fft_current["max_frequency"] = self.max_frequency
        fft_current["custom_channel_mapping"] = self.custom_channel_mapping
        fft_current["custom_channel_frequencies"] = self.custom_channel_frequencies
        fft_current["input_channels"] = self.input_channels

        if fft_cache != fft_current:
            has_config = False
            logging.warning("Cached config data does not match")

        return has_config

    def save_config(self):
        """Save the current configuration used to generate the fft data"""
        if self.config.has_section("fft"):
            self.config.remove_section("fft")

        self.config.add_section("fft")
        self.config.set('fft', '# DO NOT EDIT THIS SECTION')
        self.config.set('fft', '# EDITING THIS SECTION WILL CAUSE YOUR SYNC FILE TO BE INVALID')

        self.config.set('fft', 'chunk_size', str(self.chunk_size))
        self.config.set('fft', 'sample_rate', str(self.sample_rate))
        self.config.set('fft', 'num_bins', str(self.num_bins))
        self.config.set('fft', 'min_frequency', str(self.min_frequency))
        self.config.set('fft', 'max_frequency', str(self.max_frequency))

        if isinstance(self.custom_channel_mapping, list):
            self.config.set('fft', 'custom_channel_mapping', str(self.custom_channel_mapping)[1:-1])
        else:
            self.config.set('fft', 'custom_channel_mapping', str(self.custom_channel_mapping))

        if isinstance(self.custom_channel_frequencies, list):
            self.config.set('fft', 'custom_channel_frequencies',
                            str(self.custom_channel_frequencies)[1:-1])
        else:
            self.config.set('fft', 'custom_channel_frequencies',
                            str(self.custom_channel_frequencies))

        self.config.set('fft', 'input_channels', str(self.input_channels))

        with open(self.config_filename, "w") as f:
            self.config.write(f)
