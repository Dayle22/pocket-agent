"""Tests for the Edge TTS voice engine, sentence chunking, and Markdown cleaning."""

from __future__ import annotations

import asyncio
import math
import threading
import time
from array import array

from PyQt6.QtMultimedia import QAudioBuffer, QAudioFormat

from pocket_agent.agents import BUILTIN_AGENTS
from pocket_agent.ui.drawer import ChatDrawer
from pocket_agent.voice import (
    SentenceBuffer,
    VoiceEngine,
    _ReusableTCPConnector,
    clean_text_for_speech,
    compute_buffer_level,
)


def test_clean_text_for_speech_strips_markdown_and_urls() -> None:
    raw = "## Hello **World**\n- Check [app.py](file:///c:/app.py) and `voice.py` at https://example.com"
    cleaned = clean_text_for_speech(raw)
    assert cleaned == "Hello World Check app.py and voice.py at"


def test_sentence_buffer_streams_complete_sentences_and_flushes() -> None:
    buf = SentenceBuffer()
    assert buf.feed("Hello there") == []
    assert buf.feed(", friend. How ") == ["Hello there, friend."]
    assert buf.feed("is `app.py` doing? Great") == ["How is app.py doing?"]
    assert buf.flush() == ["Great"]
    assert buf.flush() == []


def test_sentence_buffer_ignores_fenced_code_blocks_across_chunks() -> None:
    buf = SentenceBuffer()
    assert buf.feed("Here is code.\n\n``") == ["Here is code."]
    assert buf.feed("`python\nprint('hi!')\n``") == []
    assert buf.feed("`\nDone now.") == []
    assert buf.flush() == ["Done now."]


def test_voice_engine_toggle_and_stop(qapp) -> None:
    engine = VoiceEngine(voice_name="en-ZA-LeahNeural", enabled=True)
    try:
        assert engine.enabled is True
        assert engine.voice_name == "en-ZA-LeahNeural"
        engine.set_voice("en-ZA-LukeNeural")
        assert engine.voice_name == "en-ZA-LukeNeural"

        gen_before = engine._generation_id
        engine.set_enabled(False)
        assert engine.enabled is False
        assert engine._generation_id == gen_before + 1
        assert engine.is_speaking is False
    finally:
        engine.shutdown()


def test_on_request_voice_plays_only_explicit_text(qapp, monkeypatch) -> None:
    engine = VoiceEngine(enabled=False)
    queued = []
    monkeypatch.setattr(engine, "_enqueue_sentence", queued.append)
    monkeypatch.setattr(engine, "_play_next", lambda: None)
    try:
        engine.feed_chunk("An automatic reply.")
        engine.flush()
        assert queued == []
        engine.speak_sample("Read this reply.")
        assert queued == ["Read this reply."]
        generation = engine._generation_id
        engine._on_audio_ready(generation, b"requested")
        assert list(engine._play_queue) == [b"requested"]
        engine.stop()
        engine._on_audio_ready(generation, b"stale")
        assert list(engine._play_queue) == []
        assert not engine.enabled

        queued.clear()
        engine.speak_text("First sentence. Second sentence.")
        assert queued == ["First sentence.", "Second sentence."]
        assert not engine.enabled
    finally:
        engine.shutdown()


def test_compute_buffer_level_handles_silence_speech_and_invalid(qapp) -> None:
    assert compute_buffer_level(QAudioBuffer()) == 0.0

    fmt_i16 = QAudioFormat()
    fmt_i16.setSampleRate(24000)
    fmt_i16.setChannelCount(1)
    fmt_i16.setSampleFormat(QAudioFormat.SampleFormat.Int16)

    silence = QAudioBuffer(bytes(2048), fmt_i16)
    assert compute_buffer_level(silence) == 0.0

    loud_samples = array("h", [int(16000 * math.sin(i * 0.2)) for i in range(1024)])
    loud_buf = QAudioBuffer(loud_samples.tobytes(), fmt_i16)
    assert compute_buffer_level(loud_buf) > 0.8

    fmt_f32 = QAudioFormat()
    fmt_f32.setSampleRate(24000)
    fmt_f32.setChannelCount(1)
    fmt_f32.setSampleFormat(QAudioFormat.SampleFormat.Float)
    mid_samples = array("f", [0.15 * math.sin(i * 0.2) for i in range(512)])
    mid_buf = QAudioBuffer(mid_samples.tobytes(), fmt_f32)
    mid_level = compute_buffer_level(mid_buf)
    assert 0.25 < mid_level < 0.85


