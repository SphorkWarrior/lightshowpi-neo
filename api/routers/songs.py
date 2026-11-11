"""
Songs & Custom Light Sequences API Router.

Provides endpoints for:
- Listing, uploading, updating, and deleting songs
- Toggling between FFT-based light control and Custom Light Sequence per song
- Streaming song audio and waveform data for the Web Sequence Studio
- Creating, loading, saving, generating (from FFT), and deleting custom light sequences
"""

import logging
import mimetypes
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from api.core.auth import get_current_user, require_admin
from api.models.schemas import (
    SequenceData,
    SequenceGenerateRequest,
    SongResponse,
    SongUpdate,
)
from api.services.media_service import MediaService, get_media_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/songs", tags=["Songs & Sequences"])


def get_service() -> MediaService:
    """Dependency to get MediaService."""
    try:
        return get_media_service()
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Media service not initialized",
        )


@router.get("", response_model=List[SongResponse])
async def list_songs(
    refresh: bool = False,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(get_current_user),
):
    """List all songs in the library."""
    if refresh:
        service.sync_filesystem_with_db()
    return service.list_songs()


@router.get("/{song_id}", response_model=SongResponse)
async def get_song(
    song_id: int,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(get_current_user),
):
    """Get details for a specific song."""
    song = service.get_song(song_id)
    if not song:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Song not found")
    return song


@router.post("/upload", response_model=SongResponse, status_code=status.HTTP_201_CREATED)
async def upload_song(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    artist: Optional[str] = Form(None),
    playlist_id: Optional[int] = Form(None),
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(require_admin),
):
    """Upload a new audio file to the library (and optionally add to a playlist)."""
    if not file or not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No audio file provided",
        )

    try:
        song = service.upload_song(
            filename=file.filename,
            file_obj=file.file,
            title=title,
            artist=artist,
            playlist_id=playlist_id,
        )
        log.info("Song '%s' uploaded by %s", song["title"], current_user["username"])
        return song
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        log.error("Failed to upload song: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload song: {str(e)}",
        )


@router.patch("/{song_id}", response_model=SongResponse)
async def update_song(
    song_id: int,
    payload: SongUpdate,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(require_admin),
):
    """Update song metadata or toggle sequence_mode ('fft' vs 'custom')."""
    updated = service.update_song(
        song_id=song_id,
        title=payload.title,
        artist=payload.artist,
        sequence_mode=payload.sequence_mode.value if payload.sequence_mode else None,
        sequence_timing=payload.sequence_timing,
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Song not found")
    return updated


@router.delete("/{song_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_song(
    song_id: int,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(require_admin),
):
    """Delete a song from the library and disk."""
    deleted = service.delete_song(song_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Song not found")
    return None


@router.get("/{song_id}/audio")
async def stream_song_audio(
    song_id: int,
    service: MediaService = Depends(get_service),
):
    """Stream the song's audio file for playback/preview in the Web Sequence Studio."""
    song = service.get_song(song_id)
    if not song or not os.path.isfile(song["file_path"]):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audio file not found")

    media_type, _ = mimetypes.guess_type(song["file_path"])
    return FileResponse(
        path=song["file_path"],
        media_type=media_type or "audio/mpeg",
        filename=os.path.basename(song["file_path"]),
    )


@router.get("/{song_id}/waveform")
async def get_song_waveform(
    song_id: int,
    points: int = 500,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(get_current_user),
):
    """Get downsampled audio waveform peaks for the Web Sequence Studio timeline."""
    points = max(50, min(2000, points))
    wf = service.get_waveform(song_id, num_points=points)
    if not wf:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Song not found")
    return wf


@router.get("/{song_id}/sequence")
async def get_song_sequence(
    song_id: int,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(get_current_user),
):
    """Get custom light sequence data for a song."""
    seq = service.get_sequence(song_id)
    if not seq:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Song not found")
    return seq


@router.put("/{song_id}/sequence")
async def save_song_sequence(
    song_id: int,
    payload: SequenceData,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(require_admin),
):
    """Save custom light sequence for a song."""
    seq = service.save_sequence(song_id, payload.model_dump())
    if not seq:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Song not found")
    log.info("Custom sequence saved for song %s by %s", song_id, current_user["username"])
    return seq


@router.delete("/{song_id}/sequence", response_model=SongResponse)
async def delete_song_sequence(
    song_id: int,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(require_admin),
):
    """Delete custom light sequence for a song and revert to FFT mode."""
    song = service.delete_sequence(song_id)
    if not song:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Song not found")
    return song


@router.post("/{song_id}/sequence/generate-fft")
async def generate_sequence_from_fft(
    song_id: int,
    payload: SequenceGenerateRequest,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(require_admin),
):
    """Generate a custom light sequence seeded from the song's FFT frequency response."""
    try:
        seq = service.generate_sequence_from_fft(
            song_id=song_id,
            timing_ms=payload.timing_ms,
            activate_custom=payload.activate_custom,
        )
        if not seq:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Song not found")
        return seq
    except HTTPException:
        raise
    except Exception as e:
        log.error("Failed to generate sequence from FFT for song %s: %s", song_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate sequence from FFT: {str(e)}",
        )
