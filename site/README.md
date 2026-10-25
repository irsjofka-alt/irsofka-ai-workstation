# AI Station Playground Site

A local static site that gathers visual output (ComfyUI/Blender), project knowledge, the AI blog, and
the AI Station changelog. Pages are built by `tools/site/build.py` without a framework and without a
Node build step. The site binds to `127.0.0.1`; it opens no public port.

This file is the **only** file under `site/` tracked by git. HTML, posts, media, and the index cache
are gitignored: the content is machine output and is not published.

## 1. Dependencies

| Component | Status | Notes |
|---|---|---|
| Python 3 | required | standard library only for the build |
| AI Station daemon | for `/site` | Rust `irsofka-station-core`, port 8999 |
| Blender 5.2 | Phase 5 | batch render through `blender -b`, not the MCP socket |
| ComfyUI | Phase 4 | output watcher; the daemon may be down during a build |
| Pillow | optional | thumbnails; without Pillow the original image is used |

## 2. Clone + install + init

```bash
git clone https://github.com/irsjofka-alt/irsofka-ai-workstation.git "$HOME/.ai-station"
cd "$HOME/.ai-station"
cp config/site_projects.example.json config/site_projects.json   # then edit the paths
python3 tools/site/build.py --init
python3 tools/site/build.py
```

`--init` creates `site/content/posts/`, `site/media/`, and `data/site-index/`, copies the example
registry when no local registry exists, and writes a welcome post. This README comes from the repo
(the only tracked content under `site/`); restore it with `git checkout -- site/README.md` if it is
missing. The other directories are created by the build.

## 3. Project registry

`config/site_projects.json` (gitignored) overrides example entries with the same `slug`; new entries
are appended. `${HOME}` and `~` are expanded during the build. A missing path does not fail the
build: the project is marked **not available**.

Fields per project: `slug`, `codename`, `name`, `mode` (`single|multi|system`), `status`,
`description`, `paths[]`, `reference_files[]`, `load_all_md`, `media_default`.

The correct MeshCleanRoom path is `ProjectMeshGenesis`, not `IRSofka-ProjectMeshGenesis`.

## 4. Build + serve + native open

```bash
python3 tools/site/build.py             # write pages + feed + search + projects.json
python3 tools/site/build.py --check     # validate structure, links, JSON/XML, secrets
python3 tools/site/build.py --init      # prepare folders + registry + README
bin/site_open.sh                        # build if needed, open http://127.0.0.1:8999/site/
ai-station site                         # same as bin/site_open.sh
```

The build is idempotent: files with identical content are not rewritten. Pages use relative assets,
so `file://.../site/index.html` also opens without the server.

## 5. Posts and media

A post is JSON at `site/content/posts/YYYY/<id>.json`:

```json
{
  "id": "post_20261025_example",
  "type": "blog",
  "project": "ai-station",
  "title": "Title",
  "slug": "title",
  "created_at": "2026-10-25T10:00:00+07:00",
  "worker_id": "wk_...",
  "worker_name": "Assistance Felisha",
  "summary": "One paragraph.",
  "body_md": "Markdown body.",
  "tags": ["note"],
  "visibility": "local",
  "media": [],
  "source": {"path": "site/media/ai-station/2026-10/example.png", "sha256": "...", "prompt": "...", "model": "...", "seed": 0}
}
```

`type`: `blog | showcase | generation | changelog | project-update`. `visibility` defaults to
`local`; `public` requires complete metadata. Only `public` posts enter `feed.xml`.

Media lives in `site/media/<project>/YYYY-MM/` and is referenced by a relative path
`site/media/...`. Thumbnails are written to `site/media/_thumbs/` when Pillow is available.

## 6. Publication contract (summary)

1. Every worker that generates an image/video/model/render must publish through the API/MCP or a
   watched output directory.
2. Minimum metadata: worker, project, kind, mode, time, path, sha256, prompt/model/seed.
3. No secrets, API keys, passwords, tokens, third-party personal data, or illegal content.
   The blog may contain criticism; automatic redaction still runs.
4. Missing metadata means `visibility=local`.
5. The watcher enforces the output directory; the API enforces workers outside it.

Full contract: `brain/rules/site_publication.md`.

