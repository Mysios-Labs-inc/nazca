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
- **No API keys for Google models.** Vertex AI via `gcloud` — short-lived OAuth token minted per call, nothing persisted. Opt into a non-Google backend (e.g. fal) and you need that provider's key; keep it in your shell profile / secrets manager, never in a script or CLI flag.
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

## Auth

### Google models (default — no API key)

Everything Vertex runs through your gcloud credentials. No keys, nothing persisted.

```bash
gcloud auth login
```

Defaults target project `your-gcp-project`, region `us-central1`. Override via env:

| env var | default | purpose |
|---|---|---|
| `VERTEX_PROJECT` | `your-gcp-project` | GCP project (billing/credits) |
| `VERTEX_LOCATION` | `us-central1` | default region (some models are `global`) |
| `VEO_MODEL` | `veo-3.1-fast-generate-001` | default video model |
| `VEO_POLL_INTERVAL` / `VEO_POLL_MAX_TRIES` | `15` / `60` | video/fal polling |

### fal.ai (opt-in — long-tail models only)

Models like FLUX schnell/dev, Seedance, and Wan have no Google first-party path;
fal.ai gives them all under one key. Google models **stay on Vertex** (direct is cheaper).

```bash
export FAL_KEY=<your-key>   # fal.ai dashboard → API keys
```

Keep `FAL_KEY` in your shell profile or a secrets manager (`~/.zshrc`,
`~/.profile`, 1Password, etc.). **Never** pass it as a CLI flag (shell history)
or commit it to a file. A Vertex-only run never reads `FAL_KEY`.

| `--model` | fal model id | notes |
|---|---|---|
| `flux-schnell` | fal-ai/flux/schnell | fastest FLUX; ~$0.003/MP |
| `flux-2-dev` | fal-ai/flux/dev | FLUX 2 dev; higher quality |
| `seedance-2-fast` | fal-ai/bytedance/seedance/v2/lite | video; tier/resolution-dependent pricing |
| `wan-2.6` | fal-ai/wan/v2.6/text-to-video | video; ~$0.05/s |

