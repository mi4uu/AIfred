# Eval results — model + sampling (2026-06-04)

Harness: `aifred/eval`, 26 golden cases (grounding / extract / triage) from real
AIfred data, context tiers small / grow(~3k) / huge(~10k). Metrics: hallucination
rate (invented answer/date/false-alarm) and accuracy. Ollama serves 16k
(`OLLAMA_CONTEXT_LENGTH=16384`), greedy seed=42.

## Model comparison (greedy temp=0)

| model | accuracy | halluc | grow_halluc | speed | notes |
|-------|---------:|-------:|------------:|------:|-------|
| **qwen3.6:27b** | **0.65** | **0.12** | 0.25 | **~88s** | winner; no invented dates |
| gpt-oss:latest | 0.58 | 0.15 | 0.25 | ~263s | 3× slower; invented a calendar date from "w piątek"; one triage miss |

Sampling (greedy / low_faithful / qwen_rec) made almost no difference for either
model — greedy was best or tied. So sampling is NOT the lever.

## The real finding: context size, not model or sampling

Grounding accuracy by context tier (both models, every sampling):

    small (needle only)   ~0.67
    grow  (~3k pad)       ~0.67–0.83
    huge  (~10k pad)       0.00   <-- collapses; hallucination appears here

At ~10k tokens the model can't find the needle (lost-in-the-middle) and starts
inventing — even though the window is 16k, so it's not truncation. This is
model-agnostic (qwen and gpt-oss both fail).

## Single-model test: qwen3-vl (vision) on the text tasks (2026-06-04)

Question: can one vision-capable model do everything (text + tools + images) so we
never swap models? Warm timing = per-call with the model already resident (a
warmup call precedes the timed loop).

| model | acc | halluc | warm median | small/grow ctx | size |
|-------|----:|-------:|------------:|----------------|-----:|
| qwen3.6:27b | 0.65 | 0.12 | 5.9s | 0.67 / 0.83 | 17 GB |
| qwen3-vl:8b (q4) | 0.65 | 0.12 | 8.1s | 0.67 / 1.0 | ~6 GB |
| **qwen3-vl:8b-instruct-q8_0** | **0.73** | 0.15 | **1.2s** | **1.0 / 1.0** | ~9 GB |

The q8 vision model **beats the 27B on our text tasks** — higher accuracy,
perfect on small/grow context, fastest warm median — and also passed:
- **tool-calling**: native `tool_calls` with dict args (agent loop works)
- **vision/OCR**: read "DENTYSTA 16:40 Zosia" off a test image verbatim

So one model covers text + tools + images. No more eviction/swap (the old
"spin up qwen-vl briefly" problem disappears — the main model *is* the VL model).
The +0.03 hallucination vs 27B is one borderline case and acceptable.

Adopted qwen3-vl:8b-instruct-q8_0 — THEN REVERTED. In real interactive use the
8B failed agentic tool-use: asked to "zanotuj X" it called journal_add with the
wrong arg (`note` not `text`), then FABRICATED success ("zapiszę ręcznie, to
działa") and wrote nothing. Also invented personas from stray group names
(Tomek) and lost context. The eval only covered single-shot extract/triage,
not multi-turn tool orchestration — that's where 8B breaks.

**Reverted to qwen3.6:27b** (default, V30): verified it both confirms AND
actually writes ("Zanotowane" + the line lands in brain.md). The single-model
dream is off for now — vision will be a separate on-demand call. Lesson: model
choice must also be benchmarked on agentic tool-use, not just single-shot tasks.
qwen3-vl:8b-q8 stays available for vision/OCR.

## Decisions

1. **Keep qwen3.6:27b at greedy (temperature 0)** — best accuracy, lowest
   hallucination, fastest. gpt-oss is worse on every axis for our tasks.
2. **Context size is the lever.** Never feed large raw blobs. Use brain.md
   retrieval (`context_for_query` / `similar_notes`, token-budgeted) to pass only
   the few relevant chunks; keep the hot triage/extract paths minimal. "Retrieve,
   don't dump."
3. Any change that adds context must be re-run through this harness — it must not
   raise hallucination (V28).

Rerun: `uv run python -m aifred.eval.run qwen3.6:27b --samplings=greedy -v`
(one model at a time; ollama keeps a single model resident).

## Candidate sweep — tools+vision single-model hunt (2026-06-05)

Added an AGENTIC tool-use eval (aifred/eval/agentic.py) after 8B-vl fabricated
saves — the single-shot eval missed it. Tested gemma4, glm-ocr, qwen3.5:9b.

| model | agentic (4-tool) | real 24-tool loop | vision | text acc | warm med |
|-------|-----------------:|-------------------|--------|---------:|---------:|
| qwen3.6:27b | 1.00 | reliable (saves every time) | no | 0.65 | 5.9s |
| gemma4:latest | 1.00 | FLAKY — sometimes emits tool call as text (no save) | yes | 0.69 | 1.6s |
| qwen3.5:9b | 1.00 | FAIL — searches instead of saving | yes | 0.65 | 2.0s |
| qwen3-vl:8b-q8 | 0.83 | FAIL — wrong tool + fabricates | yes | 0.73 | 1.2s |
| glm-ocr:latest | 0.00 | n/a — never calls tools | (ocr) | — | — |

Lesson: the isolated agentic test is necessary but NOT sufficient — three models
aced it then broke in the full loop. Only qwen3.6:27b is reliable as the agent.
Kept it; smaller vision models are vision/OCR-on-demand only.

Two model-agnostic fixes landed from the sweep (V35): tool errors feed back
instead of crashing the turn; current date injected each turn (fixed gemma4's
wrong-year calendar args and helps every model).
