"""
Playlist Management API Router.

Provides endpoints for creating, viewing, updating, reordering, activating,
and deleting playlists.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from api.core.auth import get_current_user, require_admin
from api.models.schemas import (
    PlaylistCreate,
    PlaylistDetailResponse,
    PlaylistReorder,
    PlaylistResponse,
    PlaylistSongAdd,
    PlaylistUpdate,
)
from api.services.media_service import MediaService, get_media_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/playlists", tags=["Playlists"])


def get_service() -> MediaService:
    """Dependency to get MediaService."""
    try:
        return get_media_service()
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Media service not initialized",
        )


@router.get("", response_model=List[PlaylistResponse])
async def list_playlists(
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(get_current_user),
):
    """List all playlists."""
    return service.list_playlists()


@router.get("/{playlist_id}", response_model=PlaylistDetailResponse)
async def get_playlist(
    playlist_id: int,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(get_current_user),
):
    """Get playlist details including its ordered list of songs."""
    playlist = service.get_playlist(playlist_id)
    if not playlist:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playlist not found")
    return playlist


@router.post("", response_model=PlaylistDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_playlist(
    payload: PlaylistCreate,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(require_admin),
):
    """Create a new playlist."""
    try:
        return service.create_playlist(
            name=payload.name,
            song_ids=payload.song_ids,
            is_active=payload.is_active,
        )
    except Exception as e:
        log.error("Failed to create playlist: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create playlist: {str(e)}",
        )


@router.patch("/{playlist_id}", response_model=PlaylistDetailResponse)
async def update_playlist(
    playlist_id: int,
    payload: PlaylistUpdate,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(require_admin),
):
    """Update playlist name, song order, or active status."""
    try:
        updated = service.update_playlist(
            playlist_id=playlist_id,
            name=payload.name,
            song_ids=payload.song_ids,
            is_active=payload.is_active,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playlist not found")
        return updated
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{playlist_id}/activate", response_model=PlaylistDetailResponse)
async def activate_playlist(
    playlist_id: int,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(require_admin),
):
    """Set this playlist as the active playlist."""
    updated = service.activate_playlist(playlist_id)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playlist not found")
    return updated


@router.post("/{playlist_id}/songs", response_model=PlaylistDetailResponse)
async def add_song_to_playlist(
    playlist_id: int,
    payload: PlaylistSongAdd,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(require_admin),
):
    """Add a song to the playlist."""
    updated = service.add_song_to_playlist(playlist_id, payload.song_id)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playlist or song not found")
    return updated


@router.delete("/{playlist_id}/songs/{song_id}", response_model=PlaylistDetailResponse)
async def remove_song_from_playlist(
    playlist_id: int,
    song_id: int,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(require_admin),
):
    """Remove a song from the playlist."""
    updated = service.remove_song_from_playlist(playlist_id, song_id)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playlist not found")
    return updated


@router.put("/{playlist_id}/reorder", response_model=PlaylistDetailResponse)
async def reorder_playlist_songs(
    playlist_id: int,
    payload: PlaylistReorder,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(require_admin),
):
    """Reorder songs in a playlist."""
    updated = service.update_playlist(playlist_id=playlist_id, song_ids=payload.song_ids)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playlist not found")
    return updated


@router.delete("/{playlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_playlist(
    playlist_id: int,
    service: MediaService = Depends(get_service),
    current_user: dict = Depends(require_admin),
):
    """Delete a playlist."""
    deleted = service.delete_playlist(playlist_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playlist not found")
    return None
