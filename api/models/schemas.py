"""
Pydantic models for LightShowPi Neo API.

These models provide request/response validation and documentation
for all API endpoints.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime
from enum import Enum


# Enums for controlled values
class LightshowState(str, Enum):
    """Current state of the lightshow system."""
    IDLE = "idle"
    STARTING = "starting"
    SCHEDULED = "scheduled"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    PLAYING = "playing"
    TEST_MODE = "test_mode"
    ERROR = "error"


class StopMode(str, Enum):
    """Mode for stopping the lightshow."""
    PAUSE = "pause"  # Pause until next scheduled event
    STOP = "stop"    # Stop completely and disable schedule


class LightshowMode(str, Enum):
    """Lightshow operating modes."""
    PLAYLIST = "playlist"  # Play music with synchronized lights
    AMBIENT = "ambient"    # Lights on steady, no music
    AUDIO_IN = "audio-in"  # Real-time audio input mode
    STREAM_IN = "stream-in"  # Stream input mode


class SequenceMode(str, Enum):
    """Light sequence mode for a song."""
    FFT = "fft"
    CUSTOM = "custom"


class ClientStatusEnum(str, Enum):
    """Status of a client Pi."""
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"


class EventType(str, Enum):
    """Types of system events."""
    START = "start"
    STOP = "stop"
    SKIP = "skip"
    REBOOT = "reboot"
    RESTART = "restart"
    ERROR = "error"
    CLIENT_CONNECT = "client_connect"
    CLIENT_DISCONNECT = "client_disconnect"


# User models
class UserCreate(BaseModel):
    """Request model for creating a new user."""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8)
    allowed_ips: Optional[List[str]] = None


class UserLogin(BaseModel):
    """Request model for user login."""
    username: str
    password: str


class UserResponse(BaseModel):
    """Response model for user data."""
    id: int
    username: str
    allowed_ips: Optional[List[str]] = None
    created_at: datetime


class Token(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"


# Client models
class ClientCreate(BaseModel):
    """Request model for creating a new client Pi."""
    name: str = Field(..., min_length=1, max_length=100)


class ClientResponse(BaseModel):
    """Response model for client data."""
    id: str
    name: str
    auth_key: str  # Only returned on creation
    ip_address: Optional[str] = None
    last_seen: Optional[datetime] = None
    status: ClientStatusEnum
    created_at: datetime


class ClientStatus(BaseModel):
    """Status information for a client Pi."""
    id: str
    name: str
    ip_address: Optional[str] = None
    status: ClientStatusEnum
    last_seen: Optional[datetime] = None


# Schedule models
class ScheduleCreate(BaseModel):
    """Request model for creating a new schedule."""
    start_time: str = Field(..., pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")
    stop_time: str = Field(..., pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")
    mode: LightshowMode = LightshowMode.PLAYLIST
    enabled: bool = True
    days_of_week: List[int] = Field(default=[0, 1, 2, 3, 4, 5, 6])

    @field_validator("days_of_week")
    @classmethod
    def validate_days(cls, v):
        """Validate days of week are 0-6 (0=Sunday, 6=Saturday)."""
        if not all(0 <= day <= 6 for day in v):
            raise ValueError("Days must be 0-6 (0=Sunday, 6=Saturday)")
        return v


class ScheduleUpdate(BaseModel):
    """Request model for updating a schedule (all fields optional)."""
    start_time: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")
    stop_time: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")
    mode: Optional[LightshowMode] = None
    enabled: Optional[bool] = None
    days_of_week: Optional[List[int]] = None

    @field_validator("days_of_week")
    @classmethod
    def validate_days(cls, v):
        """Validate days of week are 0-6 (0=Sunday, 6=Saturday)."""
        if v is not None and not all(0 <= day <= 6 for day in v):
            raise ValueError("Days must be 0-6 (0=Sunday, 6=Saturday)")
        return v


class ScheduleResponse(BaseModel):
    """Response model for schedule data."""
    id: int
    start_time: str
    stop_time: str
    mode: str
    enabled: bool
    days_of_week: List[int]
    updated_by: Optional[str] = None
    updated_at: datetime


class ScheduledEventResponse(BaseModel):
    """Response model for upcoming scheduled events."""
    schedule_id: int
    action: str  # "start" or "stop"
    time: str  # ISO 8601 datetime
    job_id: str


# Song models
class SongResponse(BaseModel):
    """Response model for song data."""
    id: int
    playlist_id: Optional[int] = None
    file_path: str
    filename: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    duration: Optional[float] = None
    file_size: Optional[int] = None
    sequence_mode: str = "fft"
    sequence_timing: int = 50
    has_custom_sequence: bool = False
    has_fft_cache: bool = False
    uploaded_at: Optional[datetime] = None


class SongUpdate(BaseModel):
    """Request model for updating song metadata or light sequence mode."""
    title: Optional[str] = None
    artist: Optional[str] = None
    sequence_mode: Optional[SequenceMode] = None
    sequence_timing: Optional[int] = Field(None, ge=10, le=2000)


class SongUpload(BaseModel):
    """Metadata for song upload."""
    title: Optional[str] = None
    artist: Optional[str] = None
    playlist_id: Optional[int] = None


# Custom Light Sequence models
class SequenceData(BaseModel):
    """Custom light sequence payload for a song."""
    song_id: Optional[int] = None
    timing_ms: int = Field(default=50, ge=10, le=2000)
    num_channels: int = Field(default=8, ge=1, le=64)
    brightness_range: int = Field(default=255, ge=1, le=255)
    sequence_mode: Optional[SequenceMode] = None
    steps: List[List[int]] = Field(default_factory=list)


class SequenceGenerateRequest(BaseModel):
    """Request model for generating a custom sequence from FFT or a pattern."""
    timing_ms: int = Field(default=50, ge=10, le=2000)
    activate_custom: bool = True


# Playlist models
class PlaylistCreate(BaseModel):
    """Request model for creating a new playlist."""
    name: str = Field(..., min_length=1, max_length=100)
    song_ids: Optional[List[int]] = None
    is_active: bool = False


class PlaylistUpdate(BaseModel):
    """Request model for updating a playlist."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    song_ids: Optional[List[int]] = None
    is_active: Optional[bool] = None


