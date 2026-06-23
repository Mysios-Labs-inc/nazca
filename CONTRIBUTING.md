# Contributing to nazca

Thanks for your interest in nazca. This document covers how to contribute and — importantly —
how contributions are licensed.

## Licensing of contributions

nazca is distributed under the **PolyForm Noncommercial License 1.0.0** (see `LICENSE`).
nazca is also offered under separate **commercial licenses** by Mysios Labs, Inc.

By submitting a contribution (a pull request, patch, or any other material) you agree that:

1. Your contribution is licensed to the project and its users under the
   **PolyForm Noncommercial License 1.0.0** (inbound = outbound), **and**
2. You grant **Mysios Labs, Inc.** a perpetual, worldwide, non-exclusive, royalty-free,
   irrevocable license to use, reproduce, modify, prepare derivative works of, distribute,
   and **relicense** your contribution, **including under commercial or proprietary terms**.

This dual grant lets the maintainers keep offering nazca under both the noncommercial
license and commercial licenses without having to track down every contributor later. You
retain copyright in your contribution; you are only granting the licenses above.

You must have the right to make the contribution (i.e. it is your original work, or you are
authorized to submit it). See the **Developer Certificate of Origin** below.

## Developer Certificate of Origin (DCO)

We use the [Developer Certificate of Origin 1.1](./DCO) to certify provenance. Every commit
must be signed off, certifying you agree to the DCO and the licensing terms above:

```bash
git commit -s -m "your message"
```

This appends a `Signed-off-by: Your Name <you@example.com>` line. Use your real name and a
reachable email. PRs without sign-off can't be merged.

## Making a change

- **Keep PRs small and focused** — one logical change per PR.
- **Match existing conventions** — read the surrounding code; follow its naming, structure,
  and the descriptors in `src/nazca/capabilities.py` (a test asserts every model has a
  `Caps` entry).
- **Run checks before pushing** — lint and tests must pass.
- **Never commit secrets.** Provider keys come from environment variables only
  (`FAL_KEY`, `ARK_API_KEY`, `OPENAI_API_KEY`, Vertex ADC). `.env` files are gitignored.
- **Update docs** when you change behavior — `README.md` and the relevant `docs/*.md`.

## Dev setup

See `README.md` for install. From a clone:

```bash
./scripts/install.sh
```

## Reporting bugs / security

- Functional bugs: open a GitHub issue with repro steps.
- Security vulnerabilities: **do not** open a public issue — see `SECURITY.md`.

## Commercial licensing

Want to use nazca commercially? The noncommercial license does not permit that. Contact
Mysios Labs, Inc. via the repository's GitHub organization to arrange a commercial license.
