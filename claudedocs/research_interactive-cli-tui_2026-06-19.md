# Interactive `mediagen login` — TUI options

**Date:** 2026-06-19 · **Confidence:** High (library docs + ecosystem)

## Goal
An interactive `mediagen login`: arrow-key **navigate** a menu (pick provider), **enter**, then a **prompt to paste the key** (hidden). Weigh against mediagen's "two tiny deps (click + Pillow), stdlib HTTP" ethos.

## The two needs split cleanly
1. **Paste a secret (hidden input):** solved **with zero new deps** — we already ship `click`. `click.prompt("Paste key", hide_input=True)` accepts pasted text and hides it. (stdlib `getpass.getpass()` is the no-click equivalent.) Pasting works fine in hidden prompts.
2. **Arrow-key menu navigation:** this is what needs a library — stdlib has only `curses` (clunky, Windows needs an extra wheel).

## Library landscape

| Option | Arrow-key nav | Hidden paste | Dep weight | Cross-platform | Notes |
|---|---|---|---|---|---|
| **click only** (have it) | ❌ (numbered menu via `click.Choice`/`prompt`) | ✅ `hide_input=True` | **none new** | ✅ | Interactive but not arrow-key |
| **questionary** | ✅ `select` | ✅ `password` | +1 (pulls `prompt_toolkit`) | ✅ | Cleanest fit; `select` + `password` = exactly the UX |
| **prompt_toolkit** | ✅ (dialogs/menus) | ✅ | medium | ✅ | Powerful but more code; questionary wraps it |
| **simple-term-menu** | ✅ | ❌ (menus only) | tiny | ❌ Unix only (termios) | No input prompts; not Windows |
| **Textual** | ✅ (full app) | ✅ | heavy (rich + more) | ✅ | Overkill for a setup screen |
| **curses** (stdlib) | ✅ | ✅ | none | ⚠️ Windows needs `windows-curses` | Low-level, verbose, fiddly |

## Recommendation
**questionary as an *optional extra*** (`pip install mediagen[tui]`), with a **graceful click fallback** when it's
not installed. This gives the real arrow-key UX the user asked for while keeping the **default install at two
deps**. `mediagen login`:
- if `questionary` importable → `questionary.select(...)` (arrow-key provider menu) + `questionary.password(...)` (hidden paste);
- else → `click` numbered menu + `click.prompt(hide_input=True)`.

Either way the same `credstore.set_value()` from PR#6 persists it (chmod 600). No change to resolution (env > file).

### Why not the alternatives
- **questionary as a core dep** → breaks the "two tiny deps" promise for everyone, even non-interactive/CI users.
- **Zero-dep numbered menu only** → honors ethos perfectly but no true arrow-key nav (the user explicitly asked for "navigate").
- **Textual/prompt_toolkit direct** → more weight/code than a credential screen warrants.

## Sources
- Click docs — *User Input Prompts* (`prompt`, `hide_input`). 
- questionary docs — `select` (arrow menu) + `password` (hidden) question types.
- python-prompt-toolkit (GitHub/readthedocs) — questionary's engine.
- simple-term-menu (PyPI) — arrow/j-k menus, Unix-only.
- stdlib `getpass.getpass` — hidden input, zero-dep.
