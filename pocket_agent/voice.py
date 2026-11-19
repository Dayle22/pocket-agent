"""Text-to-speech engine using Microsoft Edge neural voices (edge-tts) and QtMultimedia."""

from __future__ import annotations

import asyncio
import math
import queue
import re
import threading
import time
from array import array
from collections import deque

import aiohttp
from PyQt6.QtCore import QBuffer, QIODevice, QObject, QTimer, pyqtSignal
from PyQt6.QtMultimedia import (
    QAudioBuffer,
    QAudioBufferOutput,
    QAudioFormat,
    QAudioOutput,
    QMediaPlayer,
)

VOICE_PRESETS: tuple[tuple[str, str], ...] = (
    ("Leah (South Africa)", "en-ZA-LeahNeural"),
    ("Luke (South Africa)", "en-ZA-LukeNeural"),
    ("Sonia (UK)", "en-GB-SoniaNeural"),
    ("Ryan (UK)", "en-GB-RyanNeural"),
    ("Aria (US)", "en-US-AriaNeural"),
    ("Guy (US)", "en-US-GuyNeural"),
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[,;:])\s+")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_UNCLOSED_MD_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*$")
_URL_RE = re.compile(r"https?://\S+")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
_BLOCKQUOTE_RE = re.compile(r"(?m)^\s*>\s?")
_LIST_MARKER_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+\.)\s+")
_EMPHASIS_RE = re.compile(r"[*_~]+")
_MULTI_SPACE_RE = re.compile(r"\s+")

_OPENING_CLAUSE_THRESHOLD = 70
_MIN_CLAUSE_CHARS = 24
_MAX_CONCURRENT_SYNTH = 3


def clean_text_for_speech(text: str) -> str:
    """Strip Markdown syntax and URLs so spoken replies sound natural."""
    if not text:
        return ""
    cleaned = _MD_LINK_RE.sub(r"\1", text)
    cleaned = _URL_RE.sub("", cleaned)
    cleaned = _INLINE_CODE_RE.sub(r"\1", cleaned)
    cleaned = _HEADING_RE.sub("", cleaned)
    cleaned = _BLOCKQUOTE_RE.sub("", cleaned)
    cleaned = _LIST_MARKER_RE.sub("", cleaned)
    cleaned = _EMPHASIS_RE.sub("", cleaned)
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned).strip()
    return cleaned


