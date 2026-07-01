# Media generation modalities — the map

The modality of a model is **`inputs → output`**. It's the axis that decides
routing, which CLI flags are even legal, and where validation belongs. This doc
is the human-facing version of `src/nazca/capabilities.py` (the machine-readable
descriptor). Keep them in sync — a test asserts every model has a `Caps` entry.

## Operation vocabulary

A closed set. Adding a modality = a new entry here + a body-builder on the
backends that support it, not a new ad-hoc code path.

### Image out
| op | inputs → output | meaning |
|---|---|---|
| `t2i` | text → image | text-to-image |
| `i2i` | text + ref[1] → image | restyle / edit from one reference |
| `compose` | text + ref[2..N] → image | multi-subject blend |
| `inpaint` | source + mask + text → image | edit a masked region |
| `outpaint` | source (+text) → image | extend the canvas |
| `upscale` | source → image | enhance / increase resolution |
| `bg_remove` | source → image+alpha | cutout / transparent background |

### Video out
| op | inputs → output | meaning |
|---|---|---|
| `t2v` | text → video | text-to-video |
| `i2v` | text + start → video | animate from a start frame |
| `keyframe` | text + start + end → video | first-last frame interpolation |
| `v2v` | source video (+text) → video | restyle / motion-transfer |
| `reframe` | source video + aspect → video | change aspect ratio |
| `extend` | source video → video | lengthen a clip |

### Audio out
`tts`, `music`, `dub` — **named but deliberately out of scope** for nazca today.

## Ref roles (P1 — descriptive)

A second axis on `i2i`/`compose`: not just *that* a reference was passed, but *what it
is*. Today refs are untyped/positional (count alone picks `i2i` vs `compose`, and the
backend blends them). `REF_ROLES` is the closed vocabulary that will change that:

| role | meaning |
|---|---|
| `ref` | generic / untyped — **current behavior**, the default for a bare `--ref x.png` |
| `subject` | the primary thing to keep or edit (source content) |
| `style` | match this aesthetic / look, not its content |
| `identity` | this face / character / wordmark — preserve identity |

`Caps.ref_roles` declares which roles each model accepts: every ref-capable model takes
the generic `ref`; the multi-semantic-ref models (nano-banana family, `seedream`,
`gpt-image-2`) additionally accept the typed roles. Single-ref FLUX is generic-only.

**CLI surface (live):** `--ref PATH:role`, repeatable — e.g.
`--ref hero.png:subject --ref look.png:style --ref face.png:identity`. A bare `--ref x.png`
is untyped (role `ref`) and behaves exactly as before. Unknown roles, and typed roles on a
model that doesn't accept them, are rejected up front.

