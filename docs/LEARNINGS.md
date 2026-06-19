# Learnings

Durable lessons from building nazca (formerly `mediagen`). Kept short and concrete
so future work doesn't relearn them.

## Architecture / verification

- **Routing as data, not code.** A `backend` field in the `MODELS` map selects the provider;
  adding a model is a one-line entry, adding a provider is a new `Backend` + one key in `BACKENDS`.
  This kept every provider PR small and additive.
- **Byte-for-byte dry-run parity is the regression gate.** With no test suite, the reliable check is:
  capture `--dry-run` request JSON for a matrix of models, then `diff` after every change. The
  refactor (#2) and all later PRs were validated this way — output must be identical unless intended.
- **`--dry-run` everywhere, no spend.** Lets you verify request shape (URL + body) before any real call.
  Made the multi-provider work safe to build without keys.
- **Lazy auth.** A backend mints its credential only when one of *its* models is dispatched, so a
  Vertex-only run never reaches for `FAL_KEY`/`ARK_API_KEY`.

## Cost / models (verify before quoting)

- **Quote prices only from the provider's official page.** Secondary blogs were wrong/stale; Google
  Cloud's pricing page is authoritative (Veo Lite $0.05/s, Fast $0.10/s, full $0.20, +audio $0.40).
- **Seedance pricing is tier/resolution dependent** — there is no single `$/s`; do not cite one.
- **Direct-first beats aggregators for first-party models.** Google on Vertex is the floor (and gets
  GCP credits). fal is for the long tail you have no first-party account for, not a price-beater.

## ModelArk (BytePlus) integration — hard-won

- **Endpoint + auth + request shape were right; only the model ID and activation were wrong.** A live
  call returning `404 InvalidEndpointOrModel.NotFound` (not 401/connection) means the integration works —
  the API accepted the key and parsed the body.
- **Use the exact BytePlus model IDs** from their docs: `bytedance-seedance-1-0-lite-i2v-250428`,
  `bytedance-seedance-1-0-pro-250528`. nazca video is image-to-video → use the **i2v** variant.
- **Models must be ACTIVATED per account** in the BytePlus console (Model activation page), in the
  matching region (`ap-southeast` = our endpoint). Until activated, you get the same 404 even with the
  correct ID. This is account-side, not a code bug.
- Base URL: `https://ark.ap-southeast.bytepluses.com/api/v3`; video = async tasks
  (`/contents/generations/tasks` → poll → download); image = sync (`/images/generations`).

## Distribution / install

- **Python's npm-equivalent is PyPI; `pip`/`pipx` == `npm install`.** No need to rewrite to Node for a
  clean install — the tool's proven Python isn't worth re-verifying for install ergonomics alone.
- **One-command install today (private repo):** `pipx install "git+https://github.com/MRCORD/nazca.git"`.
- **Package name vs command name:** PyPI being taken doesn't block a command name — publish the package
  as `<name>-cli` while the binary stays `<name>`. `nazca` is free on PyPI for when we go public.
- **`pipx install "<path>[tui]" --force`** to get optional extras into the global app; the editable venv
  was rebuilt because the repo moved (stale shebangs).

## CLI credential UX

- **Don't make users juggle env vars** — real CLIs (AWS/Stripe/gh) store creds in `~/.config/<tool>/`
  written by a `login`/`configure` command. nazca does the same; precedence is **env var > config file**.
- **Arrow-key menus need a library + a TTY.** questionary is an *optional* `[tui]` extra with a click
  numbered-menu fallback; the rich UI activates only when questionary is importable **and** stdin is a TTY
  (so pipes/CI fall back cleanly). The default install stays two deps.
- **Secrets:** env-only or config file (chmod 600), never a `--flag` (shell history / agent transcript),
  masked on display, never echoed.

## Asset / branding workflow

- **Line-art does not convert to good ASCII.** A geoglyph's thin double-outline becomes noise; even with
  dilation it's mediocre. For a clean banner, use a real image, not ASCII.
- **Generate brand assets with the tool itself.** nazca drew its own hero (Nazca hummingbird) and animated
  it (Veo) — original, no stock/licensing risk, on-brand, and a live end-to-end test in one.
- **Aesthetic iteration is cheap and worth it.** "AI/circuit" then "homebrew/amber-phosphor on near-black"
  each took one regen (~$0.13). Avoid literal cues that backfire ("CRT monitor" drew a physical TV — say
  "flat graphic, full-bleed, no monitor/bezel").
- **GIF, not committed MP4, for a looping README hero.** GitHub renders/loops inline GIFs but not
  repo-relative `<video>`. Optimize with ffmpeg palette + `gifsicle` (got 9.7MB → 1.4MB at 520px/10fps).
- Overlay wordmarks in **code (Pillow), not via the image model** — keeps the "model renders clean media,
  no baked-in text" rule intact.

## Naming

- A good brand name is a **concrete metaphor that maps to function** (like `bacon`, `khipu`): the
  **Nazca Lines** are ancient large-scale image-making → `nazca` for an image/video tool. Verb-y literal
  names (`shoot`, `mint`) were available as commands but less sticky.