> **Note:** fal model IDs and pricing are subject to change — verify against
> [fal.ai/models](https://fal.ai/models) before spending. Use `--dry-run` first.

### ByteDance ModelArk (opt-in, optional cost path — NOT default)

An alternative direct path to ByteDance's Seedream (image) and Seedance (video) models.
**This is an optional cost path, not a default.** Vertex and fal behavior is entirely unchanged.

> **CAUTION: ModelArk API IDs, endpoints, and schemas are UNVERIFIED. Use `--dry-run` only until you
> have benchmarked ModelArk direct against `seedance-2-fast` via fal at your real resolution/tier.
> The "~25% cheaper" claim is unverified — do not assume cost savings before measuring.**

```bash
export ARK_API_KEY=<your-key>   # ByteDance ModelArk → API keys (ark.bytepluses.com)
```

Keep `ARK_API_KEY` in your shell profile or a secrets manager. **Never** pass it as a CLI flag
or commit it to a file. A Vertex-only or fal-only run never reads `ARK_API_KEY`.

| `--model` | ModelArk model id | type | notes |
|---|---|---|---|
| `seedream` | seedream-4-0 | image | text-to-image; ID UNVERIFIED |
| `seedance-pro` | seedance-3-pro | video | async task; ID UNVERIFIED |
| `seedance-lite` | seedance-3-lite | video | async task; ID UNVERIFIED |

**Known caveats (verify against current ModelArk docs before spending):**
- **720p cap** on video output — upscale in post if 1080p is required.
- **Close-up-face privacy flag** — close-up face frames may be refused by the API.
- **Billing-dashboard lag** — ModelArk billing may not reflect charges in real time.
- **~25% cheaper claim is UNVERIFIED** — Seedance pricing is tier/resolution-dependent
  (Fast ~$0.022/s vs Pro ~$0.247/s observed); measure at your target resolution before
  switching from fal.
- **Endpoints and model IDs are UNVERIFIED** — use `--dry-run` only until confirmed against
  official [ModelArk docs](https://ark.bytepluses.com).

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

# 720p Lite model — ~2x cheaper, perfect for mobile/social
mediagen video -o clip.mp4 -s start.png -p "..." --model veo-3.1-lite

# start + end frame (keyframe — only when the two frames are tight variants)
mediagen video -o clip.mp4 -s a.png --end b.png -p "the skewer lifts off the grill"

mediagen video -o clip.mp4 -s start.png -p "..." --dry-run   # request JSON, no credits
```

| `--model` | resolution | $/sec (720p) | notes |
|---|---|---|---|
| `veo-3.1-lite` | 720p | $0.05 | ~2x cheaper, mobile/social |
| `veo-3.1-fast` *(default)* | 720p | $0.10 | smooth interpolation |
| `veo-3.1` | 720p/1080p | $0.20 | highest quality (video-only) |

Prices are official Google Cloud rates (verified 2026-06-18). `--audio` costs extra
— the full `veo-3.1` is $0.40/s with audio (2x its silent rate); clips are silent by
default, so this only applies if you pass `--audio`.

Options: `-o/--out`, `-s/--start`, `-p/--prompt`, `--end`, `--model`
(default `veo-3.1-fast`), `--duration` (4/6/8), `--aspect`
(`9:16`/`16:9`), `--resolution` (`720p`/`1080p`), `--audio`, `--dry-run`.

Notes: clips are **silent by default** (add audio in post). Keyframe interpolation
**morphs** if the end frame isn't a tight variant of the start — use single-frame
for camera moves.

---

## Cost tiers (`--tier cheap|premium`)

Instead of memorizing model ids, pass `--tier cheap` or `--tier premium`. The flag
is ignored when `--model` is given (explicit model always wins).

```bash
mediagen video -o clip.mp4 -s start.png -p "push-in" --tier cheap    # → veo-3.1-lite
mediagen video -o clip.mp4 -s start.png -p "push-in" --tier premium  # → veo-3.1
mediagen image -o out.png -p "dish restyle" --tier cheap              # → nano-banana
mediagen image -o out.png -p "dish restyle" --tier premium            # → nano-banana-pro
```

Tier defaults are **Vertex-direct** (direct-first rule — Google models never routed through fal).

| command | `--tier cheap` | `--tier premium` | notes |
|---|---|---|---|
| `image` | `nano-banana` ~$0.039/img | `nano-banana-pro` ~$0.134/img @2K | pro: legible text, up to 14 refs |
| `video` | `veo-3.1-lite` $0.05/s (720p) | `veo-3.1` $0.20/s (720p) | lite: ~4x cheaper, great for mobile/social |

Other models and rough prices (official Google Cloud, verified 2026-06-18):

| model | $/unit | tier |
|---|---|---|
| `imagen-4-fast` | $0.02/img | cheap |
| `nano-banana` (gemini-2.5-flash-image) | ~$0.039/img | cheap |
| `imagen-4` | $0.04/img | premium |
| `nano-banana-pro` (gemini-3-pro-image) | ~$0.134/img @2K | premium |
| `veo-3.1-lite` | $0.05/s | cheap |
| `veo-3.1-fast` | $0.10/s (720p) / $0.12/s (1080p) | cheap |
| `veo-3.1` | $0.20/s video-only; $0.40/s with audio | premium |
| fal/FLUX schnell | ~$0.003/MP | cheap |
| Seedance (via fal) | tier/resolution-dependent — verify per call | — |

> Audio doubles Veo 3.1's rate ($0.20 → $0.40/s). Seedance pricing varies by
> tier and resolution — do not assume a single $/s figure.

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
├── cli.py                  click entrypoint: `image`, `video`
├── backends/
│   ├── __init__.py         BACKENDS registry + get_backend()
│   ├── base.py             Backend interface (auth_token, build_url, post, encode)
│   ├── vertex.py           Vertex AI: gcloud OAuth token + REST
│   ├── fal.py              fal.ai: FAL_KEY + queue submit→poll→download
│   └── modelark.py         ByteDance ModelArk: ARK_API_KEY + REST (UNVERIFIED — dry-run only)
├── vertex.py               back-compat shim (re-exports from backends/vertex.py)
├── image.py                Gemini/Imagen (Vertex), FLUX (fal), Seedream (ModelArk) dispatch
├── video.py                Veo (Vertex), Seedance/Wan (fal), Seedance (ModelArk) dispatch
└── config.py               env-overridable defaults (incl. FAL_KEY, ARK_API_KEY, optional)
docs/vertex-models.md       functionally-probed Vertex model inventory
```

Routing is **data, not code**: a `backend` field in the `MODELS` map selects the
provider. Adding a model is a one-line entry; adding a provider is a new `Backend`
subclass + one key in `BACKENDS`. Auth is lazy — a Vertex-only run never reads
`FAL_KEY` or `ARK_API_KEY`.

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
