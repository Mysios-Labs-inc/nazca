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

## Models today (P1 — descriptive, what nazca drives now)

### Image
| shorthand | backend | ops | notes |
|---|---|---|---|
| `nano-banana` | vertex/gemini | t2i, i2i, compose | 2.5-flash-image; ref count unpinned |
| `nano-banana-2` | vertex/gemini | t2i, i2i, compose | 3.1-flash-image |
| `nano-banana-pro` | vertex/gemini | t2i, i2i, compose | 3-pro-image; **up to 14 refs**, legible text |
| `imagen-4-fast` | vertex/imagen | t2i | **t2i only** — rejects refs |
| `imagen-4` | vertex/imagen | t2i | t2i only |
| `imagen-3` | vertex/imagen | t2i | t2i only |
| `flux-schnell` | fal | t2i, i2i | **single ref only**; fal id unverified |
| `flux-2-dev` | fal | t2i, i2i | single ref only; fal id unverified |
| `seedream` | modelark | t2i, i2i, compose | up to 14 refs; needs BytePlus activation; `group` (N/call) not wired |
| `upscale` | fal | upscale | clarity-upscaler, `--scale 1-4`, $0.03/MP (verified id) |
| `rmbg` | fal | bg_remove | birefnet/v2 → transparent PNG, free compute (verified id) |

### Video
| shorthand | backend | ops | notes |
|---|---|---|---|
| `veo-3.1-lite` | vertex | t2v, i2v, keyframe | `--start` optional (t2v) / one frame (i2v) / two (keyframe) |
| `veo-3.1-fast` | vertex | t2v, i2v, keyframe | |
| `veo-3.1` | vertex | t2v, i2v, keyframe | |
| `seedance-2-fast` | fal | i2v | fal id unverified |
| `wan-2.6` | fal | **t2v** | fal id is `.../text-to-video`; reachable now (no `--start`) |
| `seedance-pro` | modelark | i2v | needs BytePlus activation |
| `seedance-lite` | modelark | i2v | needs BytePlus activation |

## Mismatches (1 & 2 fixed in P2)

1. ✅ **`nazca video` no longer forces `--start`.** Omit it for pure `t2v` (wan-2.6,
   and Veo's start-less body); one frame → `i2v`; two → `keyframe`. The op is
   inferred and validated against the model.
2. ✅ **Imagen + `--ref` is rejected up front**, not mid-dispatch: the CLI infers
   the op from flags and checks `op ∈ caps.ops`, erroring with a suggested model.
3. ⬜ **Seedream `group` mode** (1 call → up to 15 related images) is a real distinct
   axis and is still unwired.

**P3 (done): `upscale` + `bg_remove`** wired on fal (clarity-upscaler, birefnet/v2)
via the positional `SOURCE` slot — `nazca image photo.png --upscale|--rmbg`.
Remaining modify ops (`inpaint`/`outpaint`, and video `v2v`/`reframe`/`extend`)
are next.

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
- 🟡 **P3** — image modify ops via the `SOURCE` slot: `upscale`/`bg_remove` **done**
  (fal clarity-upscaler / birefnet, verified ids); `inpaint`/`outpaint` (mask/region)
  next.
- ⬜ **P4** — video-to-video: `v2v`/`reframe`/`extend` (largest lift).
