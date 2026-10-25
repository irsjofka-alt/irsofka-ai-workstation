# Token & VRAM efficiency plan

Goal: make workers more capable **without** sending more context, and use local models
**only when VRAM is safe**. This document is a working plan, not a claim that everything
already runs.

Initial status: 2026-10-24.

**P0 done (2026-10-24):** prompt ordering (system/style → knowledge → user), token
accounting from the engine's `usage` (agy: `input_tokens`, `output_tokens`,
`cache_read_tokens`; NULL = not reported), plus per-worker `answer_style` and
`max_output_tokens`. Verified on an agy run: tokens were recorded and the prompt began
with the style instruction. Correction note: until 2026-10-24 the `max_output_tokens`
field was only **stored**; forwarding it to the engine was added and measured in the
verification block below.

**P1 done (2026-10-24):**

- **P1.1 Deterministic compression** — the last 6 messages stay intact, older messages
  are cut (head + tail + marker), tool output is capped at 240 characters. Total budget
  6,000 characters; the summary is never squeezed out by old messages.
- **P1.2 Rolling summary + VRAM guard** — `summary` and `summary_covered_id` per
  conversation (migration 015). Threshold 12 messages; the last 6 are never summarized.
  Model `tev1:0.8b`, `think:false`, `keep_alive:"0s"`, `num_ctx:4096`, `num_predict:420`.
  If the guard refuses or the output is unusable → deterministic summary. Verified: the
  model loaded and then stopped (`Stopping...`) with `ollama ps` empty; while ComfyUI was
  active the guard refused and the fallback was used.
- **P1.3 Knowledge threshold + cache** — minimum vector score 0.25, at most 3 chunks ×
  ±800 characters, query embeddings cached (128 entries) with `keep_alive:"0s"`. While a
  render is active, search falls back to keywords.
- **P1.4 Identical-result cache** — automation only, on Ollama engines (the HTTP path
  with no tool/shell). Key: FNV-1a over engine + model + full prompt; TTL 24 hours
  (`STATION_CACHE_TTL_SECS`). A second run does not call the engine, emits a `cache`
  event, and keeps the original token numbers. The automation_run row receives its final
  status even when the run finished before that row was written.
- **P1.5 Artifact reference** — workflow step output above 2,000 characters is written to
  `data/artifacts/<workflow_run>/step-<n>-<name>.md`; the next step receives only the
  path plus a summary ≤800 characters. The workflow log is capped at 2,000 characters.

**P0–P1 verification through real runs (2026-10-24):**

| Tested | Measured result |
|---|---|
| Output limit | Worker `max_output_tokens=24` → **132 characters**; `400` → **1,898 characters** (engine `local-fallback`, qwen3.5:4b, same prompt) |
| Limit forwarding | `qoder --max-output-tokens <n>` (the flag is in `qoder --help`); template engines use `max_tokens_arg` from the registry (`["--max-output-tokens","{value}"]`); `agy` has no such flag → the limit is only a prompt note, never an invented argument |
| Identical-result cache | The same automation run twice: the second run carries a `cache` event stating that the engine was not called, with no `delta` |
| Artifact | A tool step produced 13,893 characters → written to `data/artifacts/<workflow_run>/step-0-panjang.md`; the next step received path + summary ≤800 |
| History compression | A 14-message conversation → final prompt **1,046 characters**, containing the summary block, the condensed older block, and the last-six block |
| Summary fallback | With ComfyUI active, the VRAM guard refused the local model → deterministic summary used (`summary_model=deterministic`) |
| Defect closed | The deterministic-summary marker used to nest on every cycle; old markers are now stripped first (`strip_summary_markers`) |
| Tokens per worker | `GET /api/traces/summary` returns `by_waker` (count, input/output tokens, how many runs actually reported usage); Console → Traces shows a "Token per worker" panel. Measured: 24 runs, 21 reported, 91,895 input / 7,161 output tokens |

`cargo test`: 51 passing (including output limit, cache, artifact, and anti-nesting).

**P2 done (2026-10-24):**

- **P2.1 MCP runtime** — 13 tools read from `config/mcp_servers.json`; a tool's detail is
  loaded when called; permission `mcp[:server[:tool]]`; `ask` writes an approval row
  (not a silent refusal); at most 3 tool rounds. Verified with 13 live tools, one real
  tool-calling run, and an approval that turned into a worker permission.
- **P2.2 Task & quota routing** — utility work (summary, embedding) stays local through
  `config/resource_policy.json` + the VRAM guard; hard tasks use the engine the operator
  chose. `engine_key = "auto"` is resolved from the `engine_quota` meters
  (`engine-rust/src/quota.rs`, `GET /api/routing/suggest`), and the decision is recorded
  as a `routing` event on the run.
