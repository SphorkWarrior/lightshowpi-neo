/**
 * API service for LightShowPi Neo
 *
 * Handles all communication with the FastAPI backend
 */

const API_BASE = '/api';

class ApiError extends Error {
  constructor(message, status, details) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.details = details;
  }
}

/**
 * Get auth token from localStorage
 */
function getAuthToken() {
  return localStorage.getItem('authToken');
}

/**
 * Set auth token in localStorage
 */
function setAuthToken(token) {
  localStorage.setItem('authToken', token);
}

/**
 * Clear auth token from localStorage
 */
function clearAuthToken() {
  localStorage.removeItem('authToken');
}

/**
 * Make authenticated API request
 */
async function apiRequest(endpoint, options = {}) {
  const token = getAuthToken();
  const isFormData = options.body instanceof FormData;

  const headers = {
    ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
    ...options.headers
  };

  if (token && !options.skipAuth) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const config = {
    ...options,
    headers
  };

  try {
    const response = await fetch(`${API_BASE}${endpoint}`, config);

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new ApiError(
        error.detail || `HTTP ${response.status}`,
        response.status,
        error
      );
    }

    if (response.status === 204) {
      return null;
    }

    return await response.json();
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    throw new ApiError('Network error', 0, { message: error.message });
  }
}

// =============================================================================
// Authentication API
// =============================================================================

export const auth = {
  async login(username, password) {
    const data = await apiRequest('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
      skipAuth: true
    });

    if (data.access_token) {
      setAuthToken(data.access_token);
    }

    return data;
  },

  logout() {
    clearAuthToken();
  },

  isAuthenticated() {
    return !!getAuthToken();
  }
};

// =============================================================================
// Lightshow Control API
// =============================================================================

