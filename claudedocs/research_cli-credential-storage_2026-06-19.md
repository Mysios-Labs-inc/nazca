# How CLIs normally store credentials locally — and what mediagen should do

**Date:** 2026-06-19 · **Confidence:** High (primary sources: WorkOS CLI-auth guide, AWS CLI docs, Stripe CLI, gh)

## The question
mediagen is a CLI. Expecting users to hand-manage env vars / `.env` files is bad UX. How do real CLIs store
credentials locally so the tool "just works" after a one-time setup?

## Executive answer
The dominant pattern is **NOT env vars and NOT a project `.env`**. It's:

1. A **`login` / `configure` command** that prompts for the secret once and **writes a config file in the
   user's home dir** (or the OS keychain).
2. The CLI **reads that file at runtime** on every invocation — no env juggling.
3. **Resolution precedence:** explicit flag → environment variable → config file. Env stays for CI/overrides;
   the config file is the default human path.

A project-local `.env` is a *dev-server* convention (dotenv), not how an installed, run-from-anywhere CLI
stores user credentials.

## What real tools do (primary evidence)

| Tool | Setup command | Where the secret lives | Format |
|---|---|---|---|
| **Stripe CLI** | `stripe login` | OS keychain if available, else `~/.config/stripe/config.toml` | TOML / keychain |
| **AWS CLI** | `aws configure` | `~/.aws/credentials` (+ `~/.aws/config`) | INI, profiles |
| **GitHub `gh`** | `gh auth login` | OS keychain / `~/.config/gh/hosts.yml` | device-code OAuth → token |
| **npm** | `npm login` | `~/.npmrc` | per-registry creds |
| **kubectl** | (config) | `~/.kube/config` | YAML |
| **gcloud** *(mediagen's Vertex path)* | `gcloud auth login` | `~/.config/gcloud/` | OAuth, short-lived tokens |

Conventions that emerge:
- **Home-dir config file** is the baseline (`~/.config/<tool>/` per the XDG Base Directory spec on Linux/mac,
  or `~/.<tool>/`). Stripe, AWS, npm, gh, gcloud all do this.
- **A `login`/`configure` verb writes it** so users never hand-edit files or export vars.
- **Env var overrides the file** (AWS: env vars take precedence over the credentials file). Standard order:
  **flag > env > config file > error**.
- **Best-in-class encrypts at rest** via the OS keychain (macOS Keychain / Windows Credential Manager).
  Stripe writes to the keychain when present, falling back to a plaintext TOML. Python's `keyring` library
  gives this cross-platform.

## Anti-patterns the sources call out (WorkOS)
- ❌ "Avoid environment variables for **end-user** CLI tools — they require manual user management, can be
  exposed by process listings/logs, and lack encryption at rest." (Env vars are correct for **CI/CD**, not
  for a human's daily CLI.)
- ❌ Hardcoding keys in source or committing them.
- ✅ Use a home-dir config file (ideally keychain-backed); keep env vars as the CI/override path.

## Where mediagen stands
- Its **main path (Vertex) already follows the gold standard**: `gcloud auth login` writes to
  `~/.config/gcloud/`, mediagen reads short-lived tokens at runtime. Zero env juggling. ✅
- Only the **new BYOK keys** (`FAL_KEY`, `ARK_API_KEY`) currently rely on raw env vars — which is exactly the
  end-user anti-pattern. There's **no `.env` auto-load** either (no `python-dotenv`), so a bare `.env` does
  nothing today.

## Recommendation for mediagen (keep it minimal)
Add a tiny, **zero-dependency** config layer mirroring the standard pattern:

1. **Config file** at `~/.config/mediagen/config.toml` (honor `$XDG_CONFIG_HOME`; fall back to `~/.mediagen/`).
   - **Read:** stdlib `tomllib` (Python ≥3.11) — mediagen targets ≥3.10, so either bump to 3.11 for `tomllib`
     **or** use stdlib `configparser` (INI, read+write, works on 3.10). INI keeps it truly zero-dep and writable.
2. **A `mediagen login` / `mediagen config set` command** (click) that prompts (`click.prompt(..., hide_input=True)`)
   and writes the file with `chmod 600`. No hand-editing, no exports.
3. **Resolution order in `config.py`:** `flag > os.getenv(...) > config-file value > None`. Env var keeps
   working for CI/power users; the file becomes the default human path.
4. **Optional, later:** OS keychain via `keyring` (Stripe-style) for encryption at rest — but that's a new
   dependency, so make it opt-in or skip to honor the "two tiny deps" ethos.

Net: a user runs `mediagen login` once, the key lands in `~/.config/mediagen/config.toml` (perms 600), and
every later `mediagen image --model seedream ...` just works — matching how Stripe/AWS/gh behave, and
consistent with how mediagen's own gcloud path already works.

## Sources
- WorkOS — *Best practices for CLI authentication* (patterns table; "avoid env vars for end-user CLIs"; keychain > encrypted file > env).
- AWS CLI docs — `~/.aws/credentials` + profiles; env vars override the file.
- Stripe CLI (docs + issue #1013) — `stripe login` → OS keychain / `~/.config/stripe/config.toml`.
- GitHub `gh` — `gh auth login` device-code flow → keychain / `~/.config/gh`.
- Python `keyring` (PyPI) — cross-platform OS keychain access.
- XDG Base Directory spec — `$XDG_CONFIG_HOME` (`~/.config/<tool>/`).
