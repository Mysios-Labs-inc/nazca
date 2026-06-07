# mediagen

A thin, **agent-driven CLI** for AI **image** and **video** generation on
**Google Vertex AI**. Two commands, each does one thing and prints the output
path. Claude (or you) orchestrates — mediagen is just clean, reliable access to
the models.

```bash
mediagen image -o dish.png --ref photo.jpg -p "restyle: warm amber parrilla grade"
mediagen video -o clip.mp4 -s start.png --end end.png -p "slow push-in, embers glow"
```

---

## Why this exists

We kept reaching for heavier options — a full content framework, an MCP server,
SaaS image tools — when what actually worked was: **the agent writes the prompt,
judges the result, and runs a small command.** That's `mediagen`. It is the
"hands" (instruments). The "how" (brand rules, prompt recipes) belongs in an
[Agent Skill](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills);
posting belongs in MCP. mediagen stays deliberately small.

Design choices:
- **One auth path:** Vertex AI via `gcloud`. No API keys, no provider SDKs.
- **Two tiny dependencies:** `click` + `Pillow`.
- **Stdlib HTTP** (`urllib`) — the whole thing is a few hundred lines.
- **`--dry-run`** on both commands prints the exact request before spending anything.

---

## Install

```bash
git clone https://github.com/MRCORD/mediagen.git
cd mediagen
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```

## Auth — one path for everything

Everything runs through **Vertex AI** with your gcloud credentials. No keys.

```bash
gcloud auth login
```

Defaults target project `florece-492623`, region `us-central1`. Override via env:

| env var | default | purpose |
|---|---|---|
| `VERTEX_PROJECT` | `florece-492623` | GCP project (billing/credits) |
| `VERTEX_LOCATION` | `us-central1` | default region (some models are `global`) |
| `VEO_MODEL` | `veo-3.1-fast-generate-001` | default video model |
| `VEO_POLL_INTERVAL` / `VEO_POLL_MAX_TRIES` | `15` / `60` | video polling |

---

## `mediagen image`

Generate an image, or **restyle a real photo** with `--ref` (image-to-image —
the brand-accurate path: keep the real dish, change the look).

```bash
# restyle a real product photo (recommended)
mediagen image -o out.png --ref dish.jpg -p "warm amber/ochre grade, side-back key, honey-stained wood"

# multiple references (gemini-3-pro-image accepts up to 14 — dish + style refs)
mediagen image -o out.png --model nano-banana-pro --ref dish.jpg --ref style.jpg -p "..."

# fresh text-to-image (no source) via Imagen
mediagen image -o out.png --model imagen-4 -p "a rustic Peruvian parrilla scene, 9:16"

# inspect the request without calling the API
mediagen image -o out.png --ref dish.jpg -p "..." --dry-run
```

| `--model` | id | region | `--ref`? | notes |
|---|---|---|---|---|
| `nano-banana` *(default)* | gemini-2.5-flash-image | us-central1 | ✅ | fast, cheap |
| `nano-banana-3` | gemini-3.1-flash-image | global | ✅ | newer flash (GA) |
| `nano-banana-pro` | gemini-3-pro-image | global | ✅ (≤14) | highest fidelity |
| `imagen-4` / `imagen-4-fast` / `imagen-3` | imagen-4.0-* / 3.0 | us-central1 | ❌ | text-to-image only |

Options: `-o/--out`, `-p/--prompt`, `--ref` (repeatable), `--model`, `--aspect`
(default `9:16`), `--dry-run`. Full availability: [`docs/vertex-models.md`](docs/vertex-models.md).

---

## `mediagen video`

Vertex **Veo 3.1** image-to-video (ported from a battle-tested script). Start
frame + **optional end frame** (keyframe interpolation). Submit → poll → download.

```bash
# single start frame + motion (best for camera moves: push-in, pull-back)
mediagen video -o clip.mp4 -s start.png -p "slow cinematic push-in, embers glow"

# start + end frame (keyframe — only when the two frames are tight variants)
mediagen video -o clip.mp4 -s a.png --end b.png -p "the skewer lifts off the grill"

mediagen video -o clip.mp4 -s start.png -p "..." --dry-run   # request JSON, no credits
```

Options: `-o/--out`, `-s/--start`, `-p/--prompt`, `--end`, `--model`
(default `veo-3.1-fast-generate-001`), `--duration` (4/6/8), `--aspect`
(`9:16`/`16:9`), `--resolution` (`720p`/`1080p`), `--audio`, `--dry-run`.

Notes: clips are **silent by default** (add audio in post). Keyframe interpolation
**morphs** if the end frame isn't a tight variant of the start — use single-frame
for camera moves.

---

## Workflow rule (locked)

- **mediagen produces CLEAN media only** — food/product restyles + video, no
  baked-in text. Prompt for clean images and keep the bottom third calm/darker.
- **All text + brand overlays are done in Figma** (master templates + real
  wordmark). Do *not* prompt the image model to render captions/headlines/logos —
  even though `gemini-3-pro-image` *can* render legible text, we don't use it for that.

---

## Architecture

```
src/mediagen/
├── cli.py       click entrypoint: `image`, `video`
├── vertex.py    shared: gcloud token, REST POST, image encode, region/global URLs
├── image.py     Gemini (generateContent + --ref) and Imagen (predict, t2i) paths
├── video.py     Veo predictLongRunning + fetchPredictOperation polling
└── config.py    env-overridable defaults
docs/vertex-models.md   functionally-probed model inventory for the project
```

Both commands share `vertex.py`: one auth + REST + image-encoding path. Adding a
model is usually a one-line entry in the `MODELS` map in `image.py` (or a new
`--model` value for video).

---

## Limitations / not in scope

- No overlay/captioning (Figma does that), no posting (MCP/Postiz does that), no
  brand config or autopilot (a Skill does that).
- `image` covers Gemini + Imagen; no Imagen *edit* model wired yet
  (`imagen-3.0-capability-001` would add Imagen ref-edits).
- `video` is synchronous (polls inline). Full `veo-3.1-generate-001` is available
  but only the fast model is heavily exercised.
- Model IDs change often — re-probe with the recipe in `docs/vertex-models.md`.

## License

Private / internal tooling.
