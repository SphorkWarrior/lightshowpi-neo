"""Tests for Song Upload, Playlist Management, Custom Light Sequences, and Sequence Mode Toggle."""

import asyncio
import configparser
import io
import math
import os
import struct
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import UploadFile

from api.core.database import init_database
from api.models.schemas import (
    PlaylistCreate,
    PlaylistReorder,
    PlaylistSongAdd,
    SequenceData,
    SequenceGenerateRequest,
    SequenceMode,
    SongUpdate,
)
from api.routers import playlists as playlists_router
from api.routers import songs as songs_router
from api.services.media_service import MediaService, set_media_service


def _make_wav_bytes(duration_sec: float = 0.5, sample_rate: int = 22050, freq: float = 440.0) -> bytes:
    """Create a valid 16-bit stereo WAV file in memory."""
    buf = io.BytesIO()
    num_frames = int(duration_sec * sample_rate)
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        samples = []
        for i in range(num_frames):
            val = int(16000 * math.sin(2 * math.pi * freq * (i / sample_rate)))
            samples.extend([val, val])
        wf.writeframes(struct.pack(f'<{len(samples)}h', *samples))
    return buf.getvalue()


@pytest.fixture
def temp_env(tmp_path, monkeypatch):
    """Set up isolated temporary LightShowPi directory and SQLite DB."""
    monkeypatch.setenv("SYNCHRONIZED_LIGHTS_HOME", str(tmp_path))
    (tmp_path / "music" / "sample").mkdir(parents=True)
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "data").mkdir(parents=True)

    db_path = str(tmp_path / "data" / "test_web.db")
    db = init_database(db_path)

    mock_config = SimpleNamespace(
        hardware=SimpleNamespace(gpio_len=8),
        cm=SimpleNamespace(
            custom_sequences=SimpleNamespace(timing=50, brightness_range=255),
            lightshow=SimpleNamespace(playlist_path=str(tmp_path / "music" / "sample" / ".playlist")),
        ),
        lightshow=SimpleNamespace(
            playlist_path=str(tmp_path / "music" / "sample" / ".playlist"),
            SD_low=0.5,
            SD_high=0.75,
        ),
        audio_processing=SimpleNamespace(
            min_frequency=20,
            max_frequency=15000,
            custom_channel_mapping=0,
            custom_channel_frequencies=0,
        ),
    )

    media_service = MediaService(db, mock_config)
    set_media_service(media_service)

    return {
        "root": tmp_path,
        "db": db,
        "service": media_service,
        "user": {"username": "admin"},
    }


class TestSongUploadAndSequenceToggle:
    """Test song upload, sequence mode toggle (FFT vs Custom), and custom sequence editor APIs."""

    def test_upload_song_and_toggle_sequence_mode(self, temp_env):
        service = temp_env["service"]
        root = temp_env["root"]
        user = temp_env["user"]

        wav_bytes = _make_wav_bytes(duration_sec=0.6)
        upload = UploadFile(filename="holiday_jam.wav", file=io.BytesIO(wav_bytes))

        song = asyncio.run(
            songs_router.upload_song(
                file=upload,
                title="Holiday Jam",
                artist="Pi Zero Band",
                playlist_id=None,
                service=service,
                current_user=user,
            )
        )
        assert song["title"] == "Holiday Jam"
        assert song["artist"] == "Pi Zero Band"
        assert song["sequence_mode"] == "fft"
        assert song["has_custom_sequence"] is False

        song_id = song["id"]

        # Toggle to custom sequence mode
        updated = asyncio.run(
            songs_router.update_song(
                song_id=song_id,
                payload=SongUpdate(sequence_mode=SequenceMode.CUSTOM, sequence_timing=50),
                service=service,
                current_user=user,
            )
        )
        assert updated["sequence_mode"] == "custom"

        # Verify per-song .cfg file was written for synchronized_lights.py
        cfg_files = list(root.rglob(".holiday_jam.wav.cfg"))
        assert len(cfg_files) == 1
        parser = configparser.RawConfigParser()
        parser.read(str(cfg_files[0]))
        assert parser.get("custom_lightshow", "sequence_mode") == "custom"

        # Save a custom 8-channel light sequence
        steps = [
            [255, 0, 128, 0, 255, 0, 128, 0],
            [0, 255, 0, 128, 0, 255, 0, 128],
            [255, 255, 255, 255, 0, 0, 0, 0],
        ]
        saved_seq = asyncio.run(
            songs_router.save_song_sequence(
                song_id=song_id,
                payload=SequenceData(
                    timing_ms=50,
                    num_channels=8,
                    sequence_mode=SequenceMode.CUSTOM,
                    steps=steps,
                ),
                service=service,
                current_user=user,
            )
        )
        assert saved_seq["steps"] == steps
        assert saved_seq["sequence_mode"] == "custom"
        assert saved_seq["has_custom_sequence"] is True

        # Toggle back to FFT mode and verify custom sequence file still exists
        switched_fft = asyncio.run(
            songs_router.update_song(
                song_id=song_id,
                payload=SongUpdate(sequence_mode=SequenceMode.FFT),
                service=service,
                current_user=user,
            )
        )
        assert switched_fft["sequence_mode"] == "fft"
        assert switched_fft["has_custom_sequence"] is True

    def test_generate_sequence_from_fft_and_waveform(self, temp_env):
        service = temp_env["service"]
        user = temp_env["user"]

        wav_bytes = _make_wav_bytes(duration_sec=0.5, freq=880.0)
        upload = UploadFile(filename="synth_test.wav", file=io.BytesIO(wav_bytes))
        song = asyncio.run(
            songs_router.upload_song(
                file=upload,
                title=None,
                artist=None,
                playlist_id=None,
                service=service,
                current_user=user,
            )
        )
        song_id = song["id"]

        # Test waveform endpoint
        wave_data = asyncio.run(
            songs_router.get_song_waveform(
                song_id=song_id,
                points=50,
                service=service,
                current_user=user,
            )
        )
        peaks = wave_data["peaks"]
        assert len(peaks) == 50
        assert max(peaks) > 0.1

        # Test FFT sequence auto-generator
        seq_data = asyncio.run(
            songs_router.generate_sequence_from_fft(
                song_id=song_id,
                payload=SequenceGenerateRequest(timing_ms=50, activate_custom=True),
                service=service,
                current_user=user,
            )
        )
        assert seq_data["num_channels"] == 8
        assert len(seq_data["steps"]) >= 5
        assert seq_data["sequence_mode"] == "custom"


