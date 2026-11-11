import { useState, useEffect } from 'react';
import {
  lightshow,
  schedules as schedulesApi,
  buttons,
  playlists as playlistsApi,
  songs as songsApi
} from '../services/api';
import ScheduleManager from './ScheduleManager';
import PlaylistSongManager from './PlaylistSongManager';
import SequenceEditor from './SequenceEditor';
import '../styles/Dashboard.css';

export default function Dashboard({ onLogout }) {
  const [status, setStatus] = useState(null);
  const [upcomingEvents, setUpcomingEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeView, setActiveView] = useState('dashboard'); // 'dashboard' | 'songs' | 'playlists' | 'sequence' | 'schedules'
  const [editorSong, setEditorSong] = useState(null);
  const [buttonStatus, setButtonStatus] = useState(null);
  const [buttonHealth, setButtonHealth] = useState(null);
  const [selectedMode, setSelectedMode] = useState('playlist');
  const [playlistsList, setPlaylistsList] = useState([]);
  const [selectedPlaylistId, setSelectedPlaylistId] = useState('');
  const [songsSummary, setSongsSummary] = useState([]);

  // Poll status efficiently (pauses when tab is hidden or when in another view)
  useEffect(() => {
    if (activeView !== 'dashboard') return;

    fetchStatus();
    fetchUpcomingEvents();
    fetchButtonStatus();
    fetchButtonHealth();
    fetchMediaSummary();

    let tickCount = 0;
    const interval = setInterval(() => {
      if (document.hidden) return;
      tickCount += 1;
      fetchStatus();
      fetchButtonStatus();
      // Poll less frequently for static/slow-changing data to save Pi Zero 2 W CPU
      if (tickCount % 5 === 0) {
        fetchUpcomingEvents();
        fetchButtonHealth();
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [activeView]);

  const fetchStatus = async () => {
    try {
      const data = await lightshow.getStatus();
      setStatus(data);
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const fetchMediaSummary = async () => {
    try {
      const [pls, sgs] = await Promise.all([
        playlistsApi.list(),
        songsApi.list(false)
      ]);
      setPlaylistsList(pls);
      setSongsSummary(sgs);
      const activePl = pls.find((p) => p.is_active || p.active) || pls[0];
      if (activePl && !selectedPlaylistId) {
        setSelectedPlaylistId(String(activePl.id));
      }
    } catch (err) {
      console.error('Failed to fetch media summary:', err);
    }
  };

  const fetchUpcomingEvents = async () => {
    try {
      const events = await schedulesApi.getUpcoming(5);
      setUpcomingEvents(events);
    } catch (err) {
      console.error('Failed to fetch upcoming events:', err);
    }
  };

  const fetchButtonStatus = async () => {
    try {
      const data = await buttons.getStatus();
      setButtonStatus(data);
    } catch (err) {
      console.error('Failed to fetch button status:', err);
    }
  };

  const fetchButtonHealth = async () => {
    try {
      const data = await buttons.getHealth();
      setButtonHealth(data);
    } catch (err) {
      console.error('Failed to fetch button health:', err);
    }
  };

  const handleStart = async () => {
    try {
      const plId =
        selectedMode === 'playlist' && selectedPlaylistId
          ? parseInt(selectedPlaylistId, 10)
          : null;
      await lightshow.start(true, selectedMode, plId, null);
      await fetchStatus();
      await fetchMediaSummary();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleStop = async () => {
    try {
      await lightshow.stop('pause');
      await fetchStatus();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleSkip = async () => {
    try {
      await lightshow.skip();
      await fetchStatus();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleButtonSkip = async () => {
    try {
      await buttons.skip();
      await fetchButtonStatus();
      await fetchStatus();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleRepeatToggle = async () => {
    try {
      await buttons.repeatToggle();
      await fetchButtonStatus();
      await fetchStatus();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleAudioToggle = async () => {
    try {
      await buttons.audioToggle();
      await fetchButtonStatus();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleOpenSequenceEditor = (song = null) => {
    setEditorSong(song || songsSummary[0] || null);
    setActiveView('sequence');
  };

  if (loading) {
    return <div className="dashboard-loading">Loading...</div>;
  }

  if (activeView === 'schedules') {
    return (
      <ScheduleManager
        onBack={() => setActiveView('dashboard')}
        onLogout={onLogout}
      />
    );
  }

  if (activeView === 'songs' || activeView === 'playlists') {
    return (
      <PlaylistSongManager
        initialTab={activeView === 'playlists' ? 'playlists' : 'songs'}
        onBack={() => setActiveView('dashboard')}
        onLogout={onLogout}
        onOpenSequenceEditor={(song) => handleOpenSequenceEditor(song)}
      />
    );
  }

  if (activeView === 'sequence') {
    return (
      <SequenceEditor
        initialSong={editorSong}
        onBack={() => setActiveView('songs')}
        onLogout={onLogout}
        onSongUpdated={() => fetchMediaSummary()}
      />
    );
  }

  const customSongsCount = songsSummary.filter((s) => s.sequence_mode === 'custom').length;
  const activePlaylistObj = playlistsList.find((p) => p.is_active || p.active);

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <div className="header-nav-left">
          <h1>🎄 LightShowPi Neo</h1>
          <nav className="main-nav">
            <button
              type="button"
              className="nav-pill active"
              onClick={() => setActiveView('dashboard')}
            >
              🎛️ Dashboard
            </button>
            <button
              type="button"
              className="nav-pill"
              onClick={() => setActiveView('songs')}
            >
              🎵 Songs &amp; Upload
            </button>
            <button
              type="button"
              className="nav-pill"
              onClick={() => setActiveView('playlists')}
            >
              📋 Playlists
            </button>
            <button
              type="button"
              className="nav-pill"
              onClick={() => handleOpenSequenceEditor(null)}
            >
              ✨ Sequence Studio
            </button>
            <button
              type="button"
              className="nav-pill"
              onClick={() => setActiveView('schedules')}
            >
              📅 Schedules
            </button>
          </nav>
        </div>
        <button onClick={onLogout} className="btn btn-secondary">
          Logout
        </button>
      </header>

      {error && <div className="error-banner">{error}</div>}
      {buttonHealth && !buttonHealth.healthy && (
        <div className="warning-banner">
          ⚠ {buttonHealth.warning}
        </div>
      )}

      <div className="dashboard-content">
        {/* Status Panel */}
        <div className="card status-card">
          <h2>Status</h2>
          <div className="status-grid">
            <div className="status-item">
              <span className="label">State:</span>
              <span className={`status-badge status-${status?.state || 'idle'}`}>
                {status?.state || 'Unknown'}
              </span>
            </div>

            {status?.current_song && (
              <div className="status-item">
                <span className="label">Current Song:</span>
                <span className="value">{status.current_song}</span>
              </div>
            )}

            {status?.elapsed_time !== null && status?.total_time && (
              <div className="status-item">
                <span className="label">Progress:</span>
                <span className="value">
                  {formatTime(status.elapsed_time)} / {formatTime(status.total_time)}
                </span>
              </div>
            )}

            <div className="status-item">
              <span className="label">Active Playlist:</span>
              <span className="value">
                {activePlaylistObj
                  ? `${activePlaylistObj.name} (${activePlaylistObj.song_count} songs)`
                  : 'Default'}
              </span>
            </div>

            <div className="status-item">
              <span className="label">Schedule:</span>
              <span className="value">
                {status?.schedule_enabled ? '✓ Enabled' : '✗ Disabled'}
              </span>
            </div>
          </div>
        </div>

        {/* Controls */}
        <div className="card controls-card">
          <h2>Controls</h2>
          <div className="mode-selector">
            <label htmlFor="mode-select">Mode:</label>
            <select
              id="mode-select"
              value={selectedMode}
              onChange={(e) => setSelectedMode(e.target.value)}
              disabled={status?.state === 'playing'}
              className="mode-select"
            >
              <option value="playlist">Playlist</option>
              <option value="ambient">Ambient</option>
              <option value="audio-in">Audio In</option>
              <option value="stream-in">Stream In</option>
            </select>
          </div>

          {selectedMode === 'playlist' && playlistsList.length > 0 && (
            <div className="mode-selector">
              <label htmlFor="playlist-select">Playlist:</label>
              <select
                id="playlist-select"
                value={selectedPlaylistId}
                onChange={(e) => setSelectedPlaylistId(e.target.value)}
                disabled={status?.state === 'playing'}
                className="mode-select"
              >
                {playlistsList.map((pl) => (
                  <option key={pl.id} value={pl.id}>
                    {pl.name} ({pl.song_count} songs){pl.is_active || pl.active ? ' ★' : ''}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="controls-grid">
            <button
              onClick={handleStart}
              className="btn btn-success btn-large"
              disabled={status?.state === 'playing'}
            >
              ▶ Start
            </button>

            <button
              onClick={handleStop}
              className="btn btn-danger btn-large"
              disabled={status?.state !== 'playing'}
            >
              ■ Stop
            </button>

            <button
              onClick={handleSkip}
              className="btn btn-primary btn-large"
              disabled={status?.state !== 'playing'}
            >
              ⏭ Skip
            </button>

            <button
              onClick={() => setActiveView('schedules')}
              className="btn btn-secondary btn-large"
            >
              📅 Schedules
            </button>
          </div>
        </div>

        {/* Music, Playlists & Custom Light Sequence Quick Card */}
        <div className="card media-overview-card">
          <h2>🎵 Music &amp; Light Sequences</h2>
          <div className="status-grid">
            <div className="status-item">
              <span className="label">Library Songs:</span>
              <span className="value">{songsSummary.length} tracks</span>
            </div>
            <div className="status-item">
              <span className="label">Light Control Modes:</span>
              <span className="value">
                🎵 {songsSummary.length - customSongsCount} FFT • ✨ {customSongsCount} Custom
              </span>
            </div>
            <div className="status-item">
              <span className="label">Playlists:</span>
              <span className="value">{playlistsList.length} configured</span>
            </div>
          </div>
          <div className="controls-grid mt-md">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setActiveView('songs')}
            >
              ⬆️ Upload &amp; Songs
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setActiveView('playlists')}
            >
              📋 Manage Playlists
            </button>
            <button
              type="button"
              className="btn btn-secondary span-2"
              onClick={() => handleOpenSequenceEditor(null)}
            >
              ✨ Open Custom Sequence Studio
            </button>
          </div>
        </div>

        {/* Button Controls */}
        {buttonStatus && buttonStatus.enabled && (
          <div className="card button-controls-card">
            <h2>Button Controls</h2>
            <div className="button-status-grid">
              <div className="button-status-item">
                <span className="label">Repeat Mode:</span>
                <span className={`status-badge ${buttonStatus.repeat_mode ? 'status-active' : 'status-inactive'}`}>
                  {buttonStatus.repeat_mode ? '✓ ON' : '✗ OFF'}
                </span>
              </div>
              <div className="button-status-item">
                <span className="label">Audio Output:</span>
                <span className={`status-badge ${buttonStatus.audio_on ? 'status-active' : 'status-inactive'}`}>
                  {buttonStatus.audio_on ? '✓ ON' : '✗ OFF'}
                </span>
              </div>
              {buttonStatus.last_action && (
                <div className="button-status-item">
                  <span className="label">Last Action:</span>
                  <span className="value">{buttonStatus.last_action}</span>
                </div>
              )}
            </div>
            <div className="controls-grid">
              <button
                onClick={handleButtonSkip}
                className="btn btn-primary btn-large"
              >
                ⏭ Skip
              </button>

              <button
                onClick={handleRepeatToggle}
                className={`btn btn-large ${buttonStatus.repeat_mode ? 'btn-warning' : 'btn-success'}`}
              >
                🔁 Repeat {buttonStatus.repeat_mode ? 'OFF' : 'ON'}
              </button>

              <button
                onClick={handleAudioToggle}
                className={`btn btn-large ${buttonStatus.audio_on ? 'btn-danger' : 'btn-success'}`}
              >
                🔊 Audio {buttonStatus.audio_on ? 'OFF' : 'ON'}
              </button>
            </div>
          </div>
        )}

        {/* Upcoming Events */}
        <div className="card events-card">
          <h2>Upcoming Events</h2>
          {upcomingEvents.length === 0 ? (
            <p className="no-events">No scheduled events</p>
          ) : (
            <div className="events-list">
              {upcomingEvents.map((event, index) => (
                <div key={index} className="event-item">
                  <span className={`event-action event-${event.action}`}>
                    {event.action === 'start' ? '▶' : '■'} {event.action.toUpperCase()}
                  </span>
                  <span className="event-time">
                    {formatEventTime(event.time)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function formatTime(seconds) {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function formatEventTime(isoString) {
  const date = new Date(isoString);
  const now = new Date();
  const diff = date - now;

  // If within 24 hours, show relative time
  if (diff < 24 * 60 * 60 * 1000 && diff > 0) {
    const hours = Math.floor(diff / (60 * 60 * 1000));
    const minutes = Math.floor((diff % (60 * 60 * 1000)) / (60 * 1000));

    if (hours > 0) {
      return `in ${hours}h ${minutes}m`;
    } else {
      return `in ${minutes}m`;
    }
  }

  // Otherwise show time
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