export const lightshow = {
  async getStatus() {
    return apiRequest('/lightshow/status');
  },

  async start(resumeScheduleOrOptions = true, mode = null, playlistId = null, songId = null) {
    let payload;
    if (typeof resumeScheduleOrOptions === 'object' && resumeScheduleOrOptions !== null) {
      payload = {
        resume_schedule: resumeScheduleOrOptions.resumeSchedule ?? true,
        mode: resumeScheduleOrOptions.mode ?? null,
        playlist: resumeScheduleOrOptions.playlist ?? null,
        playlist_id: resumeScheduleOrOptions.playlistId ?? null,
        song_id: resumeScheduleOrOptions.songId ?? null
      };
    } else {
      payload = {
        resume_schedule: resumeScheduleOrOptions,
        mode,
        playlist_id: playlistId,
        song_id: songId
      };
    }

    return apiRequest('/lightshow/start', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  },

  async stop(mode = 'pause') {
    return apiRequest('/lightshow/stop', {
      method: 'POST',
      body: JSON.stringify({ mode })
    });
  },

  async skip() {
    return apiRequest('/lightshow/skip', {
      method: 'POST'
    });
  }
};

// =============================================================================
// Songs & Custom Light Sequences API
// =============================================================================

export const songs = {
  async list(refresh = false) {
    const query = refresh ? '?refresh=true' : '';
    return apiRequest(`/songs${query}`);
  },

  async get(songId) {
    return apiRequest(`/songs/${songId}`);
  },

  async upload(file, titleOrOpts = null, artist = null, playlistId = null) {
    const formData = new FormData();
    formData.append('file', file);

    let finalTitle = titleOrOpts;
    let finalArtist = artist;
    let finalPlaylistId = playlistId;

    if (typeof titleOrOpts === 'object' && titleOrOpts !== null) {
      finalTitle = titleOrOpts.title || null;
      finalArtist = titleOrOpts.artist || null;
      finalPlaylistId = titleOrOpts.playlistId ?? null;
    }

    if (finalTitle) formData.append('title', finalTitle);
    if (finalArtist) formData.append('artist', finalArtist);
    if (finalPlaylistId !== null && finalPlaylistId !== undefined && finalPlaylistId !== '') {
      formData.append('playlist_id', String(finalPlaylistId));
    }

    return apiRequest('/songs/upload', {
      method: 'POST',
      body: formData
    });
  },

  async update(songId, data) {
    return apiRequest(`/songs/${songId}`, {
      method: 'PATCH',
      body: JSON.stringify(data)
    });
  },

  async delete(songId) {
    return apiRequest(`/songs/${songId}`, {
      method: 'DELETE'
    });
  },

  getAudioUrl(songId) {
    return `${API_BASE}/songs/${songId}/audio`;
  },

  async getWaveform(songId, points = 500) {
    return apiRequest(`/songs/${songId}/waveform?points=${points}`);
  },

  async getSequence(songId) {
    return apiRequest(`/songs/${songId}/sequence`);
  },

  async saveSequence(songId, sequenceData, activateCustom = true) {
    const payload = {
      timing_ms: sequenceData.timing_ms || 50,
      num_channels: sequenceData.num_channels || sequenceData.channels || 8,
      brightness_range: sequenceData.brightness_range || 255,
      sequence_mode: activateCustom ? 'custom' : undefined,
      steps: sequenceData.steps || sequenceData.frames || []
    };
    return apiRequest(`/songs/${songId}/sequence`, {
      method: 'PUT',
      body: JSON.stringify(payload)
    });
  },

  async deleteSequence(songId) {
    return apiRequest(`/songs/${songId}/sequence`, {
      method: 'DELETE'
    });
  },

  async generateSequenceFromFft(songId, timingMs = 50, activateCustom = true) {
    return apiRequest(`/songs/${songId}/sequence/generate-fft`, {
      method: 'POST',
      body: JSON.stringify({
        timing_ms: timingMs,
        activate_custom: activateCustom
      })
    });
  }
};

// =============================================================================
// Playlists API
// =============================================================================

export const playlists = {
  async list() {
    return apiRequest('/playlists');
  },

  async get(playlistId) {
    return apiRequest(`/playlists/${playlistId}`);
  },

  async create({ name, songIds = [], isActive = false }) {
    return apiRequest('/playlists', {
      method: 'POST',
      body: JSON.stringify({
        name,
        song_ids: songIds,
        is_active: isActive
      })
    });
  },

  async update(playlistId, { name, songIds, isActive }) {
    const body = {};
    if (name !== undefined) body.name = name;
    if (songIds !== undefined) body.song_ids = songIds;
    if (isActive !== undefined) body.is_active = isActive;

    return apiRequest(`/playlists/${playlistId}`, {
      method: 'PATCH',
      body: JSON.stringify(body)
    });
  },

  async activate(playlistId) {
    return apiRequest(`/playlists/${playlistId}/activate`, {
      method: 'POST'
    });
  },

  async addSong(playlistId, songId) {
    return apiRequest(`/playlists/${playlistId}/songs`, {
      method: 'POST',
      body: JSON.stringify({ song_id: songId })
    });
  },

  async removeSong(playlistId, songId) {
    return apiRequest(`/playlists/${playlistId}/songs/${songId}`, {
      method: 'DELETE'
    });
  },

  async reorderSongs(playlistId, songIds) {
    return apiRequest(`/playlists/${playlistId}/reorder`, {
      method: 'PUT',
      body: JSON.stringify({ song_ids: songIds })
    });
  },

  async delete(playlistId) {
    return apiRequest(`/playlists/${playlistId}`, {
      method: 'DELETE'
    });
  }
};

// =============================================================================
// Schedule API
// =============================================================================

export const schedules = {
  async list(enabledOnly = false) {
    const params = enabledOnly ? '?enabled_only=true' : '';
    return apiRequest(`/schedules${params}`);
  },

  async get(id) {
    return apiRequest(`/schedules/${id}`);
  },

  async create(schedule) {
    return apiRequest('/schedules', {
      method: 'POST',
      body: JSON.stringify(schedule)
    });
  },

  async update(id, schedule) {
    return apiRequest(`/schedules/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(schedule)
    });
  },

  async delete(id) {
    return apiRequest(`/schedules/${id}`, {
      method: 'DELETE'
    });
  },

  async getUpcoming(limit = 10) {
    return apiRequest(`/schedules/upcoming/events?limit=${limit}`);
  }
};

// =============================================================================
// Button Manager API
// =============================================================================

export const buttons = {
  async getStatus() {
    return apiRequest('/buttons/status');
  },

  async getHealth() {
    return apiRequest('/buttons/health');
  },

  async skip() {
    return apiRequest('/buttons/skip', {
      method: 'POST'
    });
  },

  async repeatToggle() {
    return apiRequest('/buttons/repeat/toggle', {
      method: 'POST'
    });
  },

  async audioToggle() {
    return apiRequest('/buttons/audio/toggle', {
      method: 'POST'
    });
  }
};

// =============================================================================
// System API
// =============================================================================

export const system = {
  async getHealth() {
    return apiRequest('/health', { skipAuth: true });
  }
};

export { ApiError };
