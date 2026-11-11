import { useState, useEffect, useRef, useCallback } from 'react';
import { songs as songsApi } from '../services/api';
import '../styles/SequenceEditor.css';

const CHANNEL_COLORS = [
  '#ff4757', // Ch 1 - Red
  '#ffa502', // Ch 2 - Orange
  '#ffd32a', // Ch 3 - Yellow
  '#2ed573', // Ch 4 - Green
  '#1e90ff', // Ch 5 - Blue
  '#5352ed', // Ch 6 - Indigo
  '#a55eea', // Ch 7 - Violet
  '#ff6b81'  // Ch 8 - Warm White / Pink
];

const HEADER_HEIGHT = 26;
const WAVEFORM_HEIGHT = 56;
const ROW_HEIGHT = 34;

export default function SequenceEditor({
  initialSong,
  onBack,
  onLogout,
  onSongUpdated
}) {
  const [songsList, setSongsList] = useState([]);
  const [currentSong, setCurrentSong] = useState(initialSong || null);
  const [timingMs, setTimingMs] = useState(initialSong?.sequence_timing || 50);
  const [channelsCount] = useState(8);
  const [frames, setFrames] = useState([]);
  const [waveformPeaks, setWaveformPeaks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [generatingFft, setGeneratingFft] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [dirty, setDirty] = useState(false);

  // Playback & Live Recording State
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1.0);
  const [currentTimeSec, setCurrentTimeSec] = useState(0);
  const [currentStep, setCurrentStep] = useState(0);
  const [liveRecord, setLiveRecord] = useState(false);
  const [followPlayhead, setFollowPlayhead] = useState(true);
  const [activeKeys, setActiveKeys] = useState({});

  // Painting & Range Selection State
  const [brushValue, setBrushValue] = useState(255);
  const [stepWidthPx, setStepWidthPx] = useState(8);
  const [selectionStart, setSelectionStart] = useState(null);
  const [selectionEnd, setSelectionEnd] = useState(null);
  const [patternSpeed, setPatternSpeed] = useState(4); // steps per beat/shift
  const [activateCustomOnSave, setActivateCustomOnSave] = useState(true);

  const audioRef = useRef(null);
  const canvasRef = useRef(null);
  const scrollContainerRef = useRef(null);
  const isPaintingRef = useRef(false);
  const isSelectingRangeRef = useRef(false);
  const framesRef = useRef(frames);
  const activeKeysRef = useRef(activeKeys);
  const liveRecordRef = useRef(liveRecord);
  const brushValueRef = useRef(brushValue);
  const timingMsRef = useRef(timingMs);

  framesRef.current = frames;
  activeKeysRef.current = activeKeys;
  liveRecordRef.current = liveRecord;
  brushValueRef.current = brushValue;
  timingMsRef.current = timingMs;

  const showNotice = (msg) => {
    setNotice(msg);
    setTimeout(() => {
      setNotice((prev) => (prev === msg ? '' : prev));
    }, 4000);
  };

  // Load song list if needed
  useEffect(() => {
    const init = async () => {
      try {
        const allSongs = await songsApi.list(false);
        setSongsList(allSongs);
        if (!currentSong && allSongs.length > 0) {
          setCurrentSong(allSongs[0]);
        }
      } catch (err) {
        setError(err.message);
      }
    };
    init();
  }, []);

  // Load sequence and waveform when currentSong changes
  useEffect(() => {
    if (!currentSong) {
      setLoading(false);
      return;
    }
    loadSongSequenceAndWaveform(currentSong.id);
  }, [currentSong?.id]);

  const loadSongSequenceAndWaveform = async (songId) => {
    setLoading(true);
    setError('');
    setIsPlaying(false);
    setCurrentTimeSec(0);
    setCurrentStep(0);
    setSelectionStart(null);
    setSelectionEnd(null);

    try {
      const [seqData, waveData] = await Promise.all([
        songsApi.getSequence(songId),
        songsApi.getWaveform(songId, 600).catch(() => ({ peaks: [] }))
      ]);

      const tMs = seqData.timing_ms || 50;
      const loadedSteps = seqData.steps || seqData.frames || [];
      let initialFrames = loadedSteps;
      if (initialFrames.length === 0) {
        const durSec = seqData.duration || currentSong?.duration || 60;
        const stepCount = Math.max(40, Math.ceil((durSec * 1000) / tMs));
        initialFrames = Array.from({ length: stepCount }, () =>
          new Array(channelsCount).fill(0)
        );
      }

      setTimingMs(tMs);
      setFrames(initialFrames);
      setWaveformPeaks(waveData.peaks || []);
      setDirty(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Synchronize audio playback and live tap recording via requestAnimationFrame
  useEffect(() => {
    let rafId = null;

    const tick = () => {
      const audio = audioRef.current;
      if (audio && !audio.paused) {
        const t = audio.currentTime || 0;
        const step = Math.min(
          framesRef.current.length - 1,
          Math.max(0, Math.floor((t * 1000) / timingMsRef.current))
        );
        setCurrentTimeSec(t);
        setCurrentStep(step);

        // If Live Tap Recording is active and any channel key/pad is held, write into current step
        const held = activeKeysRef.current;
        const heldChannels = Object.keys(held).filter((ch) => held[ch]);
        if (liveRecordRef.current && heldChannels.length > 0 && framesRef.current[step]) {
          const nextFrames = [...framesRef.current];
          const updatedRow = [...nextFrames[step]];
          let changed = false;
          for (const chStr of heldChannels) {
            const chIdx = parseInt(chStr, 10);
            if (updatedRow[chIdx] !== brushValueRef.current) {
              updatedRow[chIdx] = brushValueRef.current;
              changed = true;
            }
          }
          if (changed) {
            nextFrames[step] = updatedRow;
            framesRef.current = nextFrames;
            setFrames(nextFrames);
            setDirty(true);
          }
        }

        // Auto-scroll timeline to keep playhead visible
        if (followPlayhead && scrollContainerRef.current) {
          const container = scrollContainerRef.current;
          const playheadX = step * stepWidthPx;
          const viewLeft = container.scrollLeft;
          const viewWidth = container.clientWidth;
          if (playheadX > viewLeft + viewWidth * 0.8 || playheadX < viewLeft) {
            container.scrollLeft = Math.max(0, playheadX - viewWidth * 0.25);
          }
        }

        rafId = requestAnimationFrame(tick);
      }
    };

    if (isPlaying) {
      rafId = requestAnimationFrame(tick);
    }

    return () => {
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, [isPlaying, followPlayhead, stepWidthPx]);

  // Keyboard shortcuts: 1-8 for live channel pads, Space for play/pause
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target?.tagName)) return;

      if (e.code === 'Space') {
        e.preventDefault();
        togglePlayPause();
        return;
      }

      const keyNum = parseInt(e.key, 10);
      if (keyNum >= 1 && keyNum <= 8) {
        const chIdx = keyNum - 1;
        setActiveKeys((prev) => ({ ...prev, [chIdx]: true }));

        // Also stamp current step if paused and liveRecord is enabled
        if (liveRecordRef.current && framesRef.current[currentStep]) {
          const next = [...framesRef.current];
          const row = [...next[currentStep]];
          row[chIdx] = brushValueRef.current;
          next[currentStep] = row;
          setFrames(next);
          setDirty(true);
        }
      }
    };

    const handleKeyUp = (e) => {
      const keyNum = parseInt(e.key, 10);
      if (keyNum >= 1 && keyNum <= 8) {
        const chIdx = keyNum - 1;
        setActiveKeys((prev) => {
          const next = { ...prev };
          delete next[chIdx];
          return next;
        });
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('keyup', handleKeyUp);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('keyup', handleKeyUp);
    };
  }, [currentStep, isPlaying]);

  // Draw the waveform + 8-channel sequencer grid on Canvas
  const drawTimeline = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || frames.length === 0) return;

    const totalSteps = frames.length;
    const width = Math.max(600, totalSteps * stepWidthPx);
    const height = HEADER_HEIGHT + WAVEFORM_HEIGHT + channelsCount * ROW_HEIGHT;

    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;

    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#11161d';
    ctx.fillRect(0, 0, width, height);

    // 1. Draw Selection Range Highlight if active
    const selMin =
      selectionStart !== null && selectionEnd !== null
        ? Math.min(selectionStart, selectionEnd)
        : null;
    const selMax =
      selectionStart !== null && selectionEnd !== null
        ? Math.max(selectionStart, selectionEnd)
        : null;

    if (selMin !== null && selMax !== null) {
      ctx.fillStyle = 'rgba(52, 152, 219, 0.16)';
      ctx.fillRect(
        selMin * stepWidthPx,
        0,
        (selMax - selMin + 1) * stepWidthPx,
        height
      );
    }

    // 2. Draw Time Ruler Header
    ctx.fillStyle = '#1a222d';
    ctx.fillRect(0, 0, width, HEADER_HEIGHT);
    ctx.strokeStyle = '#2c3847';
    ctx.beginPath();
    ctx.moveTo(0, HEADER_HEIGHT);
    ctx.lineTo(width, HEADER_HEIGHT);
    ctx.stroke();

    const stepsPerSec = Math.max(1, Math.round(1000 / timingMs));
    ctx.fillStyle = '#95a5a6';
    ctx.font = '11px monospace';
    for (let step = 0; step < totalSteps; step += stepsPerSec) {
      const x = step * stepWidthPx;
      const secs = Math.round((step * timingMs) / 1000);
      ctx.strokeStyle = '#34495e';
      ctx.beginPath();
      ctx.moveTo(x, HEADER_HEIGHT - 6);
      ctx.lineTo(x, HEADER_HEIGHT);
      ctx.stroke();
      ctx.fillText(formatSecShort(secs), x + 3, 16);
    }

    // 3. Draw Audio Waveform Track
    const waveTop = HEADER_HEIGHT;
    const waveMid = waveTop + WAVEFORM_HEIGHT / 2;
    ctx.fillStyle = '#151c25';
    ctx.fillRect(0, waveTop, width, WAVEFORM_HEIGHT);

    if (waveformPeaks.length > 0) {
      ctx.fillStyle = 'rgba(46, 204, 113, 0.55)';
      const numPeaks = waveformPeaks.length;
      for (let i = 0; i < numPeaks; i++) {
        const x = (i / numPeaks) * width;
        const barW = Math.max(1.5, width / numPeaks);
        const amp = Math.max(0.04, waveformPeaks[i]) * (WAVEFORM_HEIGHT * 0.44);
        ctx.fillRect(x, waveMid - amp, barW, amp * 2);
      }
    }

    ctx.strokeStyle = '#2c3847';
    ctx.beginPath();
    ctx.moveTo(0, waveTop + WAVEFORM_HEIGHT);
    ctx.lineTo(width, waveTop + WAVEFORM_HEIGHT);
    ctx.stroke();

    // 4. Draw 8 Channel Rows & Cells
    const gridTop = HEADER_HEIGHT + WAVEFORM_HEIGHT;

    for (let ch = 0; ch < channelsCount; ch++) {
      const rowY = gridTop + ch * ROW_HEIGHT;
      const baseColor = CHANNEL_COLORS[ch % CHANNEL_COLORS.length];

      // Row background
      ctx.fillStyle = ch % 2 === 0 ? '#141a22' : '#11161d';
      ctx.fillRect(0, rowY, width, ROW_HEIGHT);

      // Active cells
      for (let step = 0; step < totalSteps; step++) {
        const val = frames[step]?.[ch] || 0;
        if (val > 0) {
          const alpha = 0.2 + (val / 255) * 0.8;
          ctx.fillStyle = hexToRgba(baseColor, alpha);
          const barHeight = Math.max(4, ((ROW_HEIGHT - 4) * val) / 255);
          ctx.fillRect(
            step * stepWidthPx + 1,
            rowY + ROW_HEIGHT - 2 - barHeight,
             Math.max(1, stepWidthPx - 1),
            barHeight
          );
        }
      }

      // Row bottom line
      ctx.strokeStyle = '#232d3b';
      ctx.beginPath();
      ctx.moveTo(0, rowY + ROW_HEIGHT);
      ctx.lineTo(width, rowY + ROW_HEIGHT);
      ctx.stroke();
    }

    // Beat / second vertical grid lines
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.06)';
    ctx.beginPath();
    for (let step = 0; step < totalSteps; step += stepsPerSec) {
      const x = step * stepWidthPx;
      ctx.moveTo(x, gridTop);
      ctx.lineTo(x, height);
    }
    ctx.stroke();

    // 5. Draw Playhead Cursor
    const playheadX = currentStep * stepWidthPx;
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(playheadX, 0);
    ctx.lineTo(playheadX, height);
    ctx.stroke();
    ctx.lineWidth = 1;

    // Playhead cap triangle
    ctx.fillStyle = '#e74c3c';
    ctx.beginPath();
    ctx.moveTo(playheadX - 6, 0);
    ctx.lineTo(playheadX + 6, 0);
    ctx.lineTo(playheadX, 10);
    ctx.closePath();
    ctx.fill();
  }, [
    frames,
    stepWidthPx,
    channelsCount,
    timingMs,
    waveformPeaks,
    currentStep,
    selectionStart,
    selectionEnd
  ]);

  useEffect(() => {
    drawTimeline();
  }, [drawTimeline]);

  // Canvas Mouse Interaction: Painting & Seeking/Range Selection
  const getCanvasCoords = (e) => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const step = Math.min(
      frames.length - 1,
      Math.max(0, Math.floor(x / stepWidthPx))
    );
    const gridTop = HEADER_HEIGHT + WAVEFORM_HEIGHT;
    const channel =
      y >= gridTop
        ? Math.min(channelsCount - 1, Math.max(0, Math.floor((y - gridTop) / ROW_HEIGHT)))
        : -1;
    return { x, y, step, channel };
  };

  const paintStepChannel = (step, channel, value) => {
    if (channel < 0 || channel >= channelsCount || step < 0 || step >= frames.length) {
      return;
    }
    setFrames((prev) => {
      if (prev[step]?.[channel] === value) return prev;
      const next = [...prev];
      const row = [...next[step]];
      row[channel] = value;
      next[step] = row;
      return next;
    });
    setDirty(true);
  };

  const handleCanvasMouseDown = (e) => {
    const coords = getCanvasCoords(e);
    if (!coords) return;

    if (coords.channel === -1) {
      // Clicked in Time Ruler or Waveform: seek audio and start range selection
      isSelectingRangeRef.current = true;
      setSelectionStart(coords.step);
      setSelectionEnd(coords.step);
      seekToStep(coords.step);
    } else {
      // Clicked in a Channel Row: paint cell
      isPaintingRef.current = true;
      const valToPaint = e.button === 2 || e.altKey ? 0 : brushValue;
      paintStepChannel(coords.step, coords.channel, valToPaint);
    }
  };

  const handleCanvasMouseMove = (e) => {
    if (!isPaintingRef.current && !isSelectingRangeRef.current) return;
    const coords = getCanvasCoords(e);
    if (!coords) return;

    if (isSelectingRangeRef.current) {
      setSelectionEnd(coords.step);
    } else if (isPaintingRef.current && coords.channel >= 0) {
      const valToPaint = e.buttons === 2 || e.altKey ? 0 : brushValue;
      paintStepChannel(coords.step, coords.channel, valToPaint);
    }
  };

  const handleCanvasMouseUp = () => {
    isPaintingRef.current = false;
    isSelectingRangeRef.current = false;
  };

  const seekToStep = (step) => {
    setCurrentStep(step);
    const timeSec = (step * timingMs) / 1000;
    setCurrentTimeSec(timeSec);
    if (audioRef.current) {
      audioRef.current.currentTime = timeSec;
    }
  };

  const togglePlayPause = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      audio.playbackRate = playbackRate;
      audio.play().then(() => setIsPlaying(true)).catch((err) => {
        setError(`Audio playback error: ${err.message}`);
      });
    } else {
      audio.pause();
      setIsPlaying(false);
    }
  };

  const handleStopAudio = () => {
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.currentTime = 0;
    }
    setIsPlaying(false);
    setCurrentStep(0);
    setCurrentTimeSec(0);
  };

  const handleTimingChange = (newTiming) => {
    const parsed = parseInt(newTiming, 10);
    if (!parsed || parsed === timingMs) return;

    // Resample existing frames to preserve the sequence across the song duration
    const durationMs = frames.length * timingMs;
    const newStepCount = Math.max(1, Math.ceil(durationMs / parsed));
    const resampled = [];
    for (let i = 0; i < newStepCount; i++) {
      const oldIdx = Math.min(
        frames.length - 1,
        Math.floor((i * parsed) / timingMs)
      );
      resampled.push([...(frames[oldIdx] || new Array(channelsCount).fill(0))]);
    }
    setTimingMs(parsed);
    setFrames(resampled);
    setCurrentStep(0);
    setSelectionStart(null);
    setSelectionEnd(null);
    setDirty(true);
  };

  // Save sequence to backend
  const handleSaveSequence = async () => {
    if (!currentSong) return;
    setSaving(true);
    setError('');
    try {
      await songsApi.saveSequence(
        currentSong.id,
        {
          timing_ms: timingMs,
          channels: channelsCount,
          frames
        },
        activateCustomOnSave
      );
      const updatedSong = await songsApi.get(currentSong.id);
      setCurrentSong(updatedSong);
      setSongsList((prev) => prev.map((s) => (s.id === updatedSong.id ? updatedSong : s)));
      if (onSongUpdated) onSongUpdated(updatedSong);
      setDirty(false);
      showNotice(
        `Saved custom sequence for "${updatedSong.title}" (${frames.length} steps @ ${timingMs}ms)!`
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  // Auto-generate from song's real FFT analysis
  const handleGenerateFromFft = async () => {
    if (!currentSong) return;
    if (
      dirty &&
      !confirm('Replace current unsaved grid with FFT-generated sequence from the audio file?')
    ) {
      return;
    }
    setGeneratingFft(true);
    setError('');
    try {
      const seqData = await songsApi.generateSequenceFromFft(
        currentSong.id,
        timingMs,
        true
      );
      const generatedFrames = seqData.steps || seqData.frames || [];
      setFrames(generatedFrames);
      setDirty(false);
      const updatedSong = await songsApi.get(currentSong.id);
      setCurrentSong(updatedSong);
      setSongsList((prev) => prev.map((s) => (s.id === updatedSong.id ? updatedSong : s)));
      showNotice(
        `Generated ${generatedFrames.length} steps from audio FFT and saved custom sequence!`
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setGeneratingFft(false);
    }
  };

  // Toggle FFT vs Custom Sequence Mode for current song
  const handleModeSwitch = async (mode) => {
    if (!currentSong) return;
    setError('');
    try {
      const updated = await songsApi.update(currentSong.id, { sequence_mode: mode });
      setCurrentSong(updated);
      setSongsList((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
      if (onSongUpdated) onSongUpdated(updated);
      showNotice(
        `Set "${updated.title}" mode to ${
          mode === 'custom' ? '✨ Custom Sequence' : '🎵 Real-Time FFT'
        }.`
      );
    } catch (err) {
      setError(err.message);
    }
  };

  // Apply Pattern Generator to Selection Range (or entire song if no range selected)
  const applyPattern = (patternType) => {
    if (frames.length === 0) return;
    const start =
      selectionStart !== null && selectionEnd !== null
        ? Math.min(selectionStart, selectionEnd)
        : 0;
    const end =
      selectionStart !== null && selectionEnd !== null
        ? Math.max(selectionStart, selectionEnd)
        : frames.length - 1;
    const span = Math.max(1, end - start + 1);
    const speed = Math.max(1, patternSpeed);

    const next = [...frames];
    for (let s = start; s <= end; s++) {
      const rel = s - start;
      const phase = Math.floor(rel / speed);
      const row = new Array(channelsCount).fill(0);

      switch (patternType) {
        case 'chase-lr': {
          const activeCh = phase % channelsCount;
          row[activeCh] = brushValue;
          break;
        }
        case 'chase-rl': {
          const activeCh = (channelsCount - 1) - (phase % channelsCount);
          row[activeCh] = brushValue;
          break;
        }
        case 'cylon': {
          const cycle = (channelsCount - 1) * 2;
          const pos = phase % cycle;
          const activeCh = pos < channelsCount ? pos : cycle - pos;
          row[activeCh] = brushValue;
          break;
        }
        case 'alternate': {
          const evenOn = phase % 2 === 0;
          for (let c = 0; c < channelsCount; c++) {
            if ((c % 2 === 0) === evenOn) row[c] = brushValue;
          }
          break;
        }
        case 'pulse': {
          const on = phase % 2 === 0;
          for (let c = 0; c < channelsCount; c++) {
            row[c] = on ? brushValue : 0;
          }
          break;
        }
        case 'fade-in': {
          const val = Math.round(((rel / Math.max(1, span - 1)) * brushValue));
          for (let c = 0; c < channelsCount; c++) row[c] = val;
          break;
        }
        case 'fade-out': {
          const val = Math.round(((1 - rel / Math.max(1, span - 1)) * brushValue));
          for (let c = 0; c < channelsCount; c++) row[c] = val;
          break;
        }
        case 'all-on': {
          for (let c = 0; c < channelsCount; c++) row[c] = brushValue;
          break;
        }
        case 'clear':
        default:
          break;
      }
      next[s] = row;
    }

    setFrames(next);
    setDirty(true);
  };

  // CSV Export / Import
  const handleExportCsv = () => {
    if (!frames.length) return;
    const csvContent = frames.map((row) => row.join(',')).join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const stem = currentSong?.filename?.replace(/\.[^/.]+$/, '') || 'sequence';
    a.href = url;
    a.download = `${stem}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImportCsv = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (evt) => {
      const text = String(evt.target?.result || '');
      const parsedFrames = [];
      for (const line of text.split(/\r?\n/)) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) continue;
        const vals = trimmed.split(',').map((v) => {
          const num = parseFloat(v.trim());
          if (isNaN(num)) return 0;
          return num <= 1.0 && v.includes('.')
            ? Math.round(num * 255)
            : Math.max(0, Math.min(255, Math.round(num)));
        });
        while (vals.length < channelsCount) vals.push(0);
        parsedFrames.push(vals.slice(0, channelsCount));
      }
      if (parsedFrames.length > 0) {
        setFrames(parsedFrames);
        setDirty(true);
        showNotice(`Imported ${parsedFrames.length} frames from "${file.name}".`);
      }
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  const currentFrameValues = frames[currentStep] || new Array(channelsCount).fill(0);

  return (
    <div className="dashboard sequence-studio">
      <header className="dashboard-header studio-header">
        <div className="studio-title-group">
          <h1>✨ Custom Light Sequence Studio</h1>
          {songsList.length > 0 && (
            <select
              className="studio-song-select"
              value={currentSong?.id || ''}
              onChange={(e) => {
                const found = songsList.find((s) => s.id === parseInt(e.target.value, 10));
                if (found) setCurrentSong(found);
              }}
            >
              {songsList.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title} ({s.filename}) — [{s.sequence_mode.toUpperCase()}]
                </option>
              ))}
            </select>
          )}
        </div>

        <div className="header-actions">
          {currentSong && (
            <div className="mode-toggle-pill">
              <button
                type="button"
                className={`mode-pill-btn ${
                  currentSong.sequence_mode !== 'custom' ? 'active-fft' : ''
                }`}
                onClick={() => handleModeSwitch('fft')}
              >
                🎵 FFT Mode
              </button>
              <button
                type="button"
                className={`mode-pill-btn ${
                  currentSong.sequence_mode === 'custom' ? 'active-custom' : ''
                }`}
                onClick={() => handleModeSwitch('custom')}
              >
                ✨ Custom Mode
              </button>
            </div>
          )}

          <button
            type="button"
            className="btn btn-success"
            onClick={handleSaveSequence}
            disabled={saving || !currentSong}
          >
            {saving ? '💾 Saving...' : dirty ? '💾 Save Sequence *' : '💾 Save Sequence'}
          </button>

          {onBack && (
            <button type="button" onClick={onBack} className="btn btn-secondary">
              ← Back
            </button>
          )}
          <button type="button" onClick={onLogout} className="btn btn-secondary">
            Logout
          </button>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="notice-banner">{notice}</div>}

      {!currentSong ? (
        <div className="no-data">No song selected. Upload a song in the Song Library first.</div>
      ) : loading ? (
        <div className="loading">Loading sequence &amp; audio waveform...</div>
      ) : (
        <div className="studio-workspace">
          {/* Hidden HTML5 Audio Element */}
          <audio
            ref={audioRef}
            src={songsApi.getAudioUrl(currentSong.id)}
            onEnded={() => setIsPlaying(false)}
            onPause={() => setIsPlaying(false)}
            onPlay={() => setIsPlaying(true)}
            preload="auto"
          />

          {/* Top Row: Virtual 8-Channel Light Bulbs & Live Tap Pads */}
          <div className="card virtual-lights-card">
            <div className="virtual-lights-header">
              <div>
                <h3>
                  💡 8-Channel Live Light Preview &amp; Tap Pads
                  {liveRecord && (
                    <span className="recording-badge">🔴 LIVE TAP RECORDING</span>
                  )}
                </h3>
                <span className="virtual-lights-hint">
                  Hold keys <code>1</code>–<code>8</code> or press the channel bulbs below while playing to perform/record live!
                </span>
              </div>

              <div className="transport-controls">
                <button
                  type="button"
                  className={`btn ${isPlaying ? 'btn-warning' : 'btn-success'}`}
                  onClick={togglePlayPause}
                >
                  {isPlaying ? '⏸ Pause (Space)' : '▶ Play (Space)'}
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={handleStopAudio}
                >
                  ⏹ Stop
                </button>
                <button
                  type="button"
                  className={`btn ${liveRecord ? 'btn-danger' : 'btn-secondary'}`}
                  onClick={() => setLiveRecord((v) => !v)}
                  title="When enabled, pressing 1-8 or clicking pads while playing writes into the sequence"
                >
                  {liveRecord ? '🔴 Recording ON' : '⚪ Live Record'}
                </button>

                <select
                  className="speed-select"
                  value={playbackRate}
                  onChange={(e) => {
                    const rate = parseFloat(e.target.value);
                    setPlaybackRate(rate);
                    if (audioRef.current) audioRef.current.playbackRate = rate;
                  }}
                  title="Playback speed (slow down for easier live tap recording)"
                >
                  <option value="0.5">0.5x Speed</option>
                  <option value="0.75">0.75x Speed</option>
                  <option value="1">1.0x Speed</option>
                </select>

                <div className="time-readout">
                  <span>{formatTimeDetailed(currentTimeSec)}</span>
                  <span className="step-readout">
                    Step {currentStep + 1} / {frames.length}
                  </span>
                </div>
              </div>
            </div>

            <div className="virtual-bulbs-grid">
              {Array.from({ length: channelsCount }).map((_, chIdx) => {
                const isHeld = Boolean(activeKeys[chIdx]);
                const val = isHeld ? brushValue : currentFrameValues[chIdx] || 0;
                const intensity = val / 255;
                const color = CHANNEL_COLORS[chIdx % CHANNEL_COLORS.length];

                return (
                  <button
                    key={chIdx}
                    type="button"
                    className={`virtual-bulb-pad ${val > 0 ? 'lit' : ''}`}
                    style={{
                      '--bulb-color': color,
                      '--bulb-alpha': Math.max(0.12, intensity),
                      boxShadow:
                        val > 0
                          ? `0 0 ${Math.round(8 + intensity * 24)}px ${hexToRgba(
                              color,
                              intensity * 0.9
                            )}`
                          : 'none'
                    }}
                    onMouseDown={() => {
                      setActiveKeys((prev) => ({ ...prev, [chIdx]: true }));
                      if (liveRecord && frames[currentStep]) {
                        paintStepChannel(currentStep, chIdx, brushValue);
                      }
                    }}
                    onMouseUp={() =>
                      setActiveKeys((prev) => {
                        const next = { ...prev };
                        delete next[chIdx];
                        return next;
                      })
                    }
                    onMouseLeave={() =>
                      setActiveKeys((prev) => {
                        const next = { ...prev };
                        delete next[chIdx];
                        return next;
                      })
                    }
                  >
                    <div
                      className="bulb-lens"
                      style={{
                        backgroundColor: hexToRgba(color, 0.18 + intensity * 0.82)
                      }}
                    />
                    <div className="bulb-label">CH {chIdx + 1}</div>
                    <div className="bulb-value">
                      Key [{chIdx + 1}] • {Math.round((val / 255) * 100)}%
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Toolbar: Brush, Timing, Auto-FFT, and Pattern Generators */}
          <div className="card studio-toolbar-card">
            <div className="toolbar-row">
              {/* Brush Intensity */}
              <div className="toolbar-section">
                <span className="toolbar-label">🖌 Brush Brightness:</span>
                <div className="brush-presets">
                  {[
                    { label: '100%', val: 255 },
                    { label: '75%', val: 192 },
                    { label: '50%', val: 128 },
                    { label: '25%', val: 64 },
                    { label: 'Erase (0)', val: 0 }
                  ].map((preset) => (
                    <button
                      key={preset.val}
                      type="button"
                      className={`btn btn-sm ${
                        brushValue === preset.val ? 'btn-primary' : 'btn-secondary'
                      }`}
                      onClick={() => setBrushValue(preset.val)}
                    >
                      {preset.label}
                    </button>
                  ))}
                  <input
                    type="range"
                    min="0"
                    max="255"
                    value={brushValue}
                    onChange={(e) => setBrushValue(parseInt(e.target.value, 10))}
                    className="brush-slider"
                    title={`Brightness: ${brushValue}/255`}
                  />
                  <span className="brush-num">{brushValue}</span>
                </div>
              </div>

              {/* Step Timing & Zoom */}
              <div className="toolbar-section">
                <span className="toolbar-label">⏱ Step Timing:</span>
                <select
                  value={timingMs}
                  onChange={(e) => handleTimingChange(e.target.value)}
                  className="compact-select"
                >
                  <option value="25">25 ms (40 fps)</option>
                  <option value="50">50 ms (20 fps)</option>
                  <option value="100">100 ms (10 fps)</option>
                  <option value="200">200 ms (5 fps)</option>
                </select>

                <span className="toolbar-label ml-sm">🔍 Zoom:</span>
                <input
                  type="range"
                  min="3"
                  max="24"
                  value={stepWidthPx}
                  onChange={(e) => setStepWidthPx(parseInt(e.target.value, 10))}
                  className="zoom-slider"
                />

                <label className="follow-checkbox">
                  <input
                    type="checkbox"
                    checked={followPlayhead}
                    onChange={(e) => setFollowPlayhead(e.target.checked)}
                  />
                  Follow
                </label>
              </div>

              {/* Auto-Generate from FFT & CSV Import/Export */}
              <div className="toolbar-section">
                <button
                  type="button"
                  className="btn btn-sm btn-primary"
                  onClick={handleGenerateFromFft}
                  disabled={generatingFft}
                  title="Analyze the song with FFT and populate the sequence grid automatically"
                >
                  {generatingFft ? '⚡ Analyzing Audio...' : '⚡ Auto-Generate from FFT'}
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  onClick={handleExportCsv}
                >
                  📤 Export CSV
                </button>
                <label className="btn btn-sm btn-secondary csv-import-label">
                  📥 Import CSV
                  <input
                    type="file"
                    accept=".csv,text/csv"
                    onChange={handleImportCsv}
                    hidden
                  />
                </label>
              </div>
            </div>

            {/* Pattern Generators Row */}
            <div className="toolbar-row pattern-row">
              <div className="toolbar-section">
                <span className="toolbar-label">
                  ✨ Pattern Fill (
                  {selectionStart !== null && selectionEnd !== null
                    ? `Steps ${Math.min(selectionStart, selectionEnd) + 1}–${
                        Math.max(selectionStart, selectionEnd) + 1
                      }`
                    : 'Entire Song'}
                  ):
                </span>
                <select
                  value={patternSpeed}
                  onChange={(e) => setPatternSpeed(parseInt(e.target.value, 10))}
                  className="compact-select"
                  title="Pattern speed (steps per change)"
                >
                  <option value="1">Fast (1 step)</option>
                  <option value="2">Quick (2 steps)</option>
                  <option value="4">Medium (4 steps)</option>
                  <option value="8">Slow (8 steps)</option>
                  <option value="16">Very Slow (16 steps)</option>
                </select>

                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  onClick={() => applyPattern('chase-lr')}
                >
                  Chase 1→8
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  onClick={() => applyPattern('chase-rl')}
                >
                  Chase 8→1
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  onClick={() => applyPattern('cylon')}
                >
                  Bounce 1↔8
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  onClick={() => applyPattern('alternate')}
                >
                  Odd / Even
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  onClick={() => applyPattern('pulse')}
                >
                  Strobe / Pulse
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  onClick={() => applyPattern('fade-in')}
                >
                  Fade In ↗
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  onClick={() => applyPattern('fade-out')}
                >
                  Fade Out ↘
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  onClick={() => applyPattern('all-on')}
                >
                  All On
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-danger"
                  onClick={() => applyPattern('clear')}
                >
                  Clear
                </button>
                {selectionStart !== null && (
                  <button
                    type="button"
                    className="btn btn-sm btn-secondary"
                    onClick={() => {
                      setSelectionStart(null);
                      setSelectionEnd(null);
                    }}
                  >
                    Clear Selection
                  </button>
                )}
              </div>

              <label className="follow-checkbox">
                <input
                  type="checkbox"
                  checked={activateCustomOnSave}
                  onChange={(e) => setActivateCustomOnSave(e.target.checked)}
                />
                Enable Custom Mode on Save
              </label>
            </div>
          </div>

          {/* Interactive Timeline Grid (Sticky Labels + Scrollable Canvas) */}
          <div className="card timeline-card">
            <div className="timeline-container">
              {/* Sticky Left Track Labels */}
              <div className="timeline-labels">
                <div className="label-cell ruler-label">TIME</div>
                <div className="label-cell waveform-label">WAVEFORM</div>
                {Array.from({ length: channelsCount }).map((_, chIdx) => (
                  <div
                    key={chIdx}
                    className="label-cell channel-label"
                    style={{
                      borderLeft: `4px solid ${
                        CHANNEL_COLORS[chIdx % CHANNEL_COLORS.length]
                      }`
                    }}
                  >
                    <span>Channel {chIdx + 1}</span>
                  </div>
                ))}
              </div>

              {/* Scrollable Canvas Viewport */}
              <div className="timeline-scroll-viewport" ref={scrollContainerRef}>
                <canvas
                  ref={canvasRef}
                  className="sequence-canvas"
                  onMouseDown={handleCanvasMouseDown}
                  onMouseMove={handleCanvasMouseMove}
                  onMouseUp={handleCanvasMouseUp}
                  onMouseLeave={handleCanvasMouseUp}
                  onContextMenu={(e) => e.preventDefault()}
                />
              </div>
            </div>
            <div className="timeline-footer-hint">
              <span>
                💡 <strong>Tip:</strong> Click or drag on the <strong>Channel 1–8</strong> rows to paint light brightness (Right-click or Alt+click to erase). Click or drag on the top <strong>TIME / WAVEFORM</strong> track to seek or select a time range for pattern effects.
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function hexToRgba(hex, alpha) {
  const clean = hex.replace('#', '');
  const r = parseInt(clean.substring(0, 2), 16);
  const g = parseInt(clean.substring(2, 4), 16);
  const b = parseInt(clean.substring(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function formatSecShort(totalSec) {
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function formatTimeDetailed(sec) {
  const mins = Math.floor(sec / 60);
  const secs = Math.floor(sec % 60);
  const tenths = Math.floor((sec % 1) * 10);
  return `${mins}:${secs.toString().padStart(2, '0')}.${tenths}`;
}
