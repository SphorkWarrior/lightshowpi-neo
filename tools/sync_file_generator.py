# sync file generator for lightshowpi
# run usage
#
# python sync_file_generator.py
#
# Enter y to confirm that you wish to run this
# Enter the path to the folder containing your audio files
# along with the sync files it will also generate a playlist file
# enter the path to this playlist file in your overrides.cfg and
# lightshowpi will use this as your new playlist

import soundfile as sf
import glob
import mutagen
import numpy as np
import os
import sys
import wave


# ============================================================================
# Audio Decoder Compatibility Wrapper
# ============================================================================
class AudioFileWrapper:
    """Wraps soundfile.SoundFile to match the old decoder.py API"""

    def __init__(self, sf_file):
        self.sf_file = sf_file

    def getframerate(self):
        return self.sf_file.samplerate

    def getnchannels(self):
        return self.sf_file.channels

    def getnframes(self):
        return len(self.sf_file)

    def readframes(self, n):
        data = self.sf_file.read(n, dtype='int16', always_2d=False)
        return data.tobytes()


class decoder:
    """Compatibility module to replace old decoder.py"""

    @staticmethod
    def open(filename, force_header=False):
        sf_file = sf.SoundFile(filename)
        return AudioFileWrapper(sf_file)


HOME_DIR = os.getenv("SYNCHRONIZED_LIGHTS_HOME")
if not HOME_DIR:
    print("Need to setup SYNCHRONIZED_LIGHTS_HOME environment variable, "
          "see readme")
    sys.exit()

# hack to get the configuration_manager and fft modules to load
# from a different directory
path = list(sys.path)

# insert script location and configuration_manager location into path
sys.path.insert(0, HOME_DIR + "/py")

# import the configuration_manager and fft now that we can
import fft

import hardware_controller

hc = hardware_controller.Hardware()

# get copy of configuration manager
cm = hc.cm

GPIOLEN = cm.hardware.gpio_len
_MIN_FREQUENCY = cm.audio_processing.min_frequency
_MAX_FREQUENCY = cm.audio_processing.max_frequency

try:
    _CUSTOM_CHANNEL_MAPPING = cm.audio_processing.custom_channel_mapping
except Exception:
    _CUSTOM_CHANNEL_MAPPING = 0

try:
    _CUSTOM_CHANNEL_FREQUENCIES = cm.audio_processing.custom_channel_frequencies
except Exception:
    _CUSTOM_CHANNEL_FREQUENCIES = 0

CHUNK_SIZE = cm.audio_processing.chunk_size


def calculate_channel_frequency(min_frequency,
                                max_frequency,
                                custom_channel_mapping,
                                custom_channel_frequencies):
    """Calculate frequency values for each channel, taking into account custom settings."""
    if custom_channel_mapping != 0 and len(custom_channel_mapping) == GPIOLEN:
        channel_length = max(custom_channel_mapping)
    else:
        channel_length = GPIOLEN

    octaves = (np.log(max_frequency / min_frequency)) / np.log(2)
    octaves_per_channel = octaves / channel_length
    frequency_limits = [min_frequency]
    frequency_store = []

    if custom_channel_frequencies != 0 and (len(custom_channel_frequencies)
                                            >= channel_length + 1):
        frequency_limits = custom_channel_frequencies
    else:
        for _ in range(1, GPIOLEN + 1):
            frequency_limits.append(frequency_limits[-1]
                                    * 10 ** (3 /
                                             (10 * (1 / octaves_per_channel))))
    for i in range(0, channel_length):
        frequency_store.append((frequency_limits[i], frequency_limits[i + 1]))

    if custom_channel_mapping != 0 and len(custom_channel_mapping) == GPIOLEN:
        frequency_map = []
        for i in range(0, GPIOLEN):
            mapped_channel = custom_channel_mapping[i] - 1
            mapped_frequency_set = frequency_store[mapped_channel]
            frequency_map.append(mapped_frequency_set)
        return frequency_map
    else:
        return frequency_store


def cache_song(song_filename):
    """Generate FFT sync file for song_filename."""
    if song_filename.endswith('.wav'):
        musicfile = wave.open(song_filename, 'r')
    else:
        musicfile = decoder.open(song_filename)

    sample_rate = musicfile.getframerate()
    num_channels = musicfile.getnchannels()

    fft_calc = fft.FFT(CHUNK_SIZE,
                       sample_rate,
                       GPIOLEN,
                       _MIN_FREQUENCY,
                       _MAX_FREQUENCY,
                       _CUSTOM_CHANNEL_MAPPING,
                       _CUSTOM_CHANNEL_FREQUENCIES,
                       num_channels,
                       False)

    song_filename = os.path.abspath(song_filename)
    cache_filename = \
        os.path.dirname(song_filename) + "/." + os.path.basename(song_filename) + ".sync"

    mean = [12.0 for _ in range(GPIOLEN)]
    std = [1.5 for _ in range(GPIOLEN)]

    # Process audio song_filename using O(N) list accumulation
    cache_rows = []
    data = musicfile.readframes(CHUNK_SIZE)

    while data != b'':
        matrix = fft_calc.calculate_levels(data)
        cache_rows.append(matrix)
        data = musicfile.readframes(CHUNK_SIZE)

    cache_matrix = np.asarray(cache_rows, dtype=np.float32) if cache_rows else np.empty((0, GPIOLEN), dtype=np.float32)

    if len(cache_matrix) > 0:
        for i in range(0, GPIOLEN):
            col = cache_matrix[:, i]
            pos = col[col > 0]
            if len(pos) > 0:
                std[i] = float(np.std(pos))
                mean[i] = float(np.mean(pos))

    full_cache = np.vstack([std, mean, cache_matrix]) if len(cache_matrix) > 0 else np.vstack([std, mean])
    np.savetxt(cache_filename, full_cache, fmt='%.6g')
    fft_calc.compare_config(cache_filename)
    fft_calc.save_config()


def main():
    print("Do you want to generate sync files?")
    print()
    print("This could take a while if you have a lot of songs")

    question = input("Would you like to proceed? (Y to continue) :")

    if question not in ["y", "Y"]:
        sys.exit(0)

    location = input("Enter the path to the folder of songs:")
    location += "/"

    sync_list = list()
    audio_file_types = ["*.mp3", "*.mp4",
                        "*.m4a", "*.m4b",
                        "*.aac", "*.ogg",
                        "*.flac", "*.oga",
                        "*.wma", "*.wav"]

    for file_type in audio_file_types:
        sync_list.extend(glob.glob(location + file_type))

    playlistFile = open(location + "playlist", "w")

    for song in sync_list:
        print("Generating sync file for", song)
        cache_song(song)
        print("cached")

        metadata = mutagen.File(song, easy=True)
        if metadata and "title" in metadata:
            title = metadata["title"][0]
        else:
            title = os.path.splitext(os.path.basename(song))[0].strip()
            title = title.replace("_", " ")
            title = title.replace("-", " - ")
        playlistFile.write(title + "\t" + song + "\n")

    playlistFile.close()

    print("All Finished.")
    print("A playlist was also generated")
    print(location + "playlist")
    sys.path[:] = path


if __name__ == "__main__":
    main()