**How a role changes output:** no backend exposes a per-ref role field, so roles steer the
model through the **prompt** — `role_annotation()` appends an ordered legend ("image 1 is
the subject…; image 2 is a style reference…") to the prompt before dispatch. Untyped refs
add nothing, so the prompt sent is byte-identical to today. This is provider-agnostic (every
image backend forwards the prompt). Backends do **not** yet treat the images differently at
the API level — that's a later, per-provider step where native role fields exist.

## Models today (P1 — descriptive, what nazca drives now)

### Image
| shorthand | backend | ops | notes |
|---|---|---|---|
| `nano-banana` | vertex/gemini | t2i, i2i, compose | 2.5-flash-image; ref count unpinned |
| `nano-banana-2` | vertex/gemini | t2i, i2i, compose | 3.1-flash-image |
| `nano-banana-2-lite` | vertex/gemini | t2i, i2i | 3.1-flash-lite-image; **single ref only**, no compose, fastest/cheapest tier |
| `nano-banana-pro` | vertex/gemini | t2i, i2i, compose | 3-pro-image; **up to 14 refs**, legible text |
| `imagen-4-fast` | vertex/imagen | t2i | **t2i only** — rejects refs |
| `imagen-4` | vertex/imagen | t2i | t2i only |
| `imagen-3` | vertex/imagen | t2i | t2i only |
| `flux-schnell` | fal | t2i, i2i | **single ref only**; fal id unverified |
| `flux-2-dev` | fal | t2i, i2i | single ref only; fal id unverified |
| `seedream` | modelark | t2i, i2i, compose | up to 14 refs; needs BytePlus activation; `group` (N/call) not wired |
| `gpt-image-2` | openai | t2i, i2i, compose | **≤5 refs** via `/images/edits`; legible text/ads; `--quality` lever; token-billed; slow (~30–105s) |
| `upscale` | fal | upscale | clarity-upscaler, `--scale 1-4`, $0.03/MP (verified id) |
| `rmbg` | fal | bg_remove | birefnet/v2 → transparent PNG, free compute (verified id) |
| `inpaint` | fal | inpaint | flux-pro/v1/fill, `--mask` (white=edit) + prompt, $0.05/MP (verified id) |
| `outpaint` | fal | outpaint | flux-2-pro/outpaint, `--expand` px/side, no prompt/mask (verified id) |

### Video
| shorthand | backend | ops | notes |
|---|---|---|---|
| `veo-3.1-lite` | vertex | t2v, i2v, keyframe | `--start` optional (t2v) / one frame (i2v) / two (keyframe) |
| `veo-3.1-fast` | vertex | t2v, i2v, keyframe | |
| `veo-3.1` | vertex | t2v, i2v, keyframe | |
| `omni-flash` | vertex | t2v, i2v, ref2v, v2v | gemini-omni-flash-preview; `:generateContent` not `:predictLongRunning` — synchronous, no poll; fixed ~10s/720p+audio, ignores duration/resolution/audio/aspect flags (Vertex rejects `videoConfig.aspectRatio`); ref2v verified live to 2 imgs (`--ref`, max_refs=6 per Google's docs example, untested beyond 2); v2v takes a LOCAL file via `--v2v SOURCE` (opposite of fal's URL convention — `edit_video` branches on `spec.api == "omni"`); all verified against live Vertex calls 2026-06-30 |
| `seedance-2-fast` | fal | i2v | fal id unverified |
| `wan-2.6` | fal | **t2v** | fal id is `.../text-to-video`; reachable now (no `--start`) |
| `seedance-pro` | modelark | i2v | needs BytePlus activation |
| `seedance-lite` | modelark | i2v | needs BytePlus activation |
| `reframe` | fal | reframe | luma ray-2/reframe; SOURCE = **video URL**, `--aspect` target (verified id+field) |
| `v2v` | fal | v2v | wan-vace-apps/video-edit; SOURCE video URL + prompt (`video_url` field unverified) |
| `extend` | fal | extend | pixverse/extend; SOURCE video URL + prompt, `--duration 5|8` (`video_url` field unverified) |

## Mismatches (1 & 2 fixed in P2)

1. ✅ **`nazca video` no longer forces `--start`.** Omit it for pure `t2v` (wan-2.6,
   and Veo's start-less body); one frame → `i2v`; two → `keyframe`. The op is
   inferred and validated against the model.
2. ✅ **Imagen + `--ref` is rejected up front**, not mid-dispatch: the CLI infers
   the op from flags and checks `op ∈ caps.ops`, erroring with a suggested model.
3. ⬜ **Seedream `group` mode** (1 call → up to 15 related images) is a real distinct
   axis and is still unwired.

**P3 (done): all four image modify ops** wired on fal via the positional `SOURCE`
slot — `upscale` (clarity-upscaler), `bg_remove` (birefnet/v2), `inpaint`
(flux-pro/v1/fill, `--mask` + prompt) and `outpaint` (flux-2-pro/outpaint,
`--expand`). Remaining: video `v2v`/`reframe`/`extend` (P4).

## CLI surface (decided: infer op from flags)

The command stays `nazca image` / `nazca video`; the op is inferred from the flags
you pass, then validated against the model's `ops`. A positional `SOURCE` is the
image/video being *modified* (inpaint/outpaint/upscale/bg_remove, v2v/reframe/
extend), kept distinct from `--ref` (style/subject references). `--prompt` becomes
optional for ops that don't need it (upscale, bg_remove, reframe).

```
# image
nazca image -p "..."                       # t2i
nazca image -p "..." --ref a.png           # i2i
nazca image -p "..." --ref a.png --ref b.png   # compose
nazca image SOURCE --mask m.png -p "..."   # inpaint
nazca image SOURCE --upscale               # upscale (no prompt)
# video
nazca video -p "..."                       # t2v
nazca video -p "..." --start s.png         # i2v
nazca video -p "..." --start s.png --end e.png  # keyframe
nazca video SOURCE -p "restyle ..."        # v2v
```

## Roadmap

- ✅ **P1** — `Caps` descriptor + this doc; encode existing models; `nazca models`
  shows ops. No behavior change.
- ✅ **P2** — derive op from flags + validate against `CAPS`; make `--start`
  optional (unblocks `t2v`); reject imagen+ref up front. Fixes mismatches #1, #2.
- ✅ **P3** — all four image modify ops via the `SOURCE` slot: `upscale`,
  `bg_remove`, `inpaint` (`--mask` + prompt), `outpaint` (`--expand`) — fal, all ids
  verified.
- ✅ **P4** — all three video-edit ops via a positional `SOURCE` (a **video URL** —
  fal needs a URL, not an inlined data-URI; `gs://` unsupported): `reframe` (luma
  ray-2, verified field), `v2v` (wan-vace-apps/video-edit) and `extend`
  (pixverse/extend). For v2v/extend the `video_url` input field is fal's
  convention but **UNVERIFIED live** — dry-run safe; verify with a real call
  before spend (same posture as the existing fal video ids). Local-file SOURCE
  (→ fal-storage upload) is the one remaining follow-up.