class SentenceBuffer:
    """Incrementally extracts speakable sentences from streamed Markdown chunks."""

    def __init__(
        self,
        clause_threshold: int = _OPENING_CLAUSE_THRESHOLD,
        min_clause_chars: int = _MIN_CLAUSE_CHARS,
    ) -> None:
        self._clause_threshold = clause_threshold
        self._min_clause_chars = min_clause_chars
        self._raw = ""
        self._pending = ""
        self._in_code_block = False
        self._has_emitted = False

    def reset(self) -> None:
        """Clear any buffered partial text and reset code-fence state."""
        self._raw = ""
        self._pending = ""
        self._in_code_block = False
        self._has_emitted = False

    def feed(self, chunk: str) -> list[str]:
        """Consume a streamed text chunk and return any complete speakable sentences."""
        if not chunk:
            return []
        self._raw += chunk
        self._pending += self._drain_non_code_text()
        if not self._pending:
            return []

        parts = _SENTENCE_SPLIT_RE.split(self._pending)
        if len(parts) > 1:
            complete = [s for s in (clean_text_for_speech(p) for p in parts[:-1]) if s]
            self._pending = parts[-1]
            if complete:
                self._has_emitted = True
                return complete

        if not self._has_emitted and len(self._pending) >= self._clause_threshold:
            split = self._split_opening_clause(self._pending)
            if split is not None:
                head, tail = split
                self._pending = tail
                self._has_emitted = True
                return [head]

        return []

    def flush(self) -> list[str]:
        """Flush any remaining buffered text at the end of a turn."""
        if not self._in_code_block and self._raw:
            self._pending += self._raw
        text = clean_text_for_speech(self._pending)
        self.reset()
        return [text] if text else []

    def _split_opening_clause(self, text: str) -> tuple[str, str] | None:
        """Split a long unpunctuated opening buffer at the first safe clause boundary."""
        excluded = self._excluded_spans(text)
        for match in _CLAUSE_SPLIT_RE.finditer(text):
            pos = match.start()
            if any(start <= pos < end for start, end in excluded):
                continue
            # Avoid splitting dates/lists like "May 1, 2026" or "items 1, 2, 3"
            if pos > 0 and text[pos - 1] == "," and text[match.end() : match.end() + 1].isdigit():
                continue
            head = clean_text_for_speech(text[:pos])
            if len(head) >= self._min_clause_chars:
                return head, text[match.end() :]
        return None

    @staticmethod
    def _excluded_spans(text: str) -> list[tuple[int, int]]:
        """Return character spans (URLs, Markdown links, inline code) where clause splits are unsafe."""
        spans: list[tuple[int, int]] = []
        for match in _MD_LINK_RE.finditer(text):
            spans.append((match.start(), match.end()))
        unclosed_link = _UNCLOSED_MD_LINK_RE.search(text)
        if unclosed_link:
            spans.append((unclosed_link.start(), len(text)))
        for match in _URL_RE.finditer(text):
            spans.append((match.start(), match.end()))

        last_code_end = 0
        for match in _INLINE_CODE_RE.finditer(text):
            spans.append((match.start(), match.end()))
            last_code_end = match.end()
        unclosed_tick = text.find("`", last_code_end)
        if unclosed_tick != -1:
            spans.append((unclosed_tick, len(text)))
        return spans

    def _drain_non_code_text(self) -> str:
        """Consume complete non-code segments from self._raw while preserving trailing fences."""
        out: list[str] = []
        while self._raw:
            idx = self._raw.find("```")
            if idx == -1:
                if self._in_code_block:
                    # Keep only last 2 chars in case a closing ``` is split across chunks
                    self._raw = self._raw[-2:] if len(self._raw) >= 2 else self._raw
                else:
                    # Hold back up to 2 trailing backticks in case ``` is split across chunks
                    hold = 2 if self._raw.endswith("``") else (1 if self._raw.endswith("`") else 0)
                    if hold:
                        out.append(self._raw[:-hold])
                        self._raw = self._raw[-hold:]
                    else:
                        out.append(self._raw)
                        self._raw = ""
                break

            if not self._in_code_block:
                out.append(self._raw[:idx])
            self._raw = self._raw[idx + 3 :]
            self._in_code_block = not self._in_code_block

        return "".join(out)


