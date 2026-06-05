# AIfred — SPEC

## §G — goal

Personal life-assistant. Run local ollama model on hermes-agent runtime. Manage calendar+gmail, watch whatsapp+telegram+mail, keep brain.md journal/notes/tasks, surface important stuff, answer on web UI + telegram.

## §C — constraints

- C1. Local model first. Ollama @ `localhost:11434`. Default `qwen3.6:27b`. Any ollama model swappable. gemma OUT (hallucinate). deepseek-r1, gpt-oss = candidates.
- C2. AIfred = fresh app in `/home/mi4u/AIfred`. Hermes (`/home/mi4u/.hermes/hermes-agent`) = REFERENCE only, not runtime. Port valuable Python skills + lift `hermes-agent/web/` vite UI as start point.
- C3. Stack: core = python + FastAPI via `uv` (agent loop, MCP client, ollama, skills, google, whatsapp, telegram). Web UI = bun + vite + Radix UI. No pip/poetry/conda. No npm/yarn.
- C4. brain.md = single source of truth for notes/journal/tasks. Reached via MCP only.
- C5. Secrets in `~/.hermes/.env` + token json files. Never hardcode. Never commit secret values.
- C6. Privacy: whatsapp family groups + full gmail = sensitive. Local model so data stay local. Cloud fallback (openrouter) = opt-in only.
- C7. Context budget. Target turn ctx ≤16k (NOT 64k like hermes). Hermes skills bloated ctx via prose SKILL.md + raw data dumps → slow+hallucinate+forced big model. AIfred design opposite: code does heavy lift, LLM sees minimal.

## §I — external surfaces

- I.model — ollama OpenAI-compat `http://localhost:11434/v1`. Fallback openrouter (`OPENROUTER_API_KEY`), opt-in.
- I.brainmd — MCP HTTP `https://brainmd.example.com/mcp`, Bearer `${MCP_BRAIN_MD_API_KEY}` in `~/.hermes/.env`. Config `~/.hermes/config.yaml:361-364`.
- I.gmail — Google API via google-workspace skill. OAuth `~/.hermes/google_token.json`, client `~/.hermes/google_client_secret.json`. ?scope now `calendar` ONLY — gmail read scope MISSING.
- I.calendar — Google Calendar API, scope present, manage (read+write).
- I.whatsapp — Baileys via whatscli skill. Session `~/.hermes/platforms/whatsapp/session`. QR pair. Single-instance (2nd conn kills 1st: `conflict: replaced`). No offline catch-up on reconnect.
- I.telegram — AIfred own bot (python lib, e.g. aiogram/python-telegram-bot). Reuse token + allowed users + home channel from `~/.hermes/.env:402-404`.
- I.web — AIfred own FastAPI backend + bun/vite frontend, Radix UI components. Lift `hermes-agent/web/` as scaffold. localhost-only, auth gate.
- I.store — local sqlite. Ingest raw whatsapp/mail msgs, cursors (last-seen per channel), extracted-items cache. Ephemeral working layer. brain.md = canonical for notes/tasks; store = scratch for incremental processing + code prefilter.
- I.skills — port from hermes reference dirs (`hermes-agent/skills`, `optional-skills`, `~/.hermes/skills`). AIfred defines own skill loader; valuable skills: daily-journal, whatsapp-monitor, google-workspace, himalaya, obsidian.

## §V — invariants

