# AIfred

A self-hosted personal assistant that runs on a local model, remembers things in [brain.md](https://github.com/mi4uu/brain.md), and keeps an eye on your WhatsApp, Gmail, Calendar and Telegram so you don't have to.

AIfred is the agent. brain.md is its memory. They're built to work together: AIfred reads and writes your notes, journal and contacts through brain.md's MCP server, and brain.md gives it a searchable second brain that survives restarts and stays editable by you (or other tools) at any time. You need a running brain.md instance to use AIfred.

Everything runs on your own machine. The model is local (ollama), the data stays local, and any cloud fallback is off unless you turn it on.

## Why it exists

Most assistants either live in the cloud with your private data, or they're a thin wrapper that dumps everything into a giant prompt and hopes the model sorts it out. AIfred takes the opposite line:

- **Local first.** Your messages, mail and notes never leave the box. The default model is `qwen3.6:27b` on ollama.
- **Code-first, LLM-last.** Filtering, ranking, identity resolution and parsing are done in plain code. The model only sees a short, relevant shortlist, never a raw data dump. Turn context stays under 16k tokens on purpose.
- **Verified, not vibes.** There's a small evaluation harness (`aifred/eval`) with golden cases drawn from real data. Model and sampling choices are decided by measured hallucination and accuracy, and a change that raises hallucination doesn't ship.

## What it does

- **Reads your Gmail and manages your Calendar.** Read-only mail by default; calendar events always cite where they came from.
- **Watches WhatsApp**, including family groups, and works out who's who. WhatsApp now hands out `@lid` IDs instead of phone numbers, so AIfred resolves them back to real numbers and matches them against your Google contacts and brain.md people. It also learns that different people call you different things (your partner says "kotek", nobody else does).
- **Triages quietly in the background.** Every few minutes it pulls new mail and messages, decides what actually needs you, and pushes only that to Telegram. Group chatter aimed at someone else stays quiet. One-time codes, newsletters and phishing-shaped "verify your payment" mail are filtered out by code, not left to the model.
- **Asks before it acts.** When it spots a date in a note ("dentist on the 9th at 16:40"), it proposes a calendar event over Telegram with Yes/No buttons and a link back to the source note. Nothing lands on your calendar without a tap.
- **Treats your self-chat as an inbox.** Messages you send to yourself on WhatsApp (notes, forwards) are captured to brain.md instead of being dropped.
- **Remembers across days.** A small in-engine retrieval layer (a 0.6B embedder running alongside the chat model) indexes your own conversations, mail and notes, so it can answer "what did Kasia ask me to buy last week" without re-reading everything.
- **Reads images and documents when needed.** A vision model is loaded on demand to read a screenshot or photo, and Google Docs/Sheets links are pulled in through Drive export.
- **Writes a structured daily note** and keeps your contact book current on its own.
- **Learns from you.** Correct it in chat, in the web UI, or after the fact: mute a sender, mark someone as important, teach it a nickname. Uncertain calls go to a "to decide" queue where your answer becomes a rule.

You talk to it through a web UI or Telegram. Both go through the same agent.

## How it fits with brain.md

brain.md is a local-first markdown vault with a built-in MCP server and semantic search. AIfred uses it as the single source of truth for anything worth keeping: journal entries, the daily note, people, tasks. Reads go through brain.md's retrieval, so the model sees relevant chunks rather than whole files.

Because brain.md is shared and editable, AIfred treats it as live. It re-reads your contacts each cycle, so a number or nickname you add by hand (or another tool adds) shows up without a restart.

Want the memory without the assistant? Run brain.md on its own. Want the assistant? You'll need brain.md too.

## Architecture

```
Telegram  ─┐                         ┌─ ollama: qwen3.6:27b    (agent: text + tools)
Web UI    ─┼─ FastAPI agent loop ────┼─ ollama: qwen3-vl:8b     (vision, on demand)
WhatsApp  ─┤   router + typed tools  ├─ ollama: qwen3-embedding (semantic memory)
Gmail/Cal ─┘                         └─ brain.md MCP            (notes / journal / people)
                     │
                  SQLite  (scratch: messages, cursors, attention, embeddings)
```

- **Agent**: `qwen3.6:27b` drives the tools. It's the one model that stayed reliable at multi-tool use in testing. Smaller vision models passed isolated checks but fell over in the full loop, so vision is a separate on-demand call instead.
- **Vision**: `qwen3-vl:8b` loads only when an image needs reading, then frees the memory again.
- **Memory**: a `qwen3-embedding:0.6b` model stays resident next to the chat model (set `OLLAMA_MAX_LOADED_MODELS=2`) so retrieval never evicts the agent.
- **Store**: SQLite is the working scratch layer (incoming messages, cursors, triage output, embeddings). brain.md is canonical for notes.

Design notes, constraints and the full list of invariants live in [`SPEC.md`](SPEC.md). It's written in a compact spec format and is the best place to understand why things are the way they are.

## Requirements

- [ollama](https://ollama.com) with the models you want (`qwen3.6:27b`, optionally `qwen3-vl:8b-instruct-q8_0` for vision and `qwen3-embedding:0.6b` for memory)
- A running [brain.md](https://github.com/mi4uu/brain.md) instance and an API key
- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/)
- Optional: Google OAuth credentials (Gmail read, Calendar, Drive read, Contacts), a Telegram bot token, a WhatsApp account to pair

## Quick start

```bash
uv sync
cp .env.example .env        # fill in the brain.md URL + key, and whatever channels you want
uv run pytest               # sanity check

# point ollama at a 16k context window so prompts aren't silently truncated:
#   OLLAMA_CONTEXT_LENGTH=16384   (and OLLAMA_MAX_LOADED_MODELS=2 if you use memory)

uv run aifred               # web UI + API on http://127.0.0.1:9120
```

Run it as an always-on service with the unit in `deploy/`:

```bash
systemctl --user enable --now aifred
```

It binds to localhost. Put it behind a Cloudflare tunnel (or similar) if you want it reachable from your phone. WhatsApp pairing happens in the web UI: it shows a QR code you scan once, and the session persists.

## Configuration

All settings are environment variables, read from `.env`. Secrets stay there and never touch the code, the spec or the logs. `.env.example` has the full list. The essentials:

| Variable | What it's for |
|---|---|
| `AIFRED_MODEL` | agent model (default `qwen3.6:27b`) |
| `AIFRED_BRAINMD_MCP_URL`, `MCP_BRAIN_MD_API_KEY` | your brain.md instance |
| `AIFRED_GOOGLE_TOKEN_PATH`, `AIFRED_GOOGLE_CLIENT_SECRET_PATH` | Gmail / Calendar / Drive / Contacts |
| `AIFRED_TELEGRAM_BOT_TOKEN`, `AIFRED_TELEGRAM_ALLOWED_USERS` | Telegram channel (push + chat) |
| `AIFRED_WHATSAPP_ENABLED` | turn WhatsApp on, then pair from the web UI |
| `AIFRED_VISION_MODEL`, `AIFRED_EMBED_MODEL` | on-demand vision and the memory embedder |

Every channel is optional. With nothing but brain.md configured, AIfred still runs as a notes-and-chat assistant and tells you which subsystems are off.

## Privacy

Your data stays on your machine. The model is local, WhatsApp and mail are processed locally, and the cloud fallback (`AIFRED_CLOUD_FALLBACK_ENABLED`) is off by default. WhatsApp groups and a full mailbox are sensitive, so AIfred can run fully offline if that's what you want. Telegram is gated to an allowed-users list, and actions that reach the outside world wait for your confirmation.

## Status

AIfred works and is in daily use, but it's young and shaped around one person's setup, so expect rough edges. The eval harness (`uv run python -m aifred.eval.run`) and the invariants in `SPEC.md` are the guard rails that keep it honest as it changes.

Issues and contributions are welcome. If you already run brain.md and a local model, it might save you some time too.

## Licence

AGPL-3.0-or-later, matching brain.md.