def compute_buffer_level(buf: QAudioBuffer) -> float:
    """Compute a normalized visual speech amplitude (0..1) from a decoded QAudioBuffer."""
    if not buf.isValid():
        return 0.0
    byte_count = buf.byteCount()
    if byte_count <= 0:
        return 0.0
    ptr = buf.constData()
    if ptr is None:
        return 0.0

    raw = ptr.asstring(byte_count)
    fmt = buf.format().sampleFormat()

    if fmt == QAudioFormat.SampleFormat.Float:
        usable = len(raw) - (len(raw) % 4)
        if usable <= 0:
            return 0.0
        samples = array("f")
        samples.frombytes(raw[:usable])
        scale = 1.0
    elif fmt == QAudioFormat.SampleFormat.Int16:
        usable = len(raw) - (len(raw) % 2)
        if usable <= 0:
            return 0.0
        samples = array("h")
        samples.frombytes(raw[:usable])
        scale = 32768.0
    elif fmt == QAudioFormat.SampleFormat.Int32:
        usable = len(raw) - (len(raw) % 4)
        if usable <= 0:
            return 0.0
        samples = array("i")
        samples.frombytes(raw[:usable])
        scale = 2147483648.0
    elif fmt == QAudioFormat.SampleFormat.UInt8:
        samples = [b - 128 for b in raw]
        scale = 128.0
    else:
        return 0.0

    count = len(samples)
    if count == 0:
        return 0.0

    step = max(1, count // 256)
    sq_sum = 0.0
    n = 0
    for i in range(0, count, step):
        v = samples[i] / scale
        sq_sum += v * v
        n += 1
    if n == 0:
        return 0.0

    rms = math.sqrt(sq_sum / n)
    gated = max(0.0, rms - 0.01)
    norm = min(1.0, gated * 4.2)
    return norm ** 0.72


class _ReusableTCPConnector(aiohttp.TCPConnector):
    """TCPConnector kept open across edge_tts.Communicate calls until shutdown.

    edge_tts.Communicate instantiates aiohttp.ClientSession without
    connector_owner=False, so ClientSession.__aexit__ would otherwise close a
    caller-supplied connector after the first utterance.
    """

    async def close(self, *, abort_ssl: bool = False) -> None:
        return None

    async def force_close(self) -> None:
        await super().close()


class VoiceEngine(QObject):
    """Background edge-tts synthesis + sequential QtMultimedia playback."""

    speaking_started = pyqtSignal()
    speaking_stopped = pyqtSignal()
    pulse_requested = pyqtSignal(float)
    audio_level_updated = pyqtSignal(float)
    error_occurred = pyqtSignal(str)
    _audio_ready = pyqtSignal(int, object)

    def __init__(
        self,
        voice_name: str = "en-ZA-LeahNeural",
        enabled: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._voice_name = voice_name
        self._enabled = enabled
        self._generation_id = 0
        self._buffer = SentenceBuffer()

        self._synth_queue: queue.Queue[tuple[int, str, str] | None] = queue.Queue()
        self._play_queue: deque[bytes] = deque()
        self._current_buffer: QBuffer | None = None
        self._is_speaking = False

        self._loop_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake_event: asyncio.Event | None = None
        self._connector: _ReusableTCPConnector | None = None

        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(1.0)
        self._buffer_output = QAudioBufferOutput(self)
        self._buffer_output.audioBufferReceived.connect(self._on_audio_buffer)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.setAudioBufferOutput(self._buffer_output)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.errorOccurred.connect(self._on_player_error)

        self._amp_slices: deque[tuple[int, int, float]] = deque(maxlen=256)
        self._last_buffer_time: float = 0.0
        self._latest_buffer_level: float = 0.0

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(33)
        self._pulse_timer.timeout.connect(self._emit_live_level)

        self._audio_ready.connect(self._on_audio_ready)

        self._worker = threading.Thread(target=self._worker_loop, name="pocket-agent-tts", daemon=True)
        self._worker.start()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def voice_name(self) -> str:
        return self._voice_name

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable voice responses; stops active speech when disabled."""
        self._enabled = enabled
        if not enabled:
            self.stop()

    def set_voice(self, voice_name: str) -> None:
        """Update the active edge-tts neural voice."""
        self._voice_name = voice_name

    def feed_chunk(self, chunk: str) -> None:
        """Feed a streamed assistant message chunk into the TTS pipeline."""
        if not self._enabled or not chunk:
            return
        for sentence in self._buffer.feed(chunk):
            self._enqueue_sentence(sentence)

    def flush(self) -> None:
        """Flush any remaining buffered text at the end of a turn."""
        if not self._enabled:
            self._buffer.reset()
            return
        for sentence in self._buffer.flush():
            self._enqueue_sentence(sentence)

    def speak_sample(self, text: str = "Hello! This is your Pocket Agent voice.") -> None:
        """Stop current speech and play a short voice preview using the active voice."""
        self.stop()
        cleaned = clean_text_for_speech(text)
        if cleaned:
            self._enqueue_sentence(cleaned)

    def stop(self) -> None:
        """Interrupt active speech and discard any queued or in-flight sentences."""
        self._generation_id += 1
        self._buffer.reset()

        # Drain pending synthesis items while preserving any shutdown sentinel
        while True:
            try:
                item = self._synth_queue.get_nowait()
                if item is None:
                    self._synth_queue.put(None)
                    break
            except queue.Empty:
                break

        self._wake_worker()

        self._player.stop()
        self._release_current_buffer()
        self._reset_audio_meter()
        self._play_queue.clear()

        self._set_speaking(False)

    def shutdown(self) -> None:
        """Stop playback and terminate the background synthesis thread."""
        self.stop()
        self._synth_queue.put(None)
        self._wake_worker()
        if self._worker.is_alive():
            self._worker.join(timeout=1.5)

    def _enqueue_sentence(self, sentence: str) -> None:
        self._synth_queue.put((self._generation_id, self._voice_name, sentence))
        self._wake_worker()

    def _wake_worker(self) -> None:
        with self._loop_lock:
            loop = self._loop
            wake_event = self._wake_event
        if loop is not None and wake_event is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(wake_event.set)
            except RuntimeError:
                pass

    def _worker_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_worker())
        finally:
            loop.close()

    async def _async_worker(self) -> None:
        loop = asyncio.get_running_loop()
        wake_event = asyncio.Event()
        connector = _ReusableTCPConnector()
        with self._loop_lock:
            self._loop = loop
            self._wake_event = wake_event
            self._connector = connector

        in_flight: deque[tuple[int, asyncio.Task[bytes]]] = deque()
        shutting_down = False

        try:
            while True:
                wake_event.clear()
                current_gen = self._generation_id

                if in_flight and any(g != current_gen for g, _ in in_flight):
                    in_flight, stale = self._partition_stale_tasks(in_flight, current_gen)
                    if stale:
                        for task in stale:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*stale, return_exceptions=True)

                while not shutting_down and len(in_flight) < _MAX_CONCURRENT_SYNTH:
                    try:
                        item = self._synth_queue.get_nowait()
                    except queue.Empty:
                        break
                    if item is None:
                        shutting_down = True
                        break
                    gen_id, voice_name, text = item
                    if gen_id != self._generation_id:
                        continue
                    task = asyncio.create_task(self._synthesise(text, voice_name, connector))
                    in_flight.append((gen_id, task))

                if shutting_down:
                    for _, task in in_flight:
                        if not task.done():
                            task.cancel()
                    if in_flight:
                        await asyncio.gather(*(t for _, t in in_flight), return_exceptions=True)
                    in_flight.clear()
                    break

                emitted_any = False
                while in_flight and in_flight[0][1].done():
                    gen_id, task = in_flight.popleft()
                    emitted_any = True
                    if task.cancelled():
                        continue
                    exc = task.exception()
                    if exc is not None:
                        if gen_id == self._generation_id:
                            self.error_occurred.emit(str(exc))
                        continue
                    audio_bytes = task.result()
                    if gen_id != self._generation_id:
                        continue
                    if audio_bytes:
                        self._audio_ready.emit(gen_id, audio_bytes)

                if emitted_any and not self._synth_queue.empty():
                    continue

                pending_tasks = {t for _, t in in_flight if not t.done()}
                wake_task = asyncio.create_task(wake_event.wait())
                try:
                    await asyncio.wait(
                        {wake_task} | pending_tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    if not wake_task.done():
                        wake_task.cancel()
                        try:
                            await wake_task
                        except asyncio.CancelledError:
                            pass
        finally:
            with self._loop_lock:
                self._loop = None
                self._wake_event = None
                self._connector = None
            await connector.force_close()

    @staticmethod
    def _partition_stale_tasks(
        in_flight: deque[tuple[int, asyncio.Task[bytes]]],
        current_gen: int,
    ) -> tuple[deque[tuple[int, asyncio.Task[bytes]]], list[asyncio.Task[bytes]]]:
        kept: deque[tuple[int, asyncio.Task[bytes]]] = deque()
        stale: list[asyncio.Task[bytes]] = []
        for gen_id, task in in_flight:
            if gen_id != current_gen:
                stale.append(task)
            else:
                kept.append((gen_id, task))
        return kept, stale

    async def _synthesise(
        self,
        text: str,
        voice_name: str,
        connector: aiohttp.BaseConnector | None = None,
    ) -> bytes:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice=voice_name, connector=connector)
        chunks: list[bytes] = []
        async for message in communicate.stream():
            if message["type"] == "audio":
                chunks.append(message["data"])
        return b"".join(chunks)

    def _on_audio_ready(self, gen_id: int, audio_data: object) -> None:
        if gen_id != self._generation_id or not self._enabled:
            return
        if not isinstance(audio_data, (bytes, bytearray)) or not audio_data:
            return
        self._play_queue.append(bytes(audio_data))
        if (
            self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
            and self._current_buffer is None
        ):
            self._play_next()

    def _play_next(self) -> None:
        self._reset_audio_meter()
        self._release_current_buffer()

        if not self._play_queue:
            self._set_speaking(False)
            return

        audio_bytes = self._play_queue.popleft()
        buf = QBuffer()
        buf.setData(audio_bytes)
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        self._current_buffer = buf
        self._set_speaking(True)
        self._player.setSourceDevice(buf)
        self._player.play()

    def _release_current_buffer(self) -> None:
        if self._current_buffer is not None:
            self._player.setSourceDevice(None)
            self._current_buffer.close()
            self._current_buffer = None

    def _reset_audio_meter(self) -> None:
        self._amp_slices.clear()
        self._last_buffer_time = 0.0
        self._latest_buffer_level = 0.0

    def _on_audio_buffer(self, buf: QAudioBuffer) -> None:
        level = compute_buffer_level(buf)
        self._last_buffer_time = time.monotonic()
        self._latest_buffer_level = level
        start_us = buf.startTime()
        dur_us = buf.duration()
        if start_us >= 0 and dur_us > 0:
            start_ms = start_us // 1000
            end_ms = (start_us + dur_us) // 1000
            self._amp_slices.append((start_ms, end_ms, level))

    def _emit_live_level(self) -> None:
        if not self._is_speaking:
            return
        now = time.monotonic()
        if self._last_buffer_time > 0.0 and (now - self._last_buffer_time) <= 0.6:
            pos_ms = self._player.position()
            while self._amp_slices and self._amp_slices[0][1] < pos_ms - 50:
                self._amp_slices.popleft()
            level = self._latest_buffer_level
            for start_ms, end_ms, slice_level in self._amp_slices:
                if start_ms <= pos_ms <= end_ms or start_ms > pos_ms:
                    level = slice_level
                    break
            self.audio_level_updated.emit(level)
            return

        # Fallback cadence if the audio backend did not deliver PCM buffers yet
        cadence = (
            0.22
            + 0.45 * (0.5 - 0.5 * math.cos(now * 14.0))
            + 0.30 * (0.5 - 0.5 * math.cos(now * 6.3 + 1.1))
        )
        self.pulse_requested.emit(cadence)

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status in (
            QMediaPlayer.MediaStatus.EndOfMedia,
            QMediaPlayer.MediaStatus.InvalidMedia,
        ):
            self._play_next()

    def _on_player_error(self, _error, error_string: str) -> None:
        if error_string:
            self.error_occurred.emit(error_string)
        self._play_next()

    def _set_speaking(self, speaking: bool) -> None:
        if speaking == self._is_speaking:
            return
        self._is_speaking = speaking
        if speaking:
            self._pulse_timer.start()
            self.speaking_started.emit()
        else:
            self._pulse_timer.stop()
            self.speaking_stopped.emit()
