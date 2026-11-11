"""
Media Service for LightShowPi Neo API.

Manages songs, audio uploads, playlists (.playlist files + SQLite),
per-song light sequence mode (FFT vs Custom Sequence), custom sequence
CSV files, FFT-to-sequence generation, and audio waveform extraction.
"""

import configparser
import csv
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any

import audioread
import mutagen
import numpy as np

# Add py directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..', 'py'))

import fft as fft_module
from api.core.config import APIConfig
from api.core.database import Database

log = logging.getLogger(__name__)

SUPPORTED_AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".ogg", ".oga",
    ".m4a", ".mp4", ".m4b", ".aac", ".wma", ".aif"
}


class MediaService:
    """Service for managing songs, playlists, and custom light sequences."""

    def __init__(self, db: Database, config: APIConfig):
        self.db = db
        self.config = config
        self.home = os.getenv("SYNCHRONIZED_LIGHTS_HOME") or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../..")
        )
        self.music_dir = os.path.join(self.home, "music")
        self.playlists_dir = os.path.join(self.music_dir, "playlists")
        os.makedirs(self.music_dir, exist_ok=True)
        os.makedirs(self.playlists_dir, exist_ok=True)

        self.num_channels = int(getattr(self.config.hardware, "gpio_len", 8) or 8)
        self.default_timing_ms = int(getattr(self.config.cm.custom_sequences, "timing", 50) or 50)
        self.brightness_range = int(getattr(self.config.cm.custom_sequences, "brightness_range", 255) or 255)

        self.sync_filesystem_with_db()

    # =========================================================================
    # Path & File Helpers
    # =========================================================================

    def _expand_path(self, path_str: str) -> str:
        """Expand $SYNCHRONIZED_LIGHTS_HOME and normalize path."""
        expanded = path_str.replace("$SYNCHRONIZED_LIGHTS_HOME", self.home)
        expanded = os.path.expandvars(expanded)
        if not os.path.isabs(expanded):
            expanded = os.path.join(self.home, expanded)
        return os.path.abspath(expanded)

    @staticmethod
    def _get_song_aux_paths(song_abs_path: str) -> Dict[str, str]:
        """Return associated .csv, .cfg, .sync, and .waveform.json paths for a song."""
        directory = os.path.dirname(song_abs_path)
        basename = os.path.basename(song_abs_path)
        stem = song_abs_path.rsplit(".", 1)[0]
        return {
            "csv": f"{stem}.csv",
            "cfg": os.path.join(directory, f".{basename}.cfg"),
            "sync": os.path.join(directory, f".{basename}.sync"),
            "waveform": os.path.join(directory, f".{basename}.waveform.json"),
        }

    def _read_song_cfg_sequence_settings(self, cfg_path: str, csv_exists: bool) -> Dict[str, Any]:
        """Read sequence_mode and sequence_timing from per-song .cfg file."""
        mode = "custom" if csv_exists else "fft"
        timing = self.default_timing_ms

        if os.path.isfile(cfg_path):
            parser = configparser.RawConfigParser(allow_no_value=True)
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    parser.read_file(f)
                if parser.has_section("custom_lightshow"):
                    if parser.has_option("custom_lightshow", "sequence_mode"):
                        raw_mode = (parser.get("custom_lightshow", "sequence_mode") or "").strip().lower()
                        if raw_mode in ("fft", "custom"):
                            mode = raw_mode
                        elif raw_mode == "auto":
                            mode = "custom" if csv_exists else "fft"
                    if parser.has_option("custom_lightshow", "sequence_timing"):
                        try:
                            timing = int(float(parser.get("custom_lightshow", "sequence_timing")))
                        except (ValueError, TypeError):
                            pass
            except Exception as e:
                log.warning("Failed to read song config %s: %s", cfg_path, e)

        return {"sequence_mode": mode, "sequence_timing": timing}

    def _write_song_cfg_sequence_settings(
        self,
        cfg_path: str,
        sequence_mode: str,
        sequence_timing: Optional[int] = None,
        brightness_range: Optional[int] = None
    ) -> None:
        """Persist sequence_mode and sequence_timing to per-song .cfg file without losing [fft] section."""
        parser = configparser.RawConfigParser(allow_no_value=True)
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    parser.read_file(f)
            except Exception as e:
                log.warning("Could not parse existing %s, recreating: %s", cfg_path, e)

        if not parser.has_section("custom_lightshow"):
            parser.add_section("custom_lightshow")

        parser.set("custom_lightshow", "sequence_mode", sequence_mode)
        if sequence_timing is not None:
            parser.set("custom_lightshow", "sequence_timing", str(int(sequence_timing)))
        if brightness_range is not None:
            parser.set("custom_lightshow", "sequence_brightness_range", str(int(brightness_range)))

        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            parser.write(f)

    @staticmethod
    def _extract_audio_metadata(file_path: str) -> Dict[str, Any]:
        """Extract title, artist, duration, and file_size from an audio file."""
        stem = os.path.splitext(os.path.basename(file_path))[0]
        default_title = stem.replace("_", " ").replace("-", " - ").strip()
        title = default_title
        artist = None
        duration = None

        try:
            meta = mutagen.File(file_path, easy=True)
            if meta is not None:
                if "title" in meta and meta["title"]:
                    title = str(meta["title"][0]).strip() or default_title
                if "artist" in meta and meta["artist"]:
                    artist = str(meta["artist"][0]).strip()
                if hasattr(meta, "info") and getattr(meta.info, "length", None):
                    duration = float(meta.info.length)
        except Exception as e:
            log.debug("Mutagen metadata extraction fallback for %s: %s", file_path, e)

        if duration is None:
            try:
                with audioread.audio_open(file_path) as af:
                    duration = float(af.duration)
            except Exception:
                duration = None

        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            file_size = None

        return {
            "title": title,
            "artist": artist,
            "duration": round(duration, 2) if duration is not None else None,
            "file_size": file_size,
        }

    # =========================================================================
    # Filesystem <-> Database Synchronization
    # =========================================================================

    def sync_filesystem_with_db(self) -> None:
        """Scan music directory and active playlists so disk files and DB stay in sync."""
        try:
            discovered_files: List[str] = []
            for root, _dirs, files in os.walk(self.music_dir):
                for fname in sorted(files):
                    if fname.startswith("."):
                        continue
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in SUPPORTED_AUDIO_EXTENSIONS:
                        discovered_files.append(os.path.abspath(os.path.join(root, fname)))

            with self.db.get_connection() as conn:
                cursor = conn.cursor()

                # Map existing DB songs by normalized absolute path
                cursor.execute("SELECT id, file_path, title, sequence_mode, sequence_timing FROM songs")
                existing_rows = cursor.fetchall()
                by_path: Dict[str, sqlite3.Row] = {}
                for row in existing_rows:
                    norm_p = self._expand_path(row["file_path"])
                    if not os.path.isfile(norm_p):
                        # Remove stale song entries whose file no longer exists
                        cursor.execute("DELETE FROM songs WHERE id = ?", (row["id"],))
                    else:
                        by_path[norm_p] = row

                # Insert newly discovered audio files or refresh sequence status
                for abs_path in discovered_files:
                    aux = self._get_song_aux_paths(abs_path)
                    csv_exists = os.path.isfile(aux["csv"])
                    cfg_info = self._read_song_cfg_sequence_settings(aux["cfg"], csv_exists)

                    if abs_path not in by_path:
                        meta = self._extract_audio_metadata(abs_path)
                        cursor.execute("""
                            INSERT INTO songs (
                                file_path, title, artist, duration, file_size,
                                sequence_mode, sequence_timing, has_custom_sequence
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            abs_path,
                            meta["title"],
                            meta["artist"],
                            meta["duration"],
                            meta["file_size"],
                            cfg_info["sequence_mode"],
                            cfg_info["sequence_timing"],
                            1 if csv_exists else 0,
                        ))
                    else:
                        row = by_path[abs_path]
                        cursor.execute("""
                            UPDATE songs
                            SET file_path = ?,
                                has_custom_sequence = ?
                            WHERE id = ?
                        """, (abs_path, 1 if csv_exists else 0, row["id"]))

                # Reload song path -> id map
                cursor.execute("SELECT id, file_path, title FROM songs")
                path_to_song = {self._expand_path(r["file_path"]): r for r in cursor.fetchall()}

                # Ensure default playlist points to a valid file and sync its entries
                cursor.execute("SELECT id, name, file_path, is_active FROM playlists")
                playlists = cursor.fetchall()

                configured_playlist = self._expand_path(
                    getattr(self.config.lightshow, "playlist_path", "")
                    or os.path.join(self.music_dir, "sample", ".playlist")
                )

                for pl in playlists:
                    pl_id = pl["id"]
                    pl_path = self._expand_path(pl["file_path"])
                    if not os.path.isfile(pl_path) and os.path.isfile(configured_playlist) and pl["name"] == "Default":
                        pl_path = configured_playlist
                        cursor.execute("UPDATE playlists SET file_path = ? WHERE id = ?", (pl_path, pl_id))

                    cursor.execute("SELECT COUNT(*) FROM playlist_songs WHERE playlist_id = ?", (pl_id,))
                    has_linked_songs = cursor.fetchone()[0] > 0

                    if not has_linked_songs and os.path.isfile(pl_path):
                        # Import songs from existing .playlist file
                        try:
                            with open(pl_path, "r", encoding="utf-8") as pf:
                                reader = csv.reader(pf, delimiter="\t")
                                pos = 0
                                for line in reader:
                                    if len(line) >= 2:
                                        title_in_pl = line[0].strip()
                                        song_p = self._expand_path(line[1].strip())
                                        song_row = path_to_song.get(song_p)
                                        if song_row:
                                            cursor.execute("""
                                                INSERT OR IGNORE INTO playlist_songs (playlist_id, song_id, position)
                                                VALUES (?, ?, ?)
                                            """, (pl_id, song_row["id"], pos))
                                            if title_in_pl:
                                                cursor.execute(
                                                    "UPDATE songs SET title = ? WHERE id = ? AND (title IS NULL OR title = '')",
                                                    (title_in_pl, song_row["id"])
                                                )
                                            pos += 1
                        except Exception as e:
                            log.warning("Could not import existing playlist %s: %s", pl_path, e)

                conn.commit()
        except Exception as e:
            log.error("Error syncing filesystem with DB: %s", e)

    # =========================================================================
    # Song CRUD & Upload
    # =========================================================================

    def _format_song_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Format a database song row with live filesystem flags."""
        abs_path = self._expand_path(row["file_path"])
        aux = self._get_song_aux_paths(abs_path)
        csv_exists = os.path.isfile(aux["csv"])
        sync_exists = os.path.isfile(aux["sync"])
        seq_mode = row["sequence_mode"] if "sequence_mode" in row.keys() and row["sequence_mode"] else "fft"
        seq_timing = int(row["sequence_timing"]) if "sequence_timing" in row.keys() and row["sequence_timing"] else self.default_timing_ms

        return {
            "id": row["id"],
            "playlist_id": row["playlist_id"],
            "file_path": abs_path,
            "filename": os.path.basename(abs_path),
            "title": row["title"] or os.path.splitext(os.path.basename(abs_path))[0],
            "artist": row["artist"],
            "duration": row["duration"],
            "file_size": row["file_size"],
            "sequence_mode": seq_mode,
            "sequence_timing": seq_timing,
            "has_custom_sequence": csv_exists,
            "has_fft_cache": sync_exists,
            "uploaded_at": row["uploaded_at"],
        }

    def list_songs(self) -> List[Dict[str, Any]]:
        """List all songs in the library."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, playlist_id, file_path, title, artist, duration,
                       file_size, sequence_mode, sequence_timing, has_custom_sequence, uploaded_at
                FROM songs
                ORDER BY COALESCE(title, file_path) COLLATE NOCASE ASC
            """)
            return [self._format_song_row(r) for r in cursor.fetchall()]

    def get_song(self, song_id: int) -> Optional[Dict[str, Any]]:
        """Get a single song by ID."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, playlist_id, file_path, title, artist, duration,
                       file_size, sequence_mode, sequence_timing, has_custom_sequence, uploaded_at
                FROM songs
                WHERE id = ?
            """, (song_id,))
            row = cursor.fetchone()
            return self._format_song_row(row) if row else None

    def upload_song(
        self,
        filename: str,
        file_obj,
        title: Optional[str] = None,
        artist: Optional[str] = None,
        playlist_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Save an uploaded audio file, extract metadata, and add to DB (and optional playlist)."""
        ext = os.path.splitext(filename)[1].lower()
        if ext not in SUPPORTED_AUDIO_EXTENSIONS:
            raise ValueError(
                f"Unsupported audio format '{ext}'. Supported: {', '.join(sorted(SUPPORTED_AUDIO_EXTENSIONS))}"
            )

        # Sanitize filename
        safe_base = re.sub(r"[^A-Za-z0-9._\- ]+", "_", os.path.splitext(os.path.basename(filename))[0]).strip()
        if not safe_base:
            safe_base = "uploaded_song"
        dest_filename = f"{safe_base}{ext}"
        dest_path = os.path.abspath(os.path.join(self.music_dir, dest_filename))

        # Avoid overwriting existing file with same name
        counter = 1
        while os.path.exists(dest_path):
            dest_filename = f"{safe_base}_{counter}{ext}"
            dest_path = os.path.abspath(os.path.join(self.music_dir, dest_filename))
            counter += 1

        with open(dest_path, "wb") as out_f:
            shutil.copyfileobj(file_obj, out_f)

        meta = self._extract_audio_metadata(dest_path)
        final_title = (title or "").strip() or meta["title"]
        final_artist = (artist or "").strip() or meta["artist"]

        aux = self._get_song_aux_paths(dest_path)
        self._write_song_cfg_sequence_settings(aux["cfg"], "fft", self.default_timing_ms, self.brightness_range)

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO songs (
                    playlist_id, file_path, title, artist, duration, file_size,
                    sequence_mode, sequence_timing, has_custom_sequence
                )
                VALUES (?, ?, ?, ?, ?, ?, 'fft', ?, 0)
            """, (
                playlist_id,
                dest_path,
                final_title,
                final_artist,
                meta["duration"],
                meta["file_size"],
                self.default_timing_ms,
            ))
            song_id = cursor.lastrowid

            if playlist_id is not None:
                cursor.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM playlist_songs WHERE playlist_id = ?",
                    (playlist_id,)
                )
                next_pos = cursor.fetchone()[0]
                cursor.execute("""
                    INSERT OR IGNORE INTO playlist_songs (playlist_id, song_id, position)
                    VALUES (?, ?, ?)
                """, (playlist_id, song_id, next_pos))

            conn.commit()

        if playlist_id is not None:
            self._sync_playlist_file(playlist_id)

        return self.get_song(song_id)

    def update_song(
        self,
        song_id: int,
        title: Optional[str] = None,
        artist: Optional[str] = None,
        sequence_mode: Optional[str] = None,
        sequence_timing: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update song metadata and/or toggle sequence_mode ('fft' vs 'custom')."""
        song = self.get_song(song_id)
        if not song:
            return None

        new_title = title.strip() if title is not None and title.strip() else song["title"]
        new_artist = artist.strip() if artist is not None else song["artist"]
        new_mode = sequence_mode.lower() if sequence_mode else song["sequence_mode"]
        new_timing = int(sequence_timing) if sequence_timing is not None else song["sequence_timing"]

        aux = self._get_song_aux_paths(song["file_path"])
        self._write_song_cfg_sequence_settings(
            aux["cfg"],
            sequence_mode=new_mode,
            sequence_timing=new_timing,
            brightness_range=self.brightness_range,
        )

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE songs
                SET title = ?, artist = ?, sequence_mode = ?, sequence_timing = ?,
                    has_custom_sequence = ?
                WHERE id = ?
            """, (
                new_title,
                new_artist,
                new_mode,
                new_timing,
                1 if os.path.isfile(aux["csv"]) else 0,
                song_id,
            ))

            # Find all playlists containing this song so we can update their .playlist files if title changed
            cursor.execute("SELECT playlist_id FROM playlist_songs WHERE song_id = ?", (song_id,))
            pl_ids = [r[0] for r in cursor.fetchall()]
            conn.commit()

        if title is not None:
            for pid in pl_ids:
                self._sync_playlist_file(pid)

        return self.get_song(song_id)

    def delete_song(self, song_id: int) -> bool:
        """Delete a song from database, playlists, and disk."""
        song = self.get_song(song_id)
        if not song:
            return False

        abs_path = song["file_path"]
        aux = self._get_song_aux_paths(abs_path)

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT playlist_id FROM playlist_songs WHERE song_id = ?", (song_id,))
            affected_playlists = [r[0] for r in cursor.fetchall()]

            cursor.execute("DELETE FROM playlist_songs WHERE song_id = ?", (song_id,))
            cursor.execute("DELETE FROM songs WHERE id = ?", (song_id,))
            conn.commit()

        for pid in affected_playlists:
            self._sync_playlist_file(pid)

        # Remove audio file and auxiliary files
        for p in [abs_path, aux["csv"], aux["cfg"], aux["sync"], aux["waveform"]]:
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError as e:
                    log.warning("Could not remove file %s: %s", p, e)

        return True

    # =========================================================================
    # Custom Light Sequence & Waveform Methods
    # =========================================================================

    def get_sequence(self, song_id: int) -> Optional[Dict[str, Any]]:
        """Load custom light sequence for a song (or return empty structure if not created yet)."""
        song = self.get_song(song_id)
        if not song:
            return None

        aux = self._get_song_aux_paths(song["file_path"])
        csv_path = aux["csv"]
        timing_ms = song["sequence_timing"] or self.default_timing_ms
        steps: List[List[int]] = []

        if os.path.isfile(csv_path):
            try:
                with open(csv_path, "r", encoding="utf-8") as f:
                    for line in f:
                        stripped = line.strip()
                        if not stripped or stripped.startswith("#"):
                            continue
                        vals = [
                            max(0, min(self.brightness_range, int(round(float(x)))))
                            for x in stripped.split(",")
                            if x.strip() != ""
                        ]
                        if not vals:
                            continue
                        if len(vals) < self.num_channels:
                            vals.extend([0] * (self.num_channels - len(vals)))
                        elif len(vals) > self.num_channels:
                            vals = vals[:self.num_channels]
                        steps.append(vals)
            except Exception as e:
                log.error("Failed to read custom sequence %s: %s", csv_path, e)

        return {
            "song_id": song_id,
            "title": song["title"],
            "duration": song["duration"] or 60.0,
            "timing_ms": timing_ms,
            "num_channels": self.num_channels,
            "brightness_range": self.brightness_range,
            "sequence_mode": song["sequence_mode"],
            "has_custom_sequence": os.path.isfile(csv_path) and len(steps) > 0,
            "steps": steps,
        }

    def save_sequence(self, song_id: int, sequence_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Save custom light sequence CSV and update song configuration."""
        song = self.get_song(song_id)
        if not song:
            return None

        aux = self._get_song_aux_paths(song["file_path"])
        csv_path = aux["csv"]
        timing_ms = int(sequence_data.get("timing_ms") or song["sequence_timing"] or self.default_timing_ms)
        brightness_range = int(sequence_data.get("brightness_range") or self.brightness_range)
        raw_steps = sequence_data.get("steps") or []
        seq_mode = sequence_data.get("sequence_mode") or "custom"
        if hasattr(seq_mode, "value"):
            seq_mode = seq_mode.value

        normalized_steps: List[List[int]] = []
        for row in raw_steps:
            clean_row = [
                max(0, min(brightness_range, int(round(float(v)))))
                for v in row[:self.num_channels]
            ]
            if len(clean_row) < self.num_channels:
                clean_row.extend([0] * (self.num_channels - len(clean_row)))
            normalized_steps.append(clean_row)

        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        with open(csv_path, "w", encoding="utf-8", newline="\n") as f:
            for row in normalized_steps:
                f.write(",".join(str(v) for v in row) + "\n")

        self._write_song_cfg_sequence_settings(
            aux["cfg"],
            sequence_mode=seq_mode,
            sequence_timing=timing_ms,
            brightness_range=brightness_range,
        )

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE songs
                SET sequence_mode = ?, sequence_timing = ?, has_custom_sequence = 1
                WHERE id = ?
            """, (seq_mode, timing_ms, song_id))
            conn.commit()

        return self.get_sequence(song_id)

    def delete_sequence(self, song_id: int) -> Optional[Dict[str, Any]]:
        """Delete custom light sequence CSV for a song and revert to FFT mode."""
        song = self.get_song(song_id)
        if not song:
            return None

        aux = self._get_song_aux_paths(song["file_path"])
        if os.path.isfile(aux["csv"]):
            os.remove(aux["csv"])

        self._write_song_cfg_sequence_settings(
            aux["cfg"],
            sequence_mode="fft",
            sequence_timing=song["sequence_timing"],
            brightness_range=self.brightness_range,
        )

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE songs
                SET sequence_mode = 'fft', has_custom_sequence = 0
                WHERE id = ?
            """, (song_id,))
            conn.commit()

        return self.get_song(song_id)

    def generate_sequence_from_fft(
        self,
        song_id: int,
        timing_ms: int = 50,
        activate_custom: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Analyze song audio with FFT and generate an editable custom sequence CSV.

        This allows users to seed a custom sequence from the song's real frequency
        response and then customize sections in the Web Sequence Studio.
        """
        song = self.get_song(song_id)
        if not song:
            return None

        file_path = song["file_path"]
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        timing_ms = max(20, min(1000, int(timing_ms)))

        with audioread.audio_open(file_path) as af:
            sample_rate = af.samplerate
            channels = af.channels
            frames_per_step = max(256, int(sample_rate * (timing_ms / 1000.0)))
            bytes_per_step = frames_per_step * 2 * channels

            fft_calc = fft_module.FFT(
                chunk_size=frames_per_step,
                sample_rate=sample_rate,
                num_bins=self.num_channels,
                min_frequency=self.config.audio_processing.min_frequency,
                max_frequency=self.config.audio_processing.max_frequency,
                custom_channel_mapping=self.config.audio_processing.custom_channel_mapping,
                custom_channel_frequencies=self.config.audio_processing.custom_channel_frequencies,
                input_channels=channels,
                use_gpu=False,
            )

            buf = bytearray()
            raw_levels: List[np.ndarray] = []

            for chunk in af.read_data():
                buf.extend(chunk)
                while len(buf) >= bytes_per_step:
                    step_bytes = bytes(buf[:bytes_per_step])
                    del buf[:bytes_per_step]
                    raw_levels.append(fft_calc.calculate_levels(step_bytes))

            if len(buf) > 0:
                raw_levels.append(fft_calc.calculate_levels(bytes(buf)))

        if not raw_levels:
            steps = [[0] * self.num_channels]
        else:
            mat = np.asarray(raw_levels, dtype=np.float32)
            mean = np.full(self.num_channels, 12.0, dtype=np.float32)
            std = np.full(self.num_channels, 1.5, dtype=np.float32)

            for ch in range(self.num_channels):
                col = mat[:, ch]
                pos = col[col > 0]
                if len(pos) > 0:
                    mean[ch] = float(np.mean(pos))
                    std[ch] = max(0.25, float(np.std(pos)))

            sd_low = float(getattr(self.config.lightshow, "SD_low", 0.5) or 0.5)
            sd_high = float(getattr(self.config.lightshow, "SD_high", 0.75) or 0.75)
            denom = std * (sd_low + sd_high)
            denom = np.where(denom > 0, denom, 1.0)

            norm = (mat - mean + (std * sd_low)) / denom
            np.clip(norm, 0.0, 1.0, out=norm)
            scaled = np.rint(norm * self.brightness_range).astype(int)
            steps = scaled.tolist()

        return self.save_sequence(
            song_id,
            {
                "timing_ms": timing_ms,
                "brightness_range": self.brightness_range,
                "sequence_mode": "custom" if activate_custom else song["sequence_mode"],
                "steps": steps,
            },
        )

    def get_waveform(self, song_id: int, num_points: int = 500) -> Optional[Dict[str, Any]]:
        """Extract or load cached audio waveform envelope for the Web Sequence Studio."""
        song = self.get_song(song_id)
        if not song:
            return None

        file_path = song["file_path"]
        aux = self._get_song_aux_paths(file_path)
        wf_cache = aux["waveform"]

        if os.path.isfile(wf_cache):
            try:
                with open(wf_cache, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if len(cached.get("peaks", [])) == num_points:
                    return cached
            except Exception:
                pass

        peaks: List[float] = []
        duration = song["duration"] or 0.0

        try:
            with audioread.audio_open(file_path) as af:
                sample_rate = af.samplerate
                channels = af.channels
                if af.duration:
                    duration = float(af.duration)

                # Accumulate downsampled peak per chunk
                chunk_peaks: List[float] = []
                for chunk in af.read_data():
                    arr = np.frombuffer(chunk, dtype=np.int16)
                    if len(arr) > 0:
                        chunk_peaks.append(float(np.max(np.abs(arr))) / 32768.0)

                if chunk_peaks:
                    xp = np.linspace(0.0, 1.0, len(chunk_peaks))
                    x_new = np.linspace(0.0, 1.0, num_points)
                    resampled = np.interp(x_new, xp, chunk_peaks)
                    max_val = float(np.max(resampled))
                    if max_val > 0:
                        resampled = resampled / max_val
                    peaks = [round(float(x), 3) for x in resampled]
        except Exception as e:
            log.warning("Waveform extraction failed for %s: %s", file_path, e)

        if not peaks:
            peaks = [0.2] * num_points

        result = {
            "song_id": song_id,
            "duration": round(duration, 2),
            "peaks": peaks,
        }

        try:
            with open(wf_cache, "w", encoding="utf-8") as f:
                json.dump(result, f)
        except Exception:
            pass

        return result

    # =========================================================================
    # Playlist Management
    # =========================================================================

    def _sync_playlist_file(self, playlist_id: int) -> Optional[str]:
        """Write the .playlist tab-separated file on disk from SQLite playlist_songs."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, file_path, is_active FROM playlists WHERE id = ?", (playlist_id,))
            pl = cursor.fetchone()
            if not pl:
                return None

            file_path = self._expand_path(pl["file_path"])
            cursor.execute("""
                SELECT s.title, s.file_path
                FROM playlist_songs ps
                JOIN songs s ON s.id = ps.song_id
                WHERE ps.playlist_id = ?
                ORDER BY ps.position ASC, ps.id ASC
            """, (playlist_id,))
            rows = cursor.fetchall()

        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                song_abs = self._expand_path(r["file_path"])
                title = (r["title"] or os.path.splitext(os.path.basename(song_abs))[0]).replace("\t", " ")
                f.write(f"{title}\t{song_abs}\n")

        if pl["is_active"]:
            self._update_active_playlist_config(file_path)

        return file_path

    def _update_active_playlist_config(self, playlist_path: str) -> None:
        """Update active playlist path in runtime config and overrides.cfg."""
        try:
            self.config.lightshow.playlist_path = playlist_path
            self.config.cm.lightshow.playlist_path = playlist_path

            overrides_path = os.path.join(self.home, "config", "overrides.cfg")
            if os.path.isfile(overrides_path):
                with open(overrides_path, "r", encoding="utf-8") as f:
                    content = f.read()

                rel_or_abs = playlist_path.replace(self.home, "$SYNCHRONIZED_LIGHTS_HOME")
                if re.search(r"^\s*playlist_path\s*=", content, flags=re.MULTILINE):
                    content = re.sub(
                        r"^\s*playlist_path\s*=.*$",
                        f"playlist_path = {rel_or_abs}",
                        content,
                        count=1,
                        flags=re.MULTILINE,
                    )
                    with open(overrides_path, "w", encoding="utf-8") as f:
                        f.write(content)
        except Exception as e:
            log.warning("Could not update playlist_path in overrides.cfg: %s", e)

    def list_playlists(self) -> List[Dict[str, Any]]:
        """List all playlists with song counts."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT p.id, p.name, p.file_path, p.is_active, p.created_at,
                       COUNT(ps.song_id) AS song_count
                FROM playlists p
                LEFT JOIN playlist_songs ps ON ps.playlist_id = p.id
                GROUP BY p.id
                ORDER BY p.is_active DESC, p.name COLLATE NOCASE ASC
            """)
            return [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "file_path": self._expand_path(r["file_path"]),
                    "is_active": bool(r["is_active"]),
                    "song_count": int(r["song_count"] or 0),
                    "created_at": r["created_at"],
                }
                for r in cursor.fetchall()
            ]

    def get_playlist(self, playlist_id: int) -> Optional[Dict[str, Any]]:
        """Get a playlist with its ordered list of songs."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, name, file_path, is_active, created_at FROM playlists WHERE id = ?",
                (playlist_id,)
            )
            pl = cursor.fetchone()
            if not pl:
                return None

            cursor.execute("""
                SELECT s.id, s.playlist_id, s.file_path, s.title, s.artist, s.duration,
                       s.file_size, s.sequence_mode, s.sequence_timing, s.has_custom_sequence, s.uploaded_at
                FROM playlist_songs ps
                JOIN songs s ON s.id = ps.song_id
                WHERE ps.playlist_id = ?
                ORDER BY ps.position ASC, ps.id ASC
            """, (playlist_id,))
            songs = [self._format_song_row(r) for r in cursor.fetchall()]

        return {
            "id": pl["id"],
            "name": pl["name"],
            "file_path": self._expand_path(pl["file_path"]),
            "is_active": bool(pl["is_active"]),
            "song_count": len(songs),
            "created_at": pl["created_at"],
            "songs": songs,
        }

    def get_active_playlist(self) -> Optional[Dict[str, Any]]:
        """Return currently active playlist (or first playlist if none marked active)."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM playlists WHERE is_active = 1 LIMIT 1")
            row = cursor.fetchone()
            if not row:
                cursor.execute("SELECT id FROM playlists ORDER BY id ASC LIMIT 1")
                row = cursor.fetchone()
            return self.get_playlist(row["id"]) if row else None

    def create_playlist(
        self,
        name: str,
        song_ids: Optional[List[int]] = None,
        is_active: bool = False,
    ) -> Dict[str, Any]:
        """Create a new playlist and write its .playlist file."""
        clean_name = name.strip()
        slug = re.sub(r"[^A-Za-z0-9_\-]+", "_", clean_name.lower()).strip("_") or "playlist"
        file_path = os.path.abspath(os.path.join(self.playlists_dir, f"{slug}.playlist"))

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            if is_active:
                cursor.execute("UPDATE playlists SET is_active = 0")

            cursor.execute("""
                INSERT INTO playlists (name, file_path, is_active)
                VALUES (?, ?, ?)
            """, (clean_name, file_path, 1 if is_active else 0))
            playlist_id = cursor.lastrowid

            if song_ids:
                for pos, sid in enumerate(song_ids):
                    cursor.execute("""
                        INSERT OR IGNORE INTO playlist_songs (playlist_id, song_id, position)
                        VALUES (?, ?, ?)
                    """, (playlist_id, sid, pos))

            conn.commit()

        self._sync_playlist_file(playlist_id)
        return self.get_playlist(playlist_id)

    def update_playlist(
        self,
        playlist_id: int,
        name: Optional[str] = None,
        song_ids: Optional[List[int]] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update playlist name, song list/order, or active state."""
        existing = self.get_playlist(playlist_id)
        if not existing:
            return None

        with self.db.get_connection() as conn:
            cursor = conn.cursor()

            if is_active:
                cursor.execute("UPDATE playlists SET is_active = 0")
                cursor.execute("UPDATE playlists SET is_active = 1 WHERE id = ?", (playlist_id,))
            elif is_active is False:
                cursor.execute("UPDATE playlists SET is_active = 0 WHERE id = ?", (playlist_id,))

            if name is not None and name.strip():
                cursor.execute("UPDATE playlists SET name = ? WHERE id = ?", (name.strip(), playlist_id))

            if song_ids is not None:
                cursor.execute("DELETE FROM playlist_songs WHERE playlist_id = ?", (playlist_id,))
                for pos, sid in enumerate(song_ids):
                    cursor.execute("""
                        INSERT OR IGNORE INTO playlist_songs (playlist_id, song_id, position)
                        VALUES (?, ?, ?)
                    """, (playlist_id, sid, pos))

            conn.commit()

        self._sync_playlist_file(playlist_id)
        return self.get_playlist(playlist_id)

    def activate_playlist(self, playlist_id: int) -> Optional[Dict[str, Any]]:
        """Set a playlist as the active playlist for playback."""
        return self.update_playlist(playlist_id, is_active=True)

    def add_song_to_playlist(self, playlist_id: int, song_id: int) -> Optional[Dict[str, Any]]:
        """Add a song to the end of a playlist."""
        if not self.get_playlist(playlist_id) or not self.get_song(song_id):
            return None

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM playlist_songs WHERE playlist_id = ?",
                (playlist_id,)
            )
            next_pos = cursor.fetchone()[0]
            cursor.execute("""
                INSERT OR IGNORE INTO playlist_songs (playlist_id, song_id, position)
                VALUES (?, ?, ?)
            """, (playlist_id, song_id, next_pos))
            conn.commit()

        self._sync_playlist_file(playlist_id)
        return self.get_playlist(playlist_id)

    def remove_song_from_playlist(self, playlist_id: int, song_id: int) -> Optional[Dict[str, Any]]:
        """Remove a song from a playlist."""
        if not self.get_playlist(playlist_id):
            return None

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM playlist_songs WHERE playlist_id = ? AND song_id = ?",
                (playlist_id, song_id)
            )
            # Normalize positions
            cursor.execute(
                "SELECT id FROM playlist_songs WHERE playlist_id = ? ORDER BY position ASC, id ASC",
                (playlist_id,)
            )
            for idx, r in enumerate(cursor.fetchall()):
                cursor.execute("UPDATE playlist_songs SET position = ? WHERE id = ?", (idx, r["id"]))
            conn.commit()

        self._sync_playlist_file(playlist_id)
        return self.get_playlist(playlist_id)

    def delete_playlist(self, playlist_id: int) -> bool:
        """Delete a playlist."""
        pl = self.get_playlist(playlist_id)
        if not pl:
            return False

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM playlist_songs WHERE playlist_id = ?", (playlist_id,))
            cursor.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))

            if pl["is_active"]:
                cursor.execute("SELECT id FROM playlists ORDER BY id ASC LIMIT 1")
                next_pl = cursor.fetchone()
                if next_pl:
                    cursor.execute("UPDATE playlists SET is_active = 1 WHERE id = ?", (next_pl["id"],))
            conn.commit()

        # Only delete .playlist file if it is inside music/playlists/ (preserve music/sample/.playlist)
        if os.path.isfile(pl["file_path"]) and self.playlists_dir in pl["file_path"]:
            try:
                os.remove(pl["file_path"])
            except OSError:
                pass

        return True


# Global singleton helper
_media_service: Optional[MediaService] = None


def set_media_service(service: MediaService) -> None:
    """Set global media service instance."""
    global _media_service
    _media_service = service


def get_media_service() -> MediaService:
    """Get global media service instance."""
    if _media_service is None:
        raise RuntimeError("MediaService not initialized")
    return _media_service
