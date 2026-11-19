# Changelog

All notable changes to Pocket Agent will be documented in this file.

## [0.1.0] - 2026-11-19

### Added
- Floating transparent PyQt6 desktop companion (`PocketBot`) with real-time ACP lifecycle animations (Idle, Thinking, Coding/Tools, Permission Request, Celebrate, Error).
- 4 built-in GLSL Voice Orbs (`Siri Sheet`, `Aura Field`, `Iridescent Flow`, `Duotone Flow`) rendered offscreen with PyQt6 OpenGL, 3D glass optics, cursor lighting, and live desktop refraction.
- ACP client bridge supporting `agy acp`, `gemini --acp`, `codex-acp`, `opencode acp`, `claude-agent-acp`, and custom agents via `~/.pocket-agent/config.json`.
- Dark-glass and Light-glass ACP chat drawer (`Ctrl+Shift+Space`) with Windows 11 Acrylic blur, streaming Markdown, collapsible agent thoughts, live tool-call cards, diff viewer, slash commands, model/mode selectors, inline permission prompts, and Markdown export.
- Streaming Edge TTS voice responses (`en-ZA`, `en-GB`, `en-US` presets) with live audio amplitude reactivity on Voice Orbs.
- System tray integration, single-instance guard, Launch at Windows Startup toggle, and console-free Windows `.exe` release packaging via PyInstaller.