def test_drawer_settings_page_opens_and_changes_voice(qapp) -> None:
    drawer = ChatDrawer(
        agents=list(BUILTIN_AGENTS),
        current_agent_id="agy",
        workspace=".",
        voice_enabled=True,
        voice_name="en-ZA-LeahNeural",
    )
    selected_voices: list[str] = []
    toggled_states: list[bool] = []
    drawer.voice_selected.connect(selected_voices.append)
    drawer.voice_toggled.connect(toggled_states.append)

    assert drawer._pages.currentWidget() is drawer._chat_page
    drawer.open_settings()
    assert drawer._pages.currentWidget() is drawer._settings_page

    idx = drawer._settings_page._voice_combo.findData("en-GB-RyanNeural")
    assert idx >= 0
    drawer._settings_page._voice_combo.setCurrentIndex(idx)
    assert selected_voices == ["en-GB-RyanNeural"]

    drawer._settings_page._voice_enable_cb.setChecked(False)
    assert toggled_states == [False]

    drawer.close_settings()
    assert drawer._pages.currentWidget() is drawer._chat_page


def test_sentence_buffer_opening_clause_split_respects_threshold_and_runs_once() -> None:
    buf = SentenceBuffer(clause_threshold=70, min_clause_chars=24)

    # Under threshold: comma does not split yet
    assert buf.feed("Let us inspect the audio pipeline first, ") == []
    # Crossing threshold triggers a single opening clause split
    assert buf.feed("and then benchmark the first playback frame") == [
        "Let us inspect the audio pipeline first,"
    ]

    # Subsequent long clauses after the first emission wait for normal sentence boundaries
    assert buf.feed(", plus verify concurrency, and confirm ordering") == []
    assert buf.feed(" before wrapping up. Next sentence") == [
        "and then benchmark the first playback frame, plus verify concurrency, and confirm ordering before wrapping up."
    ]
    assert buf.flush() == ["Next sentence"]

    # reset() re-enables opening clause splitting for the next reply
    assert buf.feed(
        "Here is the second reply summary with plenty of detail; it should also split early"
    ) == ["Here is the second reply summary with plenty of detail;"]
    assert buf.flush() == ["it should also split early"]


def test_sentence_buffer_opening_clause_avoids_short_prefix_and_bad_splits() -> None:
    buf = SentenceBuffer(clause_threshold=70, min_clause_chars=24)

    # Short opening "Hi," is skipped; numbers (1,000), times (3:30), dates (May 1, 2026) are not split
    chunk = (
        "Hi, we shipped 1,000 units at 3:30 on May 1, 2026 for the launch: "
        "everything stayed stable across every node"
    )
    assert buf.feed(chunk) == [
        "Hi, we shipped 1,000 units at 3:30 on May 1, 2026 for the launch:"
    ]
    assert buf.flush() == ["everything stayed stable across every node"]


def test_sentence_buffer_opening_clause_skips_urls_links_and_code() -> None:
    buf = SentenceBuffer(clause_threshold=70, min_clause_chars=24)

    # URLs, Markdown links with commas, and split inline code must not cause mid-token splits
    assert (
        buf.feed(
            "Check https://example.com:8080/a,b and [docs, v2](https://example.com/x,y) plus `map(a, "
        )
        == []
    )
    # Closing inline code and adding a safe outer clause split after threshold
    assert buf.feed("b):` for the full setup, then restart the service now") == [
        "Check and docs, v2 plus map(a, b): for the full setup,"
    ]
    assert buf.flush() == ["then restart the service now"]

    # Fenced code blocks never trigger opening clause splits even when long
    buf.reset()
    assert buf.feed("Intro text\n```python\nvalues = [1, 2, 3]; config = {'a': 1, 'b': 2}\n```") == []
    assert buf.flush() == ["Intro text"]