class PlaylistSongAdd(BaseModel):
    """Request model for adding a song to a playlist."""
    song_id: int


class PlaylistReorder(BaseModel):
    """Request model for reordering songs in a playlist."""
    song_ids: List[int]


class PlaylistResponse(BaseModel):
    """Response model for playlist summary."""
    id: int
    name: str
    file_path: str
    is_active: bool
    song_count: int = 0
    created_at: Optional[datetime] = None


class PlaylistDetailResponse(PlaylistResponse):
    """Response model for playlist with ordered songs."""
    songs: List[SongResponse] = []


# Lightshow control models
class LightshowStart(BaseModel):
    """Request model for starting the lightshow."""
    mode: Optional[LightshowMode] = None
    playlist: Optional[str] = None
    playlist_id: Optional[int] = None
    song_id: Optional[int] = None
    resume_schedule: bool = True


class LightshowStop(BaseModel):
    """Request model for stopping the lightshow."""
    mode: StopMode = StopMode.PAUSE


class LightshowStatus(BaseModel):
    """Response model for lightshow status."""
    state: LightshowState
    current_song: Optional[str] = None
    current_song_id: Optional[int] = None
    playlist: Optional[str] = None
    elapsed_time: Optional[float] = None
    total_time: Optional[float] = None
    schedule_enabled: bool
    next_scheduled_event: Optional[str] = None
    clients: List[ClientStatus] = []


# Test mode models
class ChannelControl(BaseModel):
    """Request model for controlling a specific channel."""
    channel: int = Field(..., ge=0, le=15)
    state: bool  # True = on, False = off
    client_id: Optional[str] = None  # If None, applies to server


# Analytics models
class SongPlayStats(BaseModel):
    """Statistics for a song."""
    song_id: int
    title: Optional[str] = None
    play_count: int
    skip_count: int
    completion_rate: float  # 0.0 - 1.0


class PopularSongsResponse(BaseModel):
    """Response model for popular songs."""
    most_played: List[SongPlayStats]
    most_skipped: List[SongPlayStats]
    highest_completion: List[SongPlayStats]


# System models
class SystemHealth(BaseModel):
    """System health status."""
    api_version: str
    uptime: float  # seconds
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    disk_usage: Optional[float] = None
    lightshow_state: LightshowState
    database_ok: bool
    clients_online: int


class SystemEvent(BaseModel):
    """System event record."""
    id: int
    event_type: EventType
    client_id: Optional[str] = None
    details: Optional[str] = None
    timestamp: datetime


# WebSocket models
class WSMessage(BaseModel):
    """WebSocket message format."""
    type: str
    data: dict


class WSStatusUpdate(BaseModel):
    """WebSocket status update message."""
    type: str = "status_update"
    state: LightshowState
    current_song: Optional[str] = None
    elapsed_time: Optional[float] = None