- V1. Default model = ollama local. Cloud fallback fires only on explicit opt-in, never silent.
- V38. Group addressee is inferred from the conversation flow, not just names: the triage classifier gets more preceding thread lines for groups (GROUP_CTX_LINES) with each line's sender resolved, and the owner's own lines marked "JA". So a group message that answers/follows the owner's earlier line is directed_at_me even without naming him; one clearly aimed at someone else stays low.
- V37. Stored attention items are re-judged each cycle so frozen decisions don't persist: re-apply the phishing/OTP guards (a later-added guard demotes an old false-high like "order@zen.com payment verification") and re-resolve the sender's name from the original message (lid→phone/Google upgrades fix a stale label, e.g. "Tomek"→"Tomasz Nowak"). The name is rewritten as the whole "[prefix] <name>: …" segment via anchored regex — a substring overlap can never corrupt it (idempotent; repairs prior doubles).
- V36. Vision is on-demand, not the main model. The agent stays qwen3.6:27b; an image is read by a VL model (qwen3-vl:8b-q8) only when needed. Incoming WhatsApp images are downloaded (download_any) to data_dir/media/<ext_id>.jpg and marked '[obraz]' in the message; the vision_describe tool runs the VL model on the latest (or given) image and feeds the TEXT back to the agent. VL uses a short keep_alive so it frees VRAM after sporadic use — first call cold-loads (slow), that's the accepted trade for a rare feature.
- V35. The agent turn is robust: every tool failure (any exception, not just ToolError) is caught and fed back to the model as an "ERROR …" tool result — a bad call never crashes the turn. Current date/time (Europe/Warsaw) is injected into every turn so the model builds correct calendar dates instead of guessing the year. Model choice for the AGENT is gated on the REAL 24-tool loop (aifred/eval/agentic is necessary but not sufficient): qwen3-vl:8b/gemma4/qwen3.5:9b all passed the isolated 4-tool test yet flaked in the full loop (fabricated/searched/emitted tool calls as text), so qwen3.6:27b remains the agent; smaller vision models are vision-on-demand only.
- V34. WhatsApp @lid sender IDs are resolved to real phone numbers via neonize get_pn_from_lid (accounts ARE phones); cached in lid_phone, backfilled on connect + per new message. Identity resolution (name_for, Google/brain match) keys off the resolved phone, not the lid — this is the root fix for recurring "unknown contact" misses (a lid never matches a phone-keyed address book).
- V32. brain.md is a SHARED, mutable store (owner + other agents edit it). AIfred must not trust cached copies: the service periodically re-parses the contact book (ludzie/) so external edits — new number, nickname, person — appear without restart, and the RAG prunes mutable sources (inbox) so edited/deleted lines drop their stale vectors. Live agent vault queries already go through brain_context (fresh MCP every call), so they need no cache invalidation.
- V31. In-engine semantic memory: a standalone embedder (qwen3-embedding:0.6b, kept resident via OLLAMA_MAX_LOADED_MODELS=2 so it NEVER evicts the chat model) indexes AIfred's own data (WhatsApp/attention/chat/inbox) into the embeddings table, incrementally (one embed per snippet, dedup by ref). recall(query,k) returns the few most-relevant snippets across days/sources — "retrieve, don't dump" (small focused context, the V28 lever), not a big blob. Owner-own messages excluded; min_score floor drops weak matches.
- V30. Default model `qwen3.6:27b` — reliable AGENTIC tool-use. qwen3-vl:8b-q8 won the single-shot eval (acc 0.73) but in real interactive use it fabricated tool actions (claimed "zanotowałem" with wrong args + wrote nothing) and invented identities, so it was reverted. Lesson: benchmark model choice on multi-turn tool orchestration, not just single-shot tasks. qwen3-vl:8b-q8 kept for vision/OCR on demand. Any model change decided by `aifred/eval` + an agentic-tool-use check, not by feel.
- V29. Google Docs/Sheets/Slides links are read via Drive export (drive.readonly): Sheets→CSV, Docs/Slides→text, capped small (V28). Exposed as gdoc_read and auto-fetched when a self-note links a doc, so events come from the real source. Degrades silently if the scope isn't granted.
- V28. Context window is bounded and consistent end-to-end so the model never silently truncates: ollama serves 16k (`OLLAMA_CONTEXT_LENGTH=16384` drop-in), the client requests `num_ctx=16384`, and the budget guard trims prompts to ≤16k before send. Sampling defaults to greedy (temperature 0) for faithful extraction/classification — `aifred/eval` golden cases score hallucination (invented answer/date/false-alarm) vs accuracy across models × sampling; a change to defaults must not raise hallucination on the eval.
- V2. All notes/journal/task writes go through brain.md MCP. No second store.
- V3. Calendar events AIfred sets must cite source from brain.md or user msg. No invented events.
- V4. Gmail = read-only by default unless user asks send/draft.
- V5. Exactly ONE whatsapp connection live at a time. Start guards against double-connect.
- V6. Daily journal entry append-only per day; never overwrite prior day.
- V7. Model output that drives action (calendar write, mail send, whatsapp send) must be confirmed before side effect, or run under explicit user pre-auth.
- V8. Secrets read from env/token files at runtime. None in repo, none in spec, none in logs.
- V9. Skill that broke must fail loud (error surfaced to user) not silent no-op.
- V10. Skills exposed to LLM as typed tools (compact JSON schema). Skill prose/instructions NOT injected into context. Code in tool, not in prompt.
- V11. Heavy data (whatsapp history, mail bodies, brain.md) never dumped raw to LLM. Code filters+ranks+summarizes first; LLM sees shortlist/structured digest only.
- V12. brain.md read via search/retrieval (relevant chunks), never whole-file load.
- V13. Channel ingestion incremental via cursor. Process only new since last-seen. No full re-scan per run.
- V14. Per-turn tool set scoped by intent router. Only relevant tools loaded, not all skills at once.
- V15. Deterministic code owns filtering/ranking/IO/parsing. LLM only for judgement + natural-language generation. No LLM for work code can do.
- V16. Permission/scope checks use GRANTED values from provider (token file's `scopes`), never the REQUESTED set. Never act as if a scope is held when only asked for.
- V17. Shared store reached from many threads (whatsapp worker, telegram, web). sqlite conn = `check_same_thread=False` + serialize all access via a lock. No per-thread conn assumption.
- V18. WhatsApp group/direct split decided from the CHAT JID alone (suffix g.us/broadcast/newsletter, prefix 120363, or >=15 digits) — never `sender != chat` (@lid senders differ from the phone-chat JID in 1:1 chats). Triage re-derives the flag at read time; never trusts the stored bit. Group + not-directed-at-owner ⇒ low, never pushed.
- V19. Triage classifier sees the last CTX_LINES messages of the same thread, so a fragment ("Do puszek?") is judged in context, not in a vacuum. Context is preceding lines only (ts < item).
- V20. A small-model "high" that would ping the owner gets a skeptical single-item second opinion (full thread context) before push; disagreement ⇒ downgrade to medium (surfaced in UI, not pushed). Rule-forced items (vip/level) skip the re-check. OTP/2FA codes and self/automated mail are forced low by deterministic guards regardless of the model.
- V21. Items the model is unsure about (verify_push disagreement) are stored status='review' with a JSON meta rule-target, never pushed. The owner's one-click verdict in the "Do decyzji" panel writes a scoped rule (group→group jid, mail→domain, else sender/person) so the same source is auto-handled next time — active learning, not a one-off edit.
- V22. The owner's OWN WhatsApp messages are never triaged or shown as a contact. Capture IsFromMe (live + history key.fromMe) into messages.from_me; also treat sender == owner @lid (config) as self. Triage skips them (cursor still advances); contacts.name_for resolves the owner's lid/phone to the owner, not a phantom "PushName" person. WhatsApp now issues @lid sender IDs distinct from the phone JID — both map to the same identity.
- V23. A daily note ("notatka z dnia") is one structured brain.md doc with fixed Polish sections (⭐ Najważniejsze / 💬 Komunikacja / 📌 Do zrobienia / ✅ Zrobione / 📅 Kalendarz / 🧠 Notatki; empty sections dropped). Code gathers the day's signals (per-person directed messages, triage attention) excluding owner-own and raw group chatter; the LLM only fills the template, no invention (V15). Canonical template kept at Journal/_szablon-dzienny.md.
- V24. AIfred self-maintains so it runs unattended: the service (a) writes confirmed WhatsApp numbers into ludzie/ notes on an UNAMBIGUOUS PushName→alias match from a DIRECT chat only (never a group JID — that's not a person's number; never overwrites a real number; never guesses), and (b) composes yesterday's daily note once per day after a configured hour (idempotent via daily_note_last). Every autonomous side effect is logged + pushed to Telegram so the owner can see what it did, not do it for it.
- V27. Side effects AIfred isn't sure about (calendar events parsed from a self-note) are PROPOSALS, never auto-applied: stored pending, pushed to Telegram with ✅/❌ inline buttons, executed only on the owner's tap (callback gated by the same allowed-users check). Each confirm prompt carries the REASON (the verbatim source quote the event was parsed from) plus a link back to the brain.md fragment, and the batch is introduced with the owner's own note — so a proposal is never a context-free "set this". Proposals dedup by source_ref so re-runs don't re-ask. All-day vs timed events: a YYYY-MM-DD start/end becomes a Google all-day event, a value with 'T' a timed one.
- V26. The owner's self-chat (message-yourself: chat_id == his own number/lid) is his INBOX, not noise. V22's skip-own-messages applies only to his side of OTHER people's chats; in the self-chat his notes/forwards are KEPT, captured to brain.md (Journal/inbox.md) and surfaced as medium attention. Dedup by ref (selfnote:<id>) so re-runs don't duplicate.
- V25. People address the owner by different pet names (e.g. Kasia → "kotek"). These are stored PER PERSON in their ludzie/ note ("Mówi do mnie: …") and only count when THAT person says them (a generic word like "kotek" from anyone else is ignored). Triage treats a message containing the sender's own owner-term as directed_at_me (deterministic, V15). teach_owner_nickname(person, term) persists a new one to brain.md so it's learnable without code changes.

## §T — tasks

id|status|task|cites
T1|x|scaffold uv project: FastAPI core, .env loader, config, dirs|C3,C5
T2|x|ollama client + model abstraction; ctx budget guard ≤16k; pick+pin model (qwen3.6:27b vs deepseek-r1 vs gpt-oss); cloud fallback opt-in|C1,C7,V1,I.model
T3|x|tool framework: typed tools w/ compact JSON schema, NO prose injection; tool registry|V10,V15
T4|x|agent loop: tool-call dispatch, msg history, intent router scopes tool set per turn|C7,V14
T5|x|local store: sqlite schema for raw msgs, cursors, extracted-items cache|I.store,V13
T6|x|MCP client; connect brain.md; retrieval/search wrapper (chunk, not full-file)|I.brainmd,V2,V12
T7|x|google auth: re-auth gmail.readonly + calendar scopes; reuse token files|I.gmail,I.calendar,V4
T8|x|google tools: gmail_search/get, calendar_list/create/update — typed, code-driven|I.gmail,I.calendar,V3,V4,V10
T9|x|whatsapp ingest: robust client (eval vs whatscli/baileys), single-instance guard, incremental cursor → store|I.whatsapp,V5,V13
T10|x|whatsapp digest: code prefilter (sender/keyword/question/date) → LLM summarize shortlist → extracted items to brain.md|I.whatsapp,V11,V15
T11|x|daily-journal tool: append-only per day to brain.md, no full load|V2,V6,V12
T12|x|task tracker tool: todo/done in brain.md, query+update via retrieval|V2,V12
T13|x|calendar-from-brainmd: retrieve relevant brain.md chunks → propose+set events w/ source cite|I.calendar,V3,V7,V12
T14|x|important-digest: code aggregates structured signals (unread, today events, flagged wa) → LLM short summary|I.gmail,I.whatsapp,I.calendar,V11
T15|x|action-confirm layer: gate side-effecting tools behind confirm/pre-auth|V7
T16|x|telegram interface: own bot, allowed-users gate, route to agent|I.telegram
T17|x|web interface: FastAPI backend + bun/vite + Radix UI (lift hermes web/), auth, chat|I.web
T18|x|secret audit: no secrets in repo/logs; .env perms tight|V8,C5

## §B — bugs

id|date|cause|fix
B1|2026-06-03|google Credentials.from_authorized_user_info sets creds.scopes=requested, masking granted scopes; scope check passed falsely|V16
B2|2026-06-03|whatsapp ingest from neonize thread hit sqlite "objects created in a thread" — Store conn bound to main thread, msgs silently dropped|V17
B3|2026-06-04|family-group msgs (Anna→Olka) pushed as "high": history-sync path never set is_group, and len>=15 heuristic flapped; group chatter leaked as owner-directed|V18
B4|2026-06-04|small model over-fired: OTP "kod logowania" high, self/newsletter mail medium, fragments mis-judged w/o thread context|V19,V20
B5|2026-06-04|owner's own sent msgs (WA @lid 100000000000001 "Sówka") ingested as incoming -> phantom unknown contact + self-triaged; a contact (Kasia=Katarzyna, chat 48598765432) shown as raw number bc note had "do ustalenia"|V22|wrote confirmed WA number to ludzie/kasia.md
