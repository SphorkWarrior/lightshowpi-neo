import { useState, useEffect, useRef } from 'react';
import { songs as songsApi, playlists as playlistsApi, lightshow } from '../services/api';
import '../styles/PlaylistSongManager.css';

export default function PlaylistSongManager({
  onBack,
  onLogout,
  onOpenSequenceEditor,
  initialTab = 'songs'
}) {
  const [activeTab, setActiveTab] = useState(initialTab); // 'songs' | 'playlists'
  const [songsList, setSongsList] = useState([]);
  const [playlistsList, setPlaylistsList] = useState([]);
  const [selectedPlaylistId, setSelectedPlaylistId] = useState(null);
  const [selectedPlaylist, setSelectedPlaylist] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [searchQuery, setSearchQuery] = useState('');

  // Upload state
  const [uploadFile, setUploadFile] = useState(null);
  const [uploadTitle, setUploadTitle] = useState('');
  const [uploadArtist, setUploadArtist] = useState('');
  const [uploadPlaylistId, setUploadPlaylistId] = useState('');
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef(null);

  // New playlist state
  const [newPlaylistName, setNewPlaylistName] = useState('');
  const [newPlaylistDesc, setNewPlaylistDesc] = useState('');
  const [creatingPlaylist, setCreatingPlaylist] = useState(false);
  const [songToAddId, setSongToAddId] = useState('');

  useEffect(() => {
    loadAll(false);
  }, []);

  useEffect(() => {
    if (selectedPlaylistId) {
      loadPlaylistDetail(selectedPlaylistId);
    } else {
      setSelectedPlaylist(null);
    }
  }, [selectedPlaylistId]);

  const showNotice = (msg) => {
    setNotice(msg);
    setTimeout(() => {
      setNotice((prev) => (prev === msg ? '' : prev));
    }, 4000);
  };

  const loadAll = async (resync = false) => {
    setLoading(true);
    setError('');
    try {
      const [fetchedSongs, fetchedPlaylists] = await Promise.all([
        songsApi.list(resync),
        playlistsApi.list()
      ]);
      setSongsList(fetchedSongs);
      setPlaylistsList(fetchedPlaylists);

      if (fetchedPlaylists.length > 0) {
        const activePl = fetchedPlaylists.find((p) => p.is_active || p.active) || fetchedPlaylists[0];
        const targetId = selectedPlaylistId && fetchedPlaylists.some((p) => p.id === selectedPlaylistId)
          ? selectedPlaylistId
          : activePl.id;
        setSelectedPlaylistId(targetId);
        if (!uploadPlaylistId) {
          setUploadPlaylistId(String(targetId));
        }
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const loadPlaylistDetail = async (playlistId) => {
    try {
      const detail = await playlistsApi.get(playlistId);
      setSelectedPlaylist(detail);
    } catch (err) {
      setError(err.message);
    }
  };

  const handleFileChange = (e) => {
    const file = e.target.files?.[0] || null;
    setUploadFile(file);
    if (file && !uploadTitle) {
      const stem = file.name.replace(/\.[^/.]+$/, '').replace(/[_-]+/g, ' ');
      setUploadTitle(stem);
    }
  };

  const handleUploadSubmit = async (e) => {
    e.preventDefault();
    if (!uploadFile) {
      setError('Please select an audio file to upload.');
      return;
    }
    setUploading(true);
    setError('');
    try {
      const targetPlaylist = uploadPlaylistId ? parseInt(uploadPlaylistId, 10) : null;
      const uploaded = await songsApi.upload(
        uploadFile,
        uploadTitle.trim() || null,
        uploadArtist.trim() || null,
        targetPlaylist
      );
      setUploadFile(null);
      setUploadTitle('');
      setUploadArtist('');
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
      showNotice(`Uploaded "${uploaded.title}" successfully!`);
      await loadAll(false);
      if (selectedPlaylistId) {
        await loadPlaylistDetail(selectedPlaylistId);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  };

  const handleToggleSequenceMode = async (song, targetMode) => {
    const newMode = targetMode || (song.sequence_mode === 'custom' ? 'fft' : 'custom');
    setError('');
    try {
      const updated = await songsApi.update(song.id, { sequence_mode: newMode });
      setSongsList((prev) => prev.map((s) => (s.id === song.id ? updated : s)));
      if (selectedPlaylist) {
        setSelectedPlaylist((prev) => ({
          ...prev,
          songs: prev.songs.map((ps) => {
            const sObj = ps.song || ps;
            if (sObj.id !== song.id) return ps;
            return ps.song ? { ...ps, song: updated } : updated;
          })
        }));
      }
      showNotice(
        `"${updated.title}" set to ${
          newMode === 'custom' ? '✨ Custom Sequence' : '🎵 Real-Time FFT'
        } mode.`
      );
    } catch (err) {
      setError(err.message);
    }
  };

  const handlePlaySong = async (song) => {
    setError('');
    try {
      await lightshow.start(true, 'playlist', null, song.id);
      showNotice(`Playing "${song.title}" (${song.sequence_mode === 'custom' ? 'Custom Sequence' : 'FFT'})...`);
    } catch (err) {
      setError(err.message);
    }
  };

  const handleDeleteSong = async (song) => {
    if (!confirm(`Delete "${song.title}" (${song.filename}) and its associated files?`)) {
      return;
    }
    setError('');
    try {
      await songsApi.delete(song.id, true);
      showNotice(`Deleted "${song.title}".`);
      await loadAll(false);
      if (selectedPlaylistId) {
        await loadPlaylistDetail(selectedPlaylistId);
      }
    } catch (err) {
      setError(err.message);
    }
  };

  const handleCreatePlaylist = async (e) => {
    e.preventDefault();
    if (!newPlaylistName.trim()) return;
    setCreatingPlaylist(true);
    setError('');
    try {
      const created = await playlistsApi.create({
        name: newPlaylistName.trim(),
        description: newPlaylistDesc.trim() || null
      });
      setNewPlaylistName('');
      setNewPlaylistDesc('');
      showNotice(`Created playlist "${created.name}".`);
      const updatedPlaylists = await playlistsApi.list();
      setPlaylistsList(updatedPlaylists);
      setSelectedPlaylistId(created.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setCreatingPlaylist(false);
    }
  };

  const handleActivatePlaylist = async (playlist) => {
    setError('');
    try {
      await playlistsApi.activate(playlist.id);
      const updatedPlaylists = await playlistsApi.list();
      setPlaylistsList(updatedPlaylists);
      if (selectedPlaylistId === playlist.id) {
        await loadPlaylistDetail(playlist.id);
      }
      showNotice(`"${playlist.name}" is now the active playlist.`);
    } catch (err) {
      setError(err.message);
    }
  };

  const handlePlayPlaylist = async (playlist) => {
    setError('');
    try {
      await lightshow.start(true, 'playlist', playlist.id, null);
      const updatedPlaylists = await playlistsApi.list();
      setPlaylistsList(updatedPlaylists);
      showNotice(`Started playlist "${playlist.name}"!`);
    } catch (err) {
      setError(err.message);
    }
  };

  const handleDeletePlaylist = async (playlist) => {
    if (!confirm(`Delete playlist "${playlist.name}"? (Audio files will be kept in library)`)) {
      return;
    }
    setError('');
    try {
      await playlistsApi.delete(playlist.id);
      showNotice(`Deleted playlist "${playlist.name}".`);
      const updatedPlaylists = await playlistsApi.list();
      setPlaylistsList(updatedPlaylists);
      if (selectedPlaylistId === playlist.id) {
        setSelectedPlaylistId(updatedPlaylists[0]?.id || null);
      }
    } catch (err) {
      setError(err.message);
    }
  };

  const handleAddSongToPlaylist = async (songId, targetPlaylistId = selectedPlaylistId) => {
    if (!targetPlaylistId || !songId) return;
    setError('');
    try {
      const updatedDetail = await playlistsApi.addSong(targetPlaylistId, parseInt(songId, 10));
      if (targetPlaylistId === selectedPlaylistId) {
        setSelectedPlaylist(updatedDetail);
      }
      const updatedPlaylists = await playlistsApi.list();
      setPlaylistsList(updatedPlaylists);
      const addedSong = songsList.find((s) => s.id === parseInt(songId, 10));
      showNotice(`Added "${addedSong?.title || 'song'}" to "${updatedDetail.name}".`);
    } catch (err) {
      setError(err.message);
    }
  };

  const handleRemoveSongFromPlaylist = async (songId) => {
    if (!selectedPlaylistId) return;
    setError('');
    try {
      const updatedDetail = await playlistsApi.removeSong(selectedPlaylistId, songId);
      setSelectedPlaylist(updatedDetail);
      const updatedPlaylists = await playlistsApi.list();
      setPlaylistsList(updatedPlaylists);
    } catch (err) {
      setError(err.message);
    }
  };

  const handleMovePlaylistSong = async (index, direction) => {
    if (!selectedPlaylist || !selectedPlaylist.songs) return;
    const targetIndex = index + direction;
    if (targetIndex < 0 || targetIndex >= selectedPlaylist.songs.length) return;

    const currentSongs = [...selectedPlaylist.songs];
    const [moved] = currentSongs.splice(index, 1);
    currentSongs.splice(targetIndex, 0, moved);

    const orderedIds = currentSongs.map((item) => (item.song || item).id);
    try {
      const updatedDetail = await playlistsApi.reorderSongs(selectedPlaylist.id, orderedIds);
      setSelectedPlaylist(updatedDetail);
    } catch (err) {
      setError(err.message);
    }
  };

  const filteredSongs = songsList.filter((song) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      song.title.toLowerCase().includes(q) ||
      (song.artist && song.artist.toLowerCase().includes(q)) ||
      song.filename.toLowerCase().includes(q)
    );
  });

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <div className="header-brand">
          <h1>🎵 Songs &amp; Playlists</h1>
          <div className="sub-tabs">
            <button
              className={`sub-tab-btn ${activeTab === 'songs' ? 'active' : ''}`}
              onClick={() => setActiveTab('songs')}
            >
              🎶 Song Library &amp; Upload ({songsList.length})
            </button>
            <button
              className={`sub-tab-btn ${activeTab === 'playlists' ? 'active' : ''}`}
              onClick={() => setActiveTab('playlists')}
            >
              📋 Playlist Manager ({playlistsList.length})
            </button>
          </div>
        </div>
        <div className="header-actions">
          <button onClick={() => loadAll(true)} className="btn btn-secondary" title="Rescan music directory">
            🔄 Sync Disk
          </button>
          {onBack && (
            <button onClick={onBack} className="btn btn-secondary">
              ← Dashboard
            </button>
          )}
          <button onClick={onLogout} className="btn btn-secondary">
            Logout
          </button>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="notice-banner">{notice}</div>}

      <div className="media-manager-container">
        {activeTab === 'songs' ? (
          <div className="songs-tab-layout">
            {/* Upload Song Card */}
            <div className="card upload-card">
              <h2>⬆️ Upload New Song</h2>
              <form onSubmit={handleUploadSubmit} className="upload-form">
                <div className="form-group">
                  <label htmlFor="song-file">Audio File (.mp3, .wav, .flac, .ogg, .m4a)</label>
                  <input
                    id="song-file"
                    ref={fileInputRef}
                    type="file"
                    accept=".mp3,.wav,.flac,.ogg,.m4a,.aac,.wma,audio/*"
                    onChange={handleFileChange}
                    required
                  />
                </div>

                <div className="upload-form-row">
                  <div className="form-group">
                    <label htmlFor="song-title">Title (optional)</label>
                    <input
                      id="song-title"
                      type="text"
                      placeholder="Auto-detected from tag/filename"
                      value={uploadTitle}
                      onChange={(e) => setUploadTitle(e.target.value)}
                    />
                  </div>

                  <div className="form-group">
                    <label htmlFor="song-artist">Artist (optional)</label>
                    <input
                      id="song-artist"
                      type="text"
                      placeholder="Artist name"
                      value={uploadArtist}
                      onChange={(e) => setUploadArtist(e.target.value)}
                    />
                  </div>

                  <div className="form-group">
                    <label htmlFor="song-playlist">Add to Playlist</label>
                    <select
                      id="song-playlist"
                      value={uploadPlaylistId}
                      onChange={(e) => setUploadPlaylistId(e.target.value)}
                    >
                      <option value="">-- Library Only --</option>
                      {playlistsList.map((pl) => (
                        <option key={pl.id} value={pl.id}>
                          {pl.name} {pl.is_active || pl.active ? '(Active)' : ''}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={uploading || !uploadFile}
                >
                  {uploading ? '⏳ Uploading & Analyzing...' : '⬆️ Upload Song'}
                </button>
              </form>
            </div>

            {/* Song Library Table/List */}
            <div className="card library-card">
              <div className="card-header library-header">
                <h2>🎶 Song Library</h2>
                <div className="library-search">
                  <input
                    type="text"
                    placeholder="Search songs by title, artist, or filename..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />
                </div>
              </div>

              {loading ? (
                <div className="loading">Loading song library...</div>
              ) : filteredSongs.length === 0 ? (
                <div className="no-data">
                  No songs found. Upload an audio file above to get started!
                </div>
              ) : (
                <div className="songs-table-wrapper">
                  <table className="songs-table">
                    <thead>
                      <tr>
                        <th>Song</th>
                        <th>Duration</th>
                        <th>Light Control Mode (FFT vs Custom)</th>
                        <th>Custom Sequence</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredSongs.map((song) => (
                        <tr key={song.id}>
                          <td>
                            <div className="song-primary-info">
                              <span className="song-title">{song.title}</span>
                              <span className="song-meta">
                                {song.artist ? `${song.artist} • ` : ''}
                                <code>{song.filename}</code>
                              </span>
                            </div>
                          </td>
                          <td className="song-duration">
                            {formatDuration(song.duration)}
                          </td>
                          <td>
                            <div className="mode-toggle-pill" role="group" aria-label="Light sequence mode">
                              <button
                                type="button"
                                className={`mode-pill-btn ${
                                  song.sequence_mode !== 'custom' ? 'active-fft' : ''
                                }`}
                                onClick={() => handleToggleSequenceMode(song, 'fft')}
                                title="Use real-time FFT audio frequency analysis"
                              >
                                🎵 FFT
                              </button>
                              <button
                                type="button"
                                className={`mode-pill-btn ${
                                  song.sequence_mode === 'custom' ? 'active-custom' : ''
                                }`}
                                onClick={() => handleToggleSequenceMode(song, 'custom')}
                                title="Use user-defined custom light sequence"
                              >
                                ✨ Custom
                              </button>
                            </div>
                          </td>
                          <td>
                            <div className="sequence-status-cell">
                              {song.has_custom_sequence ? (
                                <span className="seq-badge seq-ready">
                                  ✓ Ready ({song.sequence_timing}ms)
                                </span>
                              ) : (
                                <span className="seq-badge seq-none">
                                  None
                                </span>
                              )}
                              {song.sequence_mode === 'custom' && !song.has_custom_sequence && (
                                <span className="seq-warning" title="No custom sequence saved yet; will fall back to FFT until created">
                                  ⚠ Create in Studio
                                </span>
                              )}
                            </div>
                          </td>
                          <td>
                            <div className="song-row-actions">
                              <button
                                type="button"
                                className="btn btn-sm btn-primary"
                                onClick={() => onOpenSequenceEditor(song)}
                                title="Open Custom Light Sequence Studio for this song"
                              >
                                ✨ Sequence Studio
                              </button>
                              <button
                                type="button"
                                className="btn btn-sm btn-success"
                                onClick={() => handlePlaySong(song)}
                                title="Play this song immediately on the lights"
                              >
                                ▶ Play
                              </button>
                              {selectedPlaylistId && (
                                <button
                                  type="button"
                                  className="btn btn-sm btn-secondary"
                                  onClick={() => handleAddSongToPlaylist(song.id, selectedPlaylistId)}
                                  title="Add to selected playlist"
                                >
                                  + Playlist
                                </button>
                              )}
                              <button
                                type="button"
                                className="btn btn-sm btn-danger"
                                onClick={() => handleDeleteSong(song)}
                                title="Delete song"
                              >
                                🗑
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        ) : (
          /* Playlist Manager Tab */
          <div className="playlists-tab-layout">
            {/* Sidebar: List of Playlists + Create New */}
            <div className="card playlists-sidebar-card">
              <h2>📋 Playlists</h2>
              <div className="playlists-list">
                {playlistsList.map((pl) => (
                  <div
                    key={pl.id}
                    className={`playlist-list-item ${
                      selectedPlaylistId === pl.id ? 'selected' : ''
                    }`}
                    onClick={() => setSelectedPlaylistId(pl.id)}
                  >
                    <div className="playlist-item-main">
                      <div className="playlist-item-title">
                        <span>{pl.name}</span>
                        {(pl.is_active || pl.active) && <span className="active-pl-badge">★ Active</span>}
                      </div>
                      <div className="playlist-item-sub">
                        {pl.song_count} {pl.song_count === 1 ? 'song' : 'songs'}
                        {pl.description ? ` • ${pl.description}` : ''}
                      </div>
                    </div>
                    <div
                      className="playlist-item-actions"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        type="button"
                        className="btn btn-sm btn-success"
                        onClick={() => handlePlayPlaylist(pl)}
                        title="Start playing this playlist now"
                      >
                        ▶
                      </button>
                      {!(pl.is_active || pl.active) && (
                        <button
                          type="button"
                          className="btn btn-sm btn-secondary"
                          onClick={() => handleActivatePlaylist(pl)}
                          title="Set as default active playlist"
                        >
                          Set Active
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn btn-sm btn-danger"
                        onClick={() => handleDeletePlaylist(pl)}
                        title="Delete playlist"
                      >
                        🗑
                      </button>
                    </div>
                  </div>
                ))}
              </div>

              <form onSubmit={handleCreatePlaylist} className="create-playlist-form">
                <h3>+ Create New Playlist</h3>
                <div className="form-group">
                  <input
                    type="text"
                    placeholder="Playlist Name (e.g. Holiday Favorites)"
                    value={newPlaylistName}
                    onChange={(e) => setNewPlaylistName(e.target.value)}
                    required
                  />
                </div>
                <div className="form-group">
                  <input
                    type="text"
                    placeholder="Description (optional)"
                    value={newPlaylistDesc}
                    onChange={(e) => setNewPlaylistDesc(e.target.value)}
                  />
                </div>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={creatingPlaylist || !newPlaylistName.trim()}
                >
                  {creatingPlaylist ? 'Creating...' : '+ Create Playlist'}
                </button>
              </form>
            </div>

            {/* Selected Playlist Detail & Song Ordering */}
            <div className="card playlist-detail-card">
              {!selectedPlaylist ? (
                <div className="no-data">Select or create a playlist to manage its songs.</div>
              ) : (
                <>
                  <div className="card-header playlist-detail-header">
                    <div>
                      <h2>
                        {selectedPlaylist.name}{' '}
                        {(selectedPlaylist.is_active || selectedPlaylist.active) && (
                          <span className="active-pl-badge">★ Active Playlist</span>
                        )}
                      </h2>
                      {selectedPlaylist.description && (
                        <p className="playlist-desc">{selectedPlaylist.description}</p>
                      )}
                    </div>
                    <div className="playlist-header-buttons">
                      {!(selectedPlaylist.is_active || selectedPlaylist.active) && (
                        <button
                          type="button"
                          className="btn btn-secondary"
                          onClick={() => handleActivatePlaylist(selectedPlaylist)}
                        >
                          ★ Set as Active
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn btn-success"
                        onClick={() => handlePlayPlaylist(selectedPlaylist)}
                      >
                        ▶ Play Playlist
                      </button>
                    </div>
                  </div>

                  {/* Quick Add Song Bar */}
                  <div className="playlist-add-song-bar">
                    <select
                      value={songToAddId}
                      onChange={(e) => setSongToAddId(e.target.value)}
                    >
                      <option value="">-- Select a song from library to add --</option>
                      {songsList.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.title} ({formatDuration(s.duration)}) — [{s.sequence_mode.toUpperCase()}]
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={!songToAddId}
                      onClick={() => {
                        handleAddSongToPlaylist(songToAddId, selectedPlaylist.id);
                        setSongToAddId('');
                      }}
                    >
                      + Add Song
                    </button>
                  </div>

                  {selectedPlaylist.songs.length === 0 ? (
                    <div className="no-data">
                      This playlist is empty. Add songs using the dropdown above or from the Song Library tab.
                    </div>
                  ) : (
                    <div className="playlist-songs-list">
                      {selectedPlaylist.songs.map((entry, idx) => {
                        const song = entry.song || entry;
                        return (
                          <div key={`${song.id}-${idx}`} className="playlist-song-row">
                            <div className="playlist-order-controls">
                              <span className="playlist-pos-num">{idx + 1}</span>
                              <div className="order-arrows">
                                <button
                                  type="button"
                                  className="order-btn"
                                  disabled={idx === 0}
                                  onClick={() => handleMovePlaylistSong(idx, -1)}
                                  title="Move Up"
                                >
                                  ▲
                                </button>
                                <button
                                  type="button"
                                  className="order-btn"
                                  disabled={idx === selectedPlaylist.songs.length - 1}
                                  onClick={() => handleMovePlaylistSong(idx, 1)}
                                  title="Move Down"
                                >
                                  ▼
                                </button>
                              </div>
                            </div>

                            <div className="playlist-song-info">
                              <div className="song-title">{song.title}</div>
                              <div className="song-meta">
                                {formatDuration(song.duration)} • <code>{song.filename}</code>
                                {song.has_custom_sequence && (
                                  <span className="seq-badge seq-ready ml-sm">
                                    ✓ Custom Seq ({song.sequence_timing}ms)
                                  </span>
                                )}
                              </div>
                            </div>

                            {/* Per-Song FFT vs Custom Sequence Toggle inside Playlist */}
                            <div className="playlist-song-mode">
                              <div className="mode-toggle-pill">
                                <button
                                  type="button"
                                  className={`mode-pill-btn ${
                                    song.sequence_mode !== 'custom' ? 'active-fft' : ''
                                  }`}
                                  onClick={() => handleToggleSequenceMode(song, 'fft')}
                                >
                                  🎵 FFT
                                </button>
                                <button
                                  type="button"
                                  className={`mode-pill-btn ${
                                    song.sequence_mode === 'custom' ? 'active-custom' : ''
                                  }`}
                                  onClick={() => handleToggleSequenceMode(song, 'custom')}
                                >
                                  ✨ Custom
                                </button>
                              </div>
                            </div>

                            <div className="playlist-song-actions">
                              <button
                                type="button"
                                className="btn btn-sm btn-primary"
                                onClick={() => onOpenSequenceEditor(song)}
                                title="Edit Custom Light Sequence"
                              >
                                ✨ Sequence
                              </button>
                              <button
                                type="button"
                                className="btn btn-sm btn-success"
                                onClick={() => handlePlaySong(song)}
                                title="Play Song Now"
                              >
                                ▶
                              </button>
                              <button
                                type="button"
                                className="btn btn-sm btn-danger"
                                onClick={() => handleRemoveSongFromPlaylist(song.id)}
                                title="Remove from Playlist"
                              >
                                ✕
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function formatDuration(seconds) {
  if (!seconds && seconds !== 0) return '--:--';
  const totalSecs = Math.round(seconds);
  const mins = Math.floor(totalSecs / 60);
  const secs = totalSecs % 60;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}