class TestPlaylistManagement:
    """Test playlist creation, song ordering, activation, and deletion."""

    def test_playlist_lifecycle(self, temp_env):
        service = temp_env["service"]
        user = temp_env["user"]

        # Upload two songs
        s1 = asyncio.run(
            songs_router.upload_song(
                file=UploadFile(filename="track1.wav", file=io.BytesIO(_make_wav_bytes(0.3))),
                title="Track One",
                artist=None,
                playlist_id=None,
                service=service,
                current_user=user,
            )
        )
        s2 = asyncio.run(
            songs_router.upload_song(
                file=UploadFile(filename="track2.wav", file=io.BytesIO(_make_wav_bytes(0.3))),
                title="Track Two",
                artist=None,
                playlist_id=None,
                service=service,
                current_user=user,
            )
        )

        # Create new playlist
        pl = asyncio.run(
            playlists_router.create_playlist(
                payload=PlaylistCreate(name="Winter Show"),
                service=service,
                current_user=user,
            )
        )
        pl_id = pl["id"]

        # Add both songs
        asyncio.run(
            playlists_router.add_song_to_playlist(
                playlist_id=pl_id,
                payload=PlaylistSongAdd(song_id=s1["id"]),
                service=service,
                current_user=user,
            )
        )
        add2 = asyncio.run(
            playlists_router.add_song_to_playlist(
                playlist_id=pl_id,
                payload=PlaylistSongAdd(song_id=s2["id"]),
                service=service,
                current_user=user,
            )
        )
        assert add2["song_count"] == 2
        assert [s["id"] for s in add2["songs"]] == [s1["id"], s2["id"]]

        # Reorder songs (reverse order)
        reord = asyncio.run(
            playlists_router.reorder_playlist_songs(
                playlist_id=pl_id,
                payload=PlaylistReorder(song_ids=[s2["id"], s1["id"]]),
                service=service,
                current_user=user,
            )
        )
        assert [s["id"] for s in reord["songs"]] == [s2["id"], s1["id"]]

        # Activate playlist
        act = asyncio.run(
            playlists_router.activate_playlist(
                playlist_id=pl_id,
                service=service,
                current_user=user,
            )
        )
        assert act["is_active"] is True

        # Remove first song
        rem = asyncio.run(
            playlists_router.remove_song_from_playlist(
                playlist_id=pl_id,
                song_id=s2["id"],
                service=service,
                current_user=user,
            )
        )
        assert rem["song_count"] == 1


class TestRunningStatsEfficiencyFix:
    """Test refactored py/RunningStats.py."""

    def test_running_stats_push_and_std(self):
        import RunningStats

        stats = RunningStats.Stats(8)
        stats.preload(np.ones(8) * 10.0, np.ones(8) * 2.0, sample_count=2)
        stats.push(np.ones(8) * 14.0)
        stats.push(np.ones(8) * 6.0)

        mean = stats.mean()
        std = stats.std()
        assert len(mean) == 8
        assert len(std) == 8
        assert np.all(std >= 0)