def test_voice_engine_concurrent_synthesis_emits_and_plays_in_order_and_reuses_connector(
    qapp, monkeypatch
) -> None:
    engine = VoiceEngine(voice_name="en-ZA-LeahNeural", enabled=True)
    try:
        # Stub QMediaPlayer device/playback so dummy payloads are not fed to the media backend
        played_payloads: list[bytes] = []
        is_playing = False
        monkeypatch.setattr(engine._player, "setSourceDevice", lambda _dev: None)
        monkeypatch.setattr(
            engine._player,
            "playbackState",
            lambda: engine._player.PlaybackState.PlayingState
            if is_playing
            else engine._player.PlaybackState.StoppedState,
        )

        def fake_play() -> None:
            nonlocal is_playing
            is_playing = True
            assert engine._current_buffer is not None
            played_payloads.append(bytes(engine._current_buffer.data()))

        monkeypatch.setattr(engine._player, "play", fake_play)

        seen_connectors: list[object] = []
        active = 0
        max_active = 0
        delays = {"0": 0.14, "1": 0.02, "2": 0.01, "3": 0.01}

        async def fake_synthesise(text: str, voice_name: str, connector=None) -> bytes:
            nonlocal active, max_active
            seen_connectors.append(connector)
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(delays[text])
                return f"audio:{text}".encode("utf-8")
            finally:
                active -= 1

        monkeypatch.setattr(engine, "_synthesise", fake_synthesise)

        emitted: list[bytes] = []
        done_event = threading.Event()

        def on_ready(_gen_id: int, data: object) -> None:
            emitted.append(bytes(data))
            if len(emitted) == 4:
                done_event.set()

        engine._audio_ready.connect(on_ready)

        for key in ("0", "1", "2", "3"):
            engine._enqueue_sentence(key)

        deadline = time.monotonic() + 3.0
        while not done_event.is_set() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        qapp.processEvents()

        assert emitted == [b"audio:0", b"audio:1", b"audio:2", b"audio:3"]
        assert 2 <= max_active <= 3
        assert len(seen_connectors) == 4
        connector = seen_connectors[0]
        assert isinstance(connector, _ReusableTCPConnector)
        assert all(c is connector for c in seen_connectors)
        assert connector.closed is False

        # First item began playing; remaining 3 are queued in order and advance on EndOfMedia
        assert played_payloads == [b"audio:0"]
        assert list(engine._play_queue) == [b"audio:1", b"audio:2", b"audio:3"]
        while engine._play_queue:
            engine._on_media_status_changed(engine._player.MediaStatus.EndOfMedia)
        assert played_payloads == [b"audio:0", b"audio:1", b"audio:2", b"audio:3"]
    finally:
        connector_ref = engine._connector
        engine.shutdown()

    assert connector_ref is not None
    assert connector_ref.closed is True


def test_voice_engine_stop_cancels_inflight_and_discards_stale_results(qapp, monkeypatch) -> None:
    engine = VoiceEngine(voice_name="en-ZA-LeahNeural", enabled=True)
    try:
        monkeypatch.setattr(engine._player, "setSourceDevice", lambda _dev: None)
        monkeypatch.setattr(engine._player, "play", lambda: None)

        started_count = 0
        all_inflight_started = threading.Event()
        cancelled_texts: list[str] = []
        cancelled_done = threading.Event()

        async def fake_synthesise(text: str, voice_name: str, connector=None) -> bytes:
            nonlocal started_count
            if text.startswith("stale-"):
                started_count += 1
                if started_count == 3:
                    all_inflight_started.set()
                try:
                    await asyncio.sleep(10.0)
                    return f"audio:{text}".encode("utf-8")
                except asyncio.CancelledError:
                    cancelled_texts.append(text)
                    if len(cancelled_texts) == 3:
                        cancelled_done.set()
                    raise
            return f"audio:{text}".encode("utf-8")

        monkeypatch.setattr(engine, "_synthesise", fake_synthesise)

        emitted: list[bytes] = []
        fresh_done = threading.Event()

        def on_ready(_gen_id: int, data: object) -> None:
            payload = bytes(data)
            emitted.append(payload)
            if payload == b"audio:fresh":
                fresh_done.set()

        engine._audio_ready.connect(on_ready)

        for n in ("1", "2", "3", "4"):
            engine._enqueue_sentence(f"stale-{n}")
        assert all_inflight_started.wait(timeout=2.0)

        engine.stop()
        assert cancelled_done.wait(timeout=2.0)
        assert sorted(cancelled_texts) == ["stale-1", "stale-2", "stale-3"]

        engine._enqueue_sentence("fresh")
        deadline = time.monotonic() + 2.0
        while not fresh_done.is_set() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        qapp.processEvents()

        assert emitted == [b"audio:fresh"]
        assert list(engine._play_queue) == []
        assert engine._current_buffer is not None
        assert bytes(engine._current_buffer.data()) == b"audio:fresh"
    finally:
        engine.shutdown()
