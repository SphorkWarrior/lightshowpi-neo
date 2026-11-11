"""
Lightshow Control API Endpoints

Provides REST API endpoints for controlling the lightshow.
"""

import logging
import os
from fastapi import APIRouter, Depends, HTTPException, status

from api.core.auth import require_admin
from api.core.config import get_api_config
from api.services.lightshow_manager import (
    get_lightshow_manager,
    set_lightshow_manager,
    LightshowManager,
)
from api.models.schemas import (
    LightshowStart,
    LightshowStop,
    LightshowStatus,
    StopMode,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lightshow", tags=["Lightshow Control"])


def get_manager() -> LightshowManager:
    """Dependency to get the lightshow manager instance."""
    return get_lightshow_manager()


def set_manager(manager: LightshowManager) -> None:
    """Set the global lightshow manager instance."""
    set_lightshow_manager(manager)


@router.post("/start", status_code=status.HTTP_200_OK)
async def start_lightshow(
    request: LightshowStart,
    manager: LightshowManager = Depends(get_manager),
    current_user: dict = Depends(require_admin),
):
    """
    Start the lightshow with an optional mode, playlist, playlist_id, or song_id.
    """
    playlist_path = request.playlist
    song_path = None

    try:
        from api.services.media_service import get_media_service
        media_service = get_media_service()

        if request.playlist_id is not None:
            pl = media_service.get_playlist(request.playlist_id)
            if not pl:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Playlist {request.playlist_id} not found",
                )
            playlist_path = pl["file_path"]

        if request.song_id is not None:
            song_info = media_service.get_song(request.song_id)
            if not song_info:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Song {request.song_id} not found",
                )
            song_path = song_info["file_path"]
    except HTTPException:
        raise
    except Exception as e:
        log.debug(f"Media service lookup skipped during start: {e}")

    # Stop existing lightshow if starting a new song/playlist explicitly
    if manager.is_running() and (request.song_id is not None or request.playlist_id is not None or request.playlist):
        manager.stop(graceful=True)

    success = manager.start(
        mode=request.mode.value if request.mode else None,
        playlist=playlist_path,
        song=song_path,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start lightshow: {manager.error_message}",
        )

    log.info(f"Lightshow started by user: {current_user['username']}")

    return {
        "message": "Lightshow started successfully",
        "status": manager.get_status(),
    }


@router.post("/stop", status_code=status.HTTP_200_OK)
async def stop_lightshow(
    request: LightshowStop,
    manager: LightshowManager = Depends(get_manager),
    current_user: dict = Depends(require_admin),
):
    """Stop the lightshow."""
    success = manager.stop(graceful=True)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to stop lightshow: {manager.error_message}",
        )

    log.info(f"Lightshow stopped ({request.mode.value}) by user: {current_user['username']}")

    return {
        "message": f"Lightshow stopped ({request.mode.value})",
        "status": manager.get_status(),
    }


@router.post("/skip", status_code=status.HTTP_200_OK)
async def skip_song(
    manager: LightshowManager = Depends(get_manager),
    current_user: dict = Depends(require_admin),
):
    """Skip to the next song."""
    if not manager.is_running():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lightshow is not running",
        )

    success = manager.skip()

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send skip signal",
        )

    log.info(f"Song skipped by user: {current_user['username']}")

    return {
        "message": "Skip signal sent",
        "note": "Skip will take effect within a moment",
    }


@router.get("/status", response_model=LightshowStatus)
async def get_status(
    manager: LightshowManager = Depends(get_manager),
):
    """Get current lightshow status."""
    status_info = manager.get_status()

    playlist_display = status_info.get("current_playlist")
    try:
        from api.services.media_service import get_media_service
        media_service = get_media_service()
        active_pl = media_service.get_active_playlist()
        if active_pl:
            if not playlist_display or os.path.abspath(playlist_display) == os.path.abspath(active_pl["file_path"]):
                playlist_display = active_pl["name"]
    except Exception:
        pass

    return {
        "state": status_info["state"],
        "current_song": status_info.get("current_song"),
        "current_song_id": None,
        "playlist": playlist_display,
        "elapsed_time": None,
        "total_time": None,
        "schedule_enabled": True,
        "next_scheduled_event": None,
        "clients": [],
    }


@router.post("/restart", status_code=status.HTTP_200_OK)
async def restart_lightshow(
    manager: LightshowManager = Depends(get_manager),
    current_user: dict = Depends(require_admin),
):
    """Restart the lightshow."""
    success = manager.restart()

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to restart lightshow: {manager.error_message}",
        )

    log.info(f"Lightshow restarted by user: {current_user['username']}")

    return {
        "message": "Lightshow restarted successfully",
        "status": manager.get_status(),
    }