- **P2.3 Eval harness** — dataset `config/eval_tasks.json` with 13 tasks using
  deterministic checks (`contains`, `contains_compact`, `not_contains`, `max_chars`,
  `json_fields`, `no_tool`, `tool_called`), no judge model. Endpoints:
  `GET /api/eval/tasks`, `POST /api/eval/run`, `GET /api/eval/runs`,
  `GET /api/eval/runs/:id`. Every case stores output, per-check results, input/output
  tokens, and duration. A task whose tool permission is not `allow` is `skipped` — it
  does not lower the score, and the count is kept.

**Measured baseline 2026-10-24** (engine `gemini`, model `gemini-3.8-flash-high`):
**12/12 passed, 1 skipped** (`tool_telemetry`, MCP permission not `allow`), score 1.0,
**44,951 input / 3,517 output tokens** across 13 tasks. A score of 1.0 means the dataset
is still easy for that model; its value is regression detection (format, length, banned
words, tool discipline) and tokens per task, not the 1.0 itself.

How to use it before changing a prompt, model, or compression policy: run the eval, then
compare `score` and `input_tokens` with the baseline above. A lower score means the change
is rejected. To compare another model, send `engine_key` + `model` on
`POST /api/eval/run`; the default model comes from that engine's registry entry, not from
the worker.

Environment knobs: `STATION_HISTORY_KEEP` (6), `STATION_HISTORY_CHARS` (6000),
`STATION_SUMMARY_TRIGGER` (12), `STATION_SUMMARY_CHARS` (1600),
`STATION_KNOWLEDGE_MIN_SCORE` (0.25), `STATION_CACHE_TTL_SECS` (86400). GPU policy lives
in `config/resource_policy.json`; its status is visible at `GET /api/system/resources`
and on the Settings page.

---

## 1. Machine state when this plan was written

| Item | Value |
|---|---|
| GPU | RTX 3060 12 GB |
| VRAM in use at the time | ±1.6 GB (ComfyUI running) |
| Competing applications | `comfyui-desktop`, Comfy MCP, Unreal MCP |
| `nomic-embed-text` | 0.27 GB |
| `tev1:0.8b` | 0.81 GB |
| `qwen3.5:4b` | 3.32 GB |
| `qwen3.5:9b` | 6.55 GB (do not use for utility work) |

Conclusion: utility work (summary, titles, classification) must use a model **≤1 GB**,
with `keep_alive=0`, and stop entirely while a render/Unreal is active.

---

## 2. Principles

1. **Cut what is sent, not widen the window.** Summary + retrieval + cache is cheaper
   than a 1M context.
2. **Small models for small jobs.** Summary/classification does not need a frontier model.
3. **VRAM is a shared resource.** A local model must not stay in VRAM after it finishes;
   if the GPU is busy, use the deterministic fallback.
4. **Every saving must be measured.** Without token numbers, "lean" is just a claim.

---

## 3. P0 work — cheap, fast, measurable (2–3 days)

### P0.1 Prompt ordering for prefix cache
- Order the prompt: **system prompt → SOP → static skill/knowledge (stable order) → user message**.
- Remove volatile text (timestamps, run ids, random paths) from the front.
- Goal: providers with prefix caching (Qoder/Claude) can hit the cache.
- Measure: compare input tokens on a second run with the same system prompt.

### P0.2 Token accounting
- Take `usage` from the `agy`/qoder JSON output when available; store
  `input_tokens`, `output_tokens`, `cost_estimate` in `waker_run`.
- Show it in Trace and in a per-worker summary.
- Acceptance: every run that reports usage shows numbers; runs that do not are marked
  `unsupported`, never zero.

### P0.3 Output limit & answer style
- Per-worker fields: `max_output_tokens`, `answer_style` (brief/normal/detailed).
- Forwarded to the CLI (`--max-tokens`/prompt policy) as far as the engine supports it.

---

## 4. P1 work — the big savings (1–1.5 weeks)

### P1.1 Deterministic compression (no model)
- Keep the last N messages in full (default 6).
- Older messages: cut, preserving the first/last sentence and important decisions.
- Tool output is trimmed (e.g. 2,000 characters) unless full output was requested.
- This is **always** available, even when the GPU is busy.

### P1.2 Rolling summary (local model, when safe)
- Summaries of old conversation are stored per conversation (`summary`, `covered_until`).
- The next prompt = summary + the last 6 messages, not the whole history.
- Model: `tev1:0.8b` (0.81 GB) with `keep_alive=0`, `num_ctx=4096`.
- Quality upgrade: `qwen3.5:4b` only when free VRAM ≥5 GB and no render is running.
- If the summary fails → use deterministic compression.

### P1.3 Knowledge chunking + threshold
- Split long entries into ~800-character chunks (120 overlap).
- Inject only chunks with a score ≥ threshold (e.g. 0.25 vector / keyword score > 0).
- At most 3 chunks; include title + citation.
- Cache query embeddings (text hash → vector) for repeated questions.

