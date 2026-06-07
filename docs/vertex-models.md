# Vertex AI model inventory — your-gcp-project

Functionally probed 2026-06-06 (real API calls, not metadata reads — the
metadata GET returns 403 here regardless, so only a live call is trustworthy).
Status: ✅ generated successfully · ❌ 404 not available · ⏳ documented, not live-probed.

## Image — Gemini family (`:generateContent`, supports `--ref` image-to-image)
| shorthand | model id | region | --ref | status |
|---|---|---|---|---|
| `nano-banana` (default) | `gemini-2.5-flash-image` | us-central1 | yes | ✅ |
| `nano-banana-pro` | `gemini-3-pro-image-preview` | **global** | yes | ✅ (404 in us-central1 — global only) |
| — | `gemini-2.0-flash-preview-image-generation` | us-central1 | — | ❌ 404 |

## Image — Imagen family (`:predict`, text-to-image ONLY, no `--ref`)
| shorthand | model id | region | status |
|---|---|---|---|
| `imagen-4-fast` | `imagen-4.0-fast-generate-001` | us-central1 | ✅ |
| `imagen-4` | `imagen-4.0-generate-001` | us-central1 | ✅ |
| `imagen-3` | `imagen-3.0-generate-002` | us-central1 | ✅ |
| (edit/customization) | `imagen-3.0-capability-001` | us-central1 | ⏳ not wired (would add Imagen ref-edit) |

## Video — Veo (`:predictLongRunning`, start + optional end frame)
| shorthand | model id | region | status |
|---|---|---|---|
| `veo-fast` (default) | `veo-3.1-fast-generate-001` | us-central1 | ✅ (made real DG clips) |
| `veo` | `veo-3.1-generate-001` | us-central1 | ⏳ documented; higher fidelity, not live-probed (cost) |

## Text — Gemini (`:generateContent`) — for prompt-writing / agents, not used by mediagen
| model id | region | status |
|---|---|---|
| `gemini-2.5-pro` | us-central1 | ✅ |
| `gemini-2.5-flash` | us-central1 | ✅ |
| `gemini-2.5-flash-lite` | us-central1 | ✅ |
| `gemini-3-flash-preview` | global | ✅ |
| `gemini-3-pro-preview` | global | ❌ 404 (not provisioned) |

## How to use them (guidance)
- **Restyle a real product photo** → `nano-banana` (default) or `nano-banana-pro`
  with `--ref`. Pro is higher fidelity but lives in `global`. This is the
  primary path for food/product content (keep the real dish, change the look).
- **Fresh text-to-image** (no source photo) → `imagen-4` (fidelity) or
  `imagen-4-fast` (speed). NOTE: text-to-image invents content (e.g. it added
  off-brand bell peppers in testing) — prefer restyle for brand accuracy.
- **Video** → `mediagen video` (Veo 3.1 fast); single start frame for camera
  moves, start+end for tight keyframe transforms.

## Re-running this inventory
```bash
# functional probe pattern (gemini image):
TOKEN=$(gcloud auth print-access-token)
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"contents":[{"role":"user","parts":[{"text":"red dot"}]}],"generationConfig":{"responseModalities":["IMAGE"]}}' \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/your-gcp-project/locations/us-central1/publishers/google/models/<MODEL>:generateContent"
```
Model IDs change often — re-probe before trusting. global region uses host
`aiplatform.googleapis.com` + `locations/global`.
