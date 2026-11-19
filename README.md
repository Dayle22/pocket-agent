# Pocket Agent

A floating Windows desktop AI companion powered by the **Agent Client Protocol (ACP)** and animated PyQt6 sprites.

Works with any ACP-compatible CLI agent on your machine:
- **Antigravity CLI** (`agy acp`)
- **Gemini CLI** (`gemini --acp`)
- **Codex** (`codex-acp`)
- **OpenCode** (`opencode acp`)
- **Claude Code** (`claude-agent-acp`)
- Plus custom ACP entries in `~/.pocket-agent/config.json`

## Features
- **Always-accessible floating pet** with transparent PyQt6 rendering and real-time ACP lifecycle animations (Thinking, Coding/Tools, Permission Request, Celebrate, Error).
- **4 built-in GLSL VoiceOrbs** (`Siri Sheet`, `Aura Field`, `Iridescent Flow`, `Duotone Flow`) running the exact WebGL shaders from [`amunozdev/voiceorbs`](https://github.com/amunozdev/voiceorbs) via PyQt6 OpenGL, sharing a smooth 7-state contract (`idle`, `connecting`, `listening`, `thinking`, `speaking`, `error`, `disabled`) and live audio/streaming reactivity (`Right-click -> Switch Voice Orb`).
- **Pop-out dark-glass ACP chat drawer** (`click pet` or `Ctrl+Shift+Space`) with streaming Markdown replies, collapsible agent thoughts, live tool-call badges, mode switching, workspace folder picker, and inline tool permission prompts.
- **Built-in PocketBot sprite** plus automatic discovery of 192x208 8x9 sprite packs (e.g. `awesome-antigravity-pet/pets`).

## Quick Start

```powershell
cd C:\Users\User\source\repos\pocket-agent
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m pocket_agent
```

Or double-click `start-pocket-agent.vbs` to launch silently without a console window.

## Controls
- **Left-click pet:** Open or hide the ACP chat drawer
- **Drag pet:** Reposition anywhere on screen (drawer docks cleanly beside it)
- **Right-click pet:** Switch AI agent, switch character, switch Voice Orb, change workspace, resize, toggle Always on Top, or Exit
- **`Ctrl+Shift+Space`:** Global hotkey to toggle and focus the chat drawer

## Credits
- **[`amunozdev/voiceorbs`](https://github.com/amunozdev/voiceorbs):** GLSL fragment shaders, state tables, and math helpers powering the 4 built-in Voice Orbs (`Siri Sheet`, `Aura Field`, `Iridescent Flow`, and `Duotone Flow`).
- **[`wangshuaifbj-collab/awesome-antigravity-pet`](https://github.com/wangshuaifbj-collab/awesome-antigravity-pet):** Inspiration for the floating PyQt6 companion and compatible 192x208 8x9 character sprite packs.
- **[Agent Client Protocol (`agent-client-protocol`)](https://github.com/agentclientprotocol/python-sdk):** Python SDK used to connect to ACP-compatible CLI agents.