### P1.4 Identical-result cache
- Key: hash(engine, model, prompt, knowledge version).
- Only for prompts without real-time data; default TTL 24 hours.
- Runs that use tools/shell are never cached.

### P1.5 Artifact reference
- Long workflow/step output is stored in a file.
- The next step receives **path + summary**, not the full text.

---

## 5. P2 work — new capability (2–3 weeks)

### P2.1 MCP runtime + progressive disclosure
- MCP client (stdio/SSE/HTTP) with a tool catalogue.
- Send the **tool index** in the prompt; load details when a tool is called.
- Unlocks Lark MCP, filesystem, browser, database.

### P2.2 Task- and quota-based model routing
- Summary/classification → local; hard tasks → frontier.
- Use Treasury data to pick an engine whose quota is still available.

### P2.3 Eval harness
- 10–20 sample tasks; measure quality **and** tokens per run.
- Precondition before changing a prompt/model: quality must not drop.

---

## 6. VRAM policy (cross-cutting, required for P1.2)

### 6.1 Model table

| Job | Model | Size | Condition |
|---|---|---|---|
| Embedding | `nomic-embed-text` | 0.27 GB | free VRAM ≥1 GB |
| Summary/classification | `tev1:0.8b` | 0.81 GB | free VRAM ≥2 GB |
| Quality summary | `qwen3.5:4b` | 3.32 GB | free VRAM ≥5 GB **and** no render |
| — | `qwen3.5:9b` | 6.55 GB | **not used** for utility work |

### 6.2 Execution rules
- Always send `keep_alive: "0s"` → the model leaves VRAM right after the answer.
- Small `num_ctx` (2,048–4,096) to keep the KV cache small.
- Where supported, embeddings can be forced onto CPU (`num_gpu: 0`); otherwise skip
  vectors and use keyword search.
- **Guard**: before running a local model, check
  - free VRAM (`nvidia-smi`), and
  - busy processes (`comfyui-desktop`, `UnrealEditor`, other render processes).
- If VRAM is below the threshold **or** a process is busy:
  - summary → deterministic compression,
  - embedding → keyword fallback,
  - other utility work → goes to the "idle" queue.

### 6.3 Idle queue — **not built (recorded deviation, 2026-10-24)**

Original plan: non-urgent work (summary, titles, dream) is stored and runs when the GPU
has been free for ≥60 seconds.

What is used now: **immediate deterministic fallback** for summaries and **keyword
search** for embeddings — no queue, no retry. Reason: the P1.2 acceptance criterion in §7
is already met ("fallback runs when the GPU is busy"), and an idle queue would add a
second execution path that must be tested on its own. If deterministic summaries turn out
to be too weak, the queue can be added later without changing the existing
`summary`/`summary_covered_id` schema.

### 6.4 Configuration
`config/resource_policy.json`:
```json
{
  "gpu": {
    "min_free_llm_gb": 2.0,
    "min_free_embedding_gb": 1.0,
    "busy_processes": ["comfyui-desktop", "ComfyUI", "UnrealEditor"]
  },
  "models": {
    "summary": { "model": "tev1:0.8b", "keep_alive": "0s", "num_ctx": 4096 },
    "summary_quality": { "model": "qwen3.5:4b", "min_free_gb": 5.0 },
    "embedding": { "model": "nomic-embed-text", "keep_alive": "0s" }
  },
  "fallback": { "summary": "deterministic", "embedding": "keyword" }
}
```

### 6.5 Endpoint & UI
- `GET /api/system/resources`: free/total VRAM, loaded models, busy processes, and the
  decisions (`local_allowed_summary`, `local_allowed_embedding`).
- The Settings page shows this status so the operator knows when a local model is safe
  to use.

---

## 7. Implementation order & completion criteria

| Phase | Content | Done when |
|---|---|---|
| P0 | Prompt ordering, token accounting, output limit | Input tokens recorded; prefix stable |
| P1.1 | Deterministic compression | A 20+ turn conversation does not resend the whole history |
| P1.2 + VRAM guard | Rolling summary + policy | `ollama ps` empty after summarizing; fallback runs while the GPU is busy |
| P1.3–P1.5 | Chunking, result cache, artifact | Knowledge enters only when relevant; long output is not resent |
| P2 ✅ | MCP + routing + eval | Real tools can be called; quality and tokens measured (baseline 2026-10-24) |

---

## 8. Risks

- **The 0.8B model is limited** for complex summaries → keep the extractive fallback and
  the `qwen3.5:4b` option for when VRAM is safe.
- **Prefix cache is not guaranteed** by every provider → measure, do not assume.
- **Render-process detection can miss** (different process names) → the list is
  configurable, not hardcode.
- **The VRAM checker needs `nvidia-smi`**; without it the guard falls back to conservative
  mode (assume unsafe).
