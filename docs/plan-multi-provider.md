# Plan: multi-provider + cost-tier mediagen

**Status:** ✅ SHIPPED (2026-06-19) · **Created:** 2026-06-18 · **Owner:** MRCORD
**Source research:** [`claudedocs/research_media-providers-and-cheap-models_2026-06-18.md`](../claudedocs/research_media-providers-and-cheap-models_2026-06-18.md)

> **Delivered in PRs #1–#7:** Veo 3.1 Lite (#1), `backends/` seam (#2), fal backend (#3),
> `--tier` cost routing (#4), ByteDance ModelArk (#5), `~/.config` credential store (#6),
> interactive `mediagen login` (#7). Phases 0–3 + the ModelArk option are all merged to `main`.
> **Remaining gate:** fal/ModelArk model IDs and request schemas are dry-run-validated only —
> a real-key smoke test is still required before production spend (see §"Phase 4").

## Goal

Keep mediagen the thin "hands" (two commands, prints a path, `--dry-run`, stdlib HTTP, no provider SDKs)
**while** spanning more than one provider and exposing a cheap/premium lever the agent can pull.

Today mediagen is Vertex-only (gcloud auth). The market runs **14 models on average** per deployment;
the seam to get there already exists (`MODELS` map + shared `vertex.py`). This plan generalizes that seam.

## Non-goals (preserve the "stays small" rule)

- No provider SDKs — every backend is `urllib` + that provider's REST/queue API.
- No overlay/captioning, no posting, no brand config (Skills/Figma/MCP own those — unchanged).
- No async/concurrency rewrite — video stays synchronous submit→poll→download.
- No web UI, no server. Still a CLI.

## Guiding constraints

- `--model` UX stays identical; **routing is data, not code** (a field in the `MODELS` map).
- Each backend fails loudly with a clear `*Error` if its credential/CLI is missing (like `gcloud` does today).
- `--dry-run` works on every backend and never spends.
- Defaults stay Vertex so existing usage is byte-for-byte unchanged.

---

## Cost strategy — DIRECT-FIRST, aggregator for the long tail

The fact-check (Google Cloud official pricing, 2026-06-18) settled the "is an aggregator cheaper?" question:
**no — for any model you can reach first-party, direct is the price floor.** mediagen is *already* on that
floor for everything it does (Google models, direct on Vertex, plus any GCP credits an aggregator can't pass
through). So the routing rule is **not** "everything through fal":

```
  Model reachable first-party?
    ├─ YES → go DIRECT (cheapest, credits apply)
    │        • Google (Veo / Imagen / Gemini-image) → Vertex   ← already here, don't reroute
    │        • ByteDance (Seedance / Seedream)       → ModelArk ← only at volume (Phase 4)
    └─ NO  → use fal (FLUX, Flux 2, Wan, Kling, …)   ← long tail; margin is pennies vs. onboarding N vendors
```

Implications baked into the phases below:
- **Never route Google models through fal** — it only *adds* margin. fal is for models we have no first-party account for.
- **fal (Phase 2) is about breadth, not beating Vertex on price** — it unlocks the long tail under one key.
- **ByteDance direct (Phase 4) stays optional** — Seedance pricing is tier/resolution-dependent and the
  "direct is ~25% cheaper" claim was **retracted as unverified**; measure at target resolution before adding `ARK_KEY`.
- The biggest *verified* cost win needs no new provider at all: **Phase 0** (`veo-3.1-fast` $0.10/s → `veo-3.1-lite` $0.05/s, both Vertex-direct).

---

## Auth & credentials (BYOK)

Today mediagen has a rare property: **zero stored keys**. `gcloud auth print-access-token` mints a
short-lived (~1h) OAuth token per call (`vertex.py`); nothing is persisted, nothing lives in the repo.
That is "bring your own **Google Cloud project**", not BYOK. Going multi-provider introduces real BYOK
secrets (`FAL_KEY`, `ARK_API_KEY`). The seam must be designed so this stays clean, not so it sprawls.

**Design principle — auth belongs to the backend, lazily:**
> A backend only demands its credential when one of *its* models is actually selected.

So a Vertex-only run never looks for `FAL_KEY` and behaves exactly as today — **no regression for the
no-key path**. The fal key is only required the moment you pass a fal model. Each backend raises a clear,
specific `*Error` when its credential is missing (mirroring today's `VertexError` when gcloud is absent).

**Rules (non-negotiable, match the existing no-secrets-in-repo posture):**
- Keys come from **env vars only** (`FAL_KEY`, `ARK_API_KEY`), read in `config.py` as **optional, no default**.
- **Never** a CLI flag (leaks into shell history) and **never** a file committed to the repo.
- mediagen **never persists** a secret — read env → `Authorization` header → forget. Same lifecycle as the gcloud token, just a different source.

**Credential matrix:**

| backend | credential | kind | TTL | who pays |
|---|---|---|---|---|
| `vertex` (default) | `gcloud` token | short-lived OAuth, minted per call | ~1h | your GCP project |
| `fal` | `FAL_KEY` env | long-lived API key (BYOK) | until rotated | your fal account |
| `modelark` | `ARK_API_KEY` env | long-lived API key (BYOK) | until rotated | your ByteDance account |

**Tradeoff to accept:** the README's "no API keys anywhere" claim becomes "**no keys unless you opt into a
non-Google backend**" — soften that line when Phase 2 lands. Long-lived keys don't self-expire, so the
README should advise keeping them in a shell profile / secrets manager, never in scripts.

**Implementation hooks:**
- Phase 1: `auth_token()` is part of the backend protocol and is **lazy** (called only on dispatch).
- Phase 2/4: `config.py` gains `FAL_KEY = os.getenv("FAL_KEY")` / `ARK_API_KEY = os.getenv("ARK_API_KEY")` (optional).
- Phase 3 (optional): a `--check` / `doctor` note reporting which credentials are present for the models you have, so failures are self-explanatory.

### Stripe Projects — the "wallet of envs" option (watch, not adopt yet)

[Stripe Projects](https://projects.dev/) (`projects.dev`, CLI `stripe projects add <provider>/<service>`)
is the closest off-the-shelf answer to our credential + spend problem. It provisions third-party services
**and holds their credentials**, injecting them as scoped env vars — which is exactly the layer our
hand-rolled env-var approach approximates. Relevant features (Stripe blog, 2026):
- **Scoped credentials** minted/held per service (vs. raw keys in your shell profile).
- **Per-provider spend limits** — "tighter limits on an AI model provider" — = our Phase 3 cost guardrail, for free.
- **Unified cost-per-project** across all providers — one spend view.
- **Named isolated environments** (dev/staging/prod); agents default to `development`, can't touch prod.
- Distributed as an **agent skill / CLI** (Hermes, Factory Droids, Warp).

**Blocker (verified 2026-06-18):** the [49-provider catalog](https://projects.dev/providers) does **NOT**
include our media backends — **no fal.ai, no ByteDance ModelArk, no Google Vertex.** Closest AI entries are
**OpenRouter** (LLM routing, not the image/video models we need), **Hugging Face**, **HeyGen** (talking-head
video), **ElevenLabs** (audio). So Projects **cannot hold our keys today** — adopting it now buys nothing for
fal/ModelArk/Vertex, and adds a Stripe dependency that fights mediagen's "two tiny deps, no SDKs" identity.

**Decision:** keep the **plain env-var** approach for Phases 1–4. It is forward-compatible — Stripe Projects
(and similar managers) work by *injecting* env vars / scoped creds, so our backends need no change to benefit
later. **Do not build a bespoke spend-cap/credential-vault layer.** Revisit Projects only if (a) fal / Vertex /
ModelArk land in its catalog, or (b) we decide to route media through a provider that *is* in the catalog
(e.g. OpenRouter, if/when its image+video coverage matches our needs).

> Not to be confused with Stripe's *other* agent launch — **Link wallet / Issuing for agents** (Stripe
> Sessions, Apr 2026) — which gives agents scoped one-time-use **virtual cards to buy things**. That's for
> an agent making purchases, not for managing our fixed API keys → out of scope for mediagen.

---

## Phases

### Phase 0 — Quick win: Veo 3.1 Lite (no refactor, no new auth)
Half-price 720p video on the auth path we already have.
- [ ] Add `veo-3.1-lite` as a selectable video model (`MODELS`/`--model` value or `VEO_MODEL` default note).
- [ ] README: document `--model veo-3.1-lite` ($0.05/s, 720p) vs `veo-3.1`/`veo-3.1-fast`.
- [ ] `--dry-run` shows the Lite model id.
**Done when:** `mediagen video --model veo-3.1-lite ...` renders a clip; price ≈ half of fast.

### Phase 1 — Backend interface (refactor, behavior unchanged)
Generalize `vertex.py` into a provider-agnostic seam.
- [ ] Create `src/mediagen/backends/` with a small protocol: `auth_token()`, `build_url(model, op)`,
      `post(url, body)`, plus per-call `encode`/`extract` helpers (move the shared bits out of `vertex.py`).
- [ ] Move current Vertex logic to `backends/vertex.py` (no behavior change).
- [ ] Add a `backend` field to the `MODELS` map: `(model_id, location/endpoint, api, backend)`.
- [ ] `image.py` / `video.py` resolve `backend` and dispatch; default backend = `vertex`.
- [ ] Make `auth_token()` part of the backend protocol and **lazy** — only invoked when that backend is dispatched, so a Vertex-only run never touches other credentials.
**Done when:** all existing commands produce identical output; `--dry-run` JSON unchanged for Vertex models.

### Phase 2 — fal.ai backend (breadth for the long tail, NOT a Vertex price-beater)
One `FAL_KEY` unlocks the models we have no first-party account for — FLUX schnell ($0.003/MP), Flux 2, Wan,
Kling, and (conveniently) Seedance. **Do not register Google models here** — those stay on Vertex-direct
(cheaper). This phase buys *breadth under one key*, not a discount on what we already run.
- [ ] `backends/fal.py`: `FAL_KEY` env auth; `urllib` POST to fal queue endpoint + poll for result; download bytes.
- [ ] Register fal models in the `MODELS` map (start small, long-tail only): e.g. `flux-2-dev`, `flux-schnell`, `seedance-2-fast`, `wan-2.6`.
- [ ] Map mediagen's existing flags (`--aspect`, `--size`, `--duration`, `--ref`) onto fal's input schema; document any unsupported combos with a clear error.
- [ ] `config.py`: add `FAL_KEY = os.getenv("FAL_KEY")` (optional, no default; only required when a fal model is selected). Raise a clear `FalError` if missing on dispatch.
- [ ] README: soften the "no API keys anywhere" claim → "no keys unless you opt into a non-Google backend"; note keeping `FAL_KEY` in a shell profile / secrets manager, never in scripts.
**Done when:** `mediagen image --model flux-2-dev ...` and `mediagen video --model seedance-2-fast ...` work via fal; `--dry-run` shows the fal request; a Vertex-only run still needs no `FAL_KEY`.

### Phase 3 — Cost tiers / agent-friendly routing
Let Claude ask for "cheapest acceptable" instead of memorizing model ids.
- [ ] Tag each `MODELS` entry with a `tier` (`cheap` | `premium`) and rough `$` metadata.
- [ ] Add `--tier cheap|premium` (resolves to a sensible default model per command) **or** a `select_model(use_case, needs_audio, min_duration)` helper.
- [ ] README: a short "cost tiers" table the agent/user can read.
- [ ] (optional) `--check` / `doctor` note: report which credentials are present for the models in scope, so "why did this fail" is self-explanatory.
**Done when:** `mediagen video --tier cheap ...` picks a low-cost production-quality model without naming it.

### Phase 4 (optional, later) — ByteDance ModelArk direct
Only if a **measured** ModelArk-vs-fal saving at your target resolution justifies a second key. The
"direct is ~25% cheaper" claim was **retracted as unverified** — Seedance pricing is tier/resolution
dependent (Fast ~$0.022/s vs Pro ~$0.247/s observed), so confirm the delta before adding `ARK_KEY`.
- [ ] **First: benchmark** ModelArk direct vs the same Seedance model via fal at your real resolution/tier; only proceed if the saving is material.
- [ ] `backends/modelark.py`: `ARK_API_KEY`, token-billed Seedream/Seedance.
- [ ] Note caveats in README: 720p cap (upscale in post), close-up-face privacy flag, dashboard billing lag.
**Done when:** Seedance/Seedream reachable both via fal (Phase 2) and direct, with a measured cost reason to prefer direct.

---

## Reference: models & pricing to wire in (verify before spend — 2026-06-18)

**Video ($/sec)** — Google Veo, *official Google Cloud pricing, verified 2026-06-18*: Veo 3.1 Lite **$0.05** (720p) · Veo 3.1 Fast **$0.10** (720p) / $0.12 (1080p) · Veo 3.1 video-only **$0.20** · Veo 3.1 **+audio $0.40** (audio doubles cost). fal long-tail: Wan 2.6 ~$0.05 · Kling 3.0 Pro ~$0.09. **Seedance: tier/resolution-dependent, do not assume one $/s** (Fast ~$0.022 vs Pro ~$0.247 observed) — verify per-call.
**Image ($/image)** — Google official: Imagen 4 Fast **$0.02** · Imagen 4/3 **$0.04** · `nano-banana` ~**$0.039** · `nano-banana-pro` **$0.134 @2K** (premium). fal: FLUX schnell **$0.003/MP** → $0.15 premium.

**Auth paths by backend:** `vertex` = gcloud token (current) · `fal` = `FAL_KEY` · `modelark` = `ARK_API_KEY`.

## Risks / watch-items

- **Pricing & model ids drift fast** — keep them in the `MODELS` map (data), re-probe per `docs/vertex-models.md` recipe.
- **Flag mapping** — aspect/size/duration semantics differ per provider; surface unsupported combos as errors, don't silently coerce.
- **Sora 2 is dead** (Mar 2026) — do not target it. **Wan-next** (Alibaba) is the rising arena leader; likely lands on fal — easy add once Phase 2 exists.
- Keep total LOC honest — if a backend needs an SDK, reconsider the model rather than breaking the no-SDK rule.

## Sequencing

Phase 0 ships standalone today. Phase 1 unblocks 2/3/4. Recommended order: **0 → 1 → 2 → 3**, with 4 deferred until volume justifies a second key.