## 6b. Worker command contract

Two commands must be recognized by every model, not treated as free text:

- **`worker load`** — load the whole AI-Station: config, engines/models
  (Nvidia, Qoder, Antigravity, local), MCP, workers, project registry, and current
  state. Output must be a READY/DEGRADED/BLOCKED table with evidence. It does not
  log in automatically; a failing model is named with its reason.
- **`worker recovery`** — equivalent to `ai-station recovery 40`: read the
  ledger/SQL, handoff, recent runs, open Human Decide, automations, and git state;
  then build a resume. Unreadable data is reported as UNKNOWN, not “none”.

Command bar above the chat composer:

```text
COMMAND : (Worker Load) (Worker Recovery)   click to insert into the chat box, then press Enter
```

The buttons only fill the input; the operator presses Enter. The console/daemon builds the context
pack and attaches it to the worker message. Implementation:
`tools/worker_context.py`, `POST /api/worker/context`, and MCP `worker_context`.

`@Project <Name>` or `@Codename` in a console message includes the project document context from
`site/project-context/<slug>.json` (generated during the build; redacted and without absolute paths).
The direct/CLI resolver defaults to 100,000 characters to fit the CLI's 128 KB argument limit. The
site build exports project context with a 400,000-character budget for local files; the console
still injects at most 100,000 bytes. Identical documents are not repeated and the remainder becomes
a snippet plus a pointer to the full context file.

## 7. ComfyUI watcher/timer (Phase 4)

Default watched directories: `$HOME/ComfyUI-Shared/output`, `$HOME/runtime/comfyui/output`, and
`$HOME/ComfyUI-Installs/ComfyUI/ComfyUI/output`; override them with `config/site_ingest.json`
(gitignored). The watcher scans every 20 seconds (`OnUnitActiveSec=20`, `AccuracySec=1s` — the
systemd default of 1 minute would make a 20-second schedule drift to ~59 seconds), reads PNG metadata
(`prompt`/`workflow`) or a sidecar JSON file, falls back to the filename when neither exists, and
publishes a `generation` post through `tools/site/publish.py`.

Deduplication uses sha256 + a cursor in `data/site-index/ingest_state.json`. The first pass records
old files as a baseline without posting; `--backfill` forces old files to be included.

```bash
bin/install_units.sh                            # copy repo units to ~/.config/systemd/user
systemctl --user enable --now irsofka-site-ingest.timer
python3 tools/site/ingest.py --once --dry-run   # candidate posts, no writes
journalctl --user -u irsofka-site-ingest.service -n 5
```

Measured 2026-10-25: a new ComfyUI file became a post + thumbnail in 4.65 seconds (the file must be
stable for ≥ 4 seconds); two watcher passes produced no duplicates (`published=[]`).

## 8. Blender render (Phase 5)

Batch-render the 19 ReadyAsset assets with
`blender -b <file> --python tools/site/blender_render_one.py`.
Requirements: at least 2 GiB free VRAM, no concurrent ComfyUI/UE workload holding high VRAM,
`--dry-run` first, then `--only` one mesh before the rest.

## 9. Redaction and privacy

The build redacts secret patterns (`api_key/token/secret/password=`, `sk-*`, `nvapi-*`, `AIza*`,
`ghp_*`, `xox*-*`, `Bearer *`), email addresses, Indonesian phone numbers, and absolute `$HOME`
paths. Pages do not display absolute paths. `--check` scans the generated output and fails when a
secret pattern is found.

## 10. Backup/restore

The daily automation (02:00) runs `bin/backup_push.sh`, which also mirrors `site/content` and
`site/media` into the private repo (thumbnails are excluded because they are rebuilt) and writes
restore commands into `MANIFEST.md`.

Content and media are not tracked by git. Manual backup:

```bash
tar -C "$HOME/.ai-station" -czf "$HOME/runtime/backups/site-$(date +%F).tar.gz" site/content site/media
```

Restore: extract into `$HOME/.ai-station/`, then run `python3 tools/site/build.py`. `site/README.md`
is restored by `--init` if missing.

## 11. License and boundaries

The site is local content; this build does not publish it. Do not add a CDN or a public port. The
upstream Blender MCP code is AGPL: do not merge it into this BSL repository.
