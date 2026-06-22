"""nazca CLI — `nazca image`, `nazca video`, `nazca login`, `nazca config`."""

from __future__ import annotations

import json
import sys

import click

from nazca import __version__

# ---------------------------------------------------------------------------
# Login UI helpers — two code paths:
#   Rich path:  questionary installed AND stdin is a real TTY
#   Fallback:   numbered menu via click (works non-TTY / piped / no questionary)
# ---------------------------------------------------------------------------

try:
    import questionary as _questionary  # noqa: F401 — presence check only

    _HAS_QUESTIONARY = True
except ImportError:
    _HAS_QUESTIONARY = False

# Provider menu entries — (display label, credential key or None for info-only)
# (base label, credential key). key=None → Vertex (info only); "done" → exit.
_PROVIDERS: list[tuple[str, str | None]] = [
    ("fal.ai  (FAL_KEY)", "fal_key"),
    ("ByteDance ModelArk  (ARK_API_KEY)", "ark_api_key"),
    ("Vertex AI  (gcloud — no key needed)", None),
    ("Done", "done"),
]

_LABEL_W = 36  # pad base labels so the status column lines up


def _status(key: str | None) -> str:
    """Right-hand status tag for a provider row (set/not-set + source)."""
    if key == "done":
        return ""
    if key is None:  # Vertex — auth is gcloud, no stored key
        return "✓ gcloud"
    from nazca.credstore import _key_source

    _, source = _key_source(key)
    return "✗ not set" if source == "unset" else f"✓ set · {source}"


def _menu_items() -> list[tuple[str, str | None]]:
    """Build (display_label, key) rows with an aligned live status column."""
    items = []
    for base, key in _PROVIDERS:
        status = _status(key)
        display = f"{base.ljust(_LABEL_W)}{status}".rstrip()
        items.append((display, key))
    return items


def _use_rich_ui() -> bool:
    """Return True only when questionary is available and stdin is a real TTY."""
    return _HAS_QUESTIONARY and sys.stdin.isatty()


def _select_provider() -> str | None:
    """Show a provider menu; return the credential key, None (Vertex info), or 'done'."""
    items = _menu_items()

    if _use_rich_ui():
        import questionary

        label = questionary.select(
            "Select a provider to configure:",
            choices=[d for d, _ in items],
        ).ask()
        if label is None:  # Ctrl-C
            return "done"
        return dict(items)[label]
    else:
        # Fallback: numbered menu
        click.echo("\nSelect a provider:")
        for i, (display, _) in enumerate(items, 1):
            click.echo(f"  {i}. {display}")
        choice = click.prompt(
            "Enter number",
            type=click.IntRange(1, len(items)),
        )
        return items[choice - 1][1]


def _prompt_secret(label: str) -> str:
    """Prompt for a hidden API key; returns '' if the user skips."""
    if _use_rich_ui():
        import questionary

        val = questionary.password(f"Paste {label} (Enter to skip):").ask()
        return val or ""
    else:
        return click.prompt(
            f"Paste {label} (Enter to skip)",
            hide_input=True,
            default="",
            show_default=False,
            prompt_suffix=": ",
        )


@click.group()
@click.version_option(__version__)
def cli() -> None:
    """Thin CLI for AI image + video generation. Claude-driven."""


@cli.command()
@click.option("-o", "--out", required=True, help="Output image path (.png).")
@click.option("-p", "--prompt", required=True, help="Generation prompt.")
@click.option("--ref", multiple=True, help="Reference image → image-to-image restyle. Repeatable (pro-image: up to 14).")
@click.option("--model", default=None, help="nano-banana (default,fast,ref) | nano-banana-2 (ref) | nano-banana-pro (ref, legible text, 14 refs) | imagen-4 | imagen-4-fast | imagen-3 (t2i only)")
@click.option("--aspect", "aspect_ratio", default="9:16", help="Aspect ratio.")
@click.option("--size", default="2K", type=click.Choice(["1K", "2K", "4K"]), help="Output res (gemini-3 only; 2.5-flash stays 1K).")
@click.option("--tier", default=None, type=click.Choice(["cheap", "premium"]), help="Cost tier: pick cheap or premium default model. Ignored when --model is given.")
@click.option("--dry-run", is_flag=True, help="Print the planned request; no API call.")
def image(out, prompt, ref, model, aspect_ratio, size, tier, dry_run):
    """Generate (or restyle with --ref) one image via Vertex Gemini / Imagen."""
    from nazca.capabilities import CapabilityError, infer_image_op, validate_op
    from nazca.image import DEFAULT_MODEL, generate_image, select_model

    # --model wins; --tier only supplies a default when --model is absent
    resolved_model = model or select_model(tier)

    # Capability check up front: the op is the refs count (t2i/i2i/compose), and we
    # reject e.g. imagen + --ref here instead of letting it raise mid-dispatch.
    op = infer_image_op(len(ref))
    try:
        validate_op(resolved_model or DEFAULT_MODEL, op, n_refs=len(ref))
    except CapabilityError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(2) from e

    result = generate_image(
        out, prompt, ref=list(ref) or None, model=resolved_model,
        aspect_ratio=aspect_ratio, size=size, dry_run=dry_run,
    )
    if dry_run:
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(f"✅ {result}")


@cli.command(name="batch")
@click.argument("manifest", required=False, type=click.Path())
@click.option("--from-dir", "from_dir", default=None, type=click.Path(exists=True, file_okay=False),
              help="Build rows from a dir of ref images instead of a manifest.")
@click.option("--prompt", default=None, help="Prompt for --from-dir (may use {stem}/{name}).")
@click.option("--out-dir", "out_dir", default="batch-out", type=click.Path(),
              help="Output dir for --from-dir (default: batch-out).")
@click.option("--rpm", default=2.0, type=float, help="Per-lane request starts/min (default 2 = Vertex cap).")
@click.option("--models", default=None, help="Comma-separated model shorthands: filter (manifest) or fan-out (--from-dir).")
@click.option("--aspect", "aspect", default=None, help="Default aspect ratio for rows that omit it.")
@click.option("--size", default=None, type=click.Choice(["1K", "2K", "4K"]), help="Default size for rows that omit it.")
@click.option("--concurrency", default=None, type=int, help="Max concurrent model lanes (default: one per model).")
@click.option("--vertex-batch", "vertex_batch", is_flag=True, help="Use async Vertex Batch (no RPM wall, −50%, 1K only). Needs --gcs.")
@click.option("--gcs", "gcs", default=None, help="gs://bucket/prefix for Vertex Batch input/output (with --vertex-batch).")
@click.option("--dry-run", is_flag=True, help="Print the plan + per-row requests; no API calls.")
def batch_cmd(manifest, from_dir, prompt, out_dir, rpm, models, aspect, size, concurrency, vertex_batch, gcs, dry_run):
    """Generate many images, paced per model lane (idempotent + resumable).

    Two input modes:

    \b
      nazca batch jobs.jsonl                     # manifest: one row per image
      nazca batch --from-dir refs/ --prompt "…"  # one row per ref image in a dir

    Rows already present at their `out` path are skipped, so a re-run only fills
    gaps. Each distinct model runs on its own paced lane (the 2/min Vertex cap is
    per base model), so N models ≈ N×rpm combined.

    \b
    --vertex-batch routes Gemini-image rows through async Vertex Batch inference
    (no per-minute quota, 50% cheaper, 1K-only output) via a --gcs bucket.
    """
    from nazca.batch import (
        BatchError,
        load_manifest,
        plan_batch,
        rows_from_dir,
        run_batch,
    )
    from nazca.image import DEFAULT_MODEL

    model_list = [m.strip() for m in models.split(",") if m.strip()] if models else None

    try:
        if from_dir:
            if not prompt:
                raise BatchError("--from-dir requires --prompt")
            rows = rows_from_dir(
                from_dir, prompt, out_dir, models=model_list, aspect=aspect, size=size,
            )
            only = None  # --models already applied as fan-out
        elif manifest:
            defaults = {"aspect": aspect, "size": size}
            rows = load_manifest(manifest, defaults=defaults)
            only = set(model_list) if model_list else None
        else:
            raise BatchError("provide a MANIFEST path or --from-dir")
    except BatchError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(2) from e

    # --- Vertex Batch (async, no RPM wall) -------------------------------
    if vertex_batch:
        _run_vertex_batch_cmd(rows, gcs, only, dry_run)
        return

    try:
        plan = plan_batch(rows, rpm=rpm, default_model=DEFAULT_MODEL, only_models=only)
    except BatchError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(2) from e

    for line in plan.summary_lines():
        click.echo(line)

    if plan.pending == 0 and not dry_run:
        click.echo("nothing to do — all outputs already exist.")
        return

    icons = {"ok": "✅", "skipped": "⏭", "error": "❌", "planned": "📝"}

    def on_event(status, row, detail):
        line = f"  {icons.get(status, '?')} {row.out}"
        if status == "error":
            line += f"  ({detail})"
        click.echo(line)

    results = run_batch(plan, dry_run=dry_run, concurrency=concurrency, on_event=on_event)

    ok = sum(1 for r in results if r.status == "ok")
    skipped = sum(1 for r in results if r.status == "skipped")
    errors = [r for r in results if r.status == "error"]
    planned = sum(1 for r in results if r.status == "planned")

    if dry_run:
        click.echo(f"\nplanned {planned} request(s) ({skipped} already done) — no API calls made.")
        return

    click.echo(f"\ndone: {ok} generated · {skipped} skipped · {len(errors)} failed")
    if errors:
        raise SystemExit(1)


def _run_vertex_batch_cmd(rows, gcs, only_models, dry_run):
    """Drive an async Vertex Batch run from the `batch` command (with --vertex-batch)."""
    from nazca.vertex_batch import VertexBatchError, run_vertex_batch

    if not gcs:
        click.echo("❌ --vertex-batch requires --gcs gs://bucket/prefix", err=True)
        raise SystemExit(2)

    # Honor the manifest --models filter (resolve each row's model, keep matches).
    if only_models:
        from nazca.image import DEFAULT_MODEL, _resolve
        kept = []
        for r in rows:
            mid, _, _, _ = _resolve(r.model or DEFAULT_MODEL)
            if (r.model in only_models) or (mid in only_models):
                kept.append(r)
        rows = kept

    def on_event(stage, detail):
        if stage == "submit":
            click.echo(f"  🚀 submitting {detail.model_id} ({len(detail.rows)} rows)")
        elif stage == "poll":
            click.echo(f"  ⏳ {detail}")
        elif stage == "fetch":
            click.echo(f"  ⬇  fetching {detail.model_id} predictions")

    try:
        summary = run_vertex_batch(rows, gcs, dry_run=dry_run, on_event=on_event)
    except VertexBatchError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(2) from e

    click.echo(
        f"vertex-batch: {summary['jobs']} job(s) · {summary['pending']} pending · "
        f"models {', '.join(summary['models']) or '—'}"
    )
    if summary["oversize_forced_1k"]:
        click.echo(f"  ⚠ {summary['oversize_forced_1k']} row(s) asked for 2K/4K — batch is 1K-only, forced to 1K")

    if dry_run:
        for j in summary.get("planned", []):
            click.echo(f"  📝 {j['model']} [{j['location']}] {j['rows']} rows → {j['output_prefix']}")
        click.echo(json.dumps(summary["planned"], indent=2))
        click.echo("\nno job submitted (--dry-run).")
        return

    written = summary.get("written", 0)
    errs = summary.get("errors", [])
    click.echo(f"\ndone: {written} image(s) written · {len(errs)} error(s)")
    for e in errs:
        click.echo(f"  ❌ {e}")
    if errs:
        raise SystemExit(1)


@cli.command()
@click.option("-o", "--out", required=True, help="Output video path (.mp4).")
@click.option("-s", "--start", default=None, help="Start frame image. Omit for text-to-video (t2v).")
@click.option("-p", "--prompt", required=True, help="Motion prompt.")
@click.option("--end", default=None, help="Optional end frame (keyframe interpolation).")
@click.option("--model", default=None, help="Veo model (default: veo-3.1-fast-generate-001).")
@click.option("--duration", default=8, type=int, help="Seconds (4, 6, or 8).")
@click.option("--aspect", "aspect_ratio", default="9:16", help="9:16 or 16:9.")
@click.option("--resolution", default="720p", help="720p | 1080p.")
@click.option("--audio", is_flag=True, help="Let Veo generate audio.")
@click.option("--tier", default=None, type=click.Choice(["cheap", "premium"]), help="Cost tier: pick cheap or premium default model. Ignored when --model is given.")
@click.option("--dry-run", is_flag=True, help="Write request JSON; no API call / no credits.")
def video(out, start, prompt, end, model, duration, aspect_ratio, resolution, audio, tier, dry_run):
    """Generate a video on Vertex Veo: text-to-video, image-to-video (--start), or keyframe (--start --end)."""
    from nazca import config
    from nazca.capabilities import CapabilityError, infer_video_op, validate_op
    from nazca.video import VEO_ALIASES, generate_video, select_model

    # --model wins; --tier only supplies a default when --model is absent
    resolved_model = model or select_model(tier)
    # For validation, resolve the implicit default (config.VEO_MODEL is a raw Veo
    # id) to its shorthand so the default path is validated too — symmetric with
    # the image command. Unknown ids stay unvalidated (validate_op no-ops).
    validate_target = resolved_model or {v: k for k, v in VEO_ALIASES.items()}.get(config.VEO_MODEL)

    # Infer the op from the frames given (none→t2v, start→i2v, +end→keyframe) and
    # validate against the model so e.g. an i2v-only model without --start, or a
    # t2v-only model fed a --start, is rejected with a clear suggestion.
    if end and not start:
        click.echo("❌ --end requires --start (keyframe interpolation needs both frames)", err=True)
        raise SystemExit(2)
    op = infer_video_op(bool(start), bool(end))
    try:
        validate_op(validate_target, op)
    except CapabilityError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(2) from e

    result = generate_video(
        out, start, prompt, end=end, model=resolved_model, duration=duration,
        aspect_ratio=aspect_ratio, resolution=resolution,
        generate_audio=audio, dry_run=dry_run,
    )
    click.echo(f"{'📝' if dry_run else '✅'} {result}")


@cli.command()
def login() -> None:
    """Interactively store API credentials in ~/.config/nazca/config.ini.

    Arrow-key menu when `nazca[tui]` (questionary) is installed and stdin
    is a TTY; numbered menu otherwise.  Keys are hidden on input and masked in
    confirmation output.  Press Enter to skip any key (leaves existing value).
    """
    from nazca.credstore import config_path, mask_value, set_value

    ui = "arrow-key (questionary)" if _use_rich_ui() else "numbered (click fallback)"
    click.echo(f"nazca login  [{ui}]")
    click.echo("Credentials are saved to ~/.config/nazca/config.ini (chmod 600).")
    click.echo("Precedence: env var > config file.  Enter to skip any key.\n")

    while True:
        key_id = _select_provider()

        if key_id == "done":
            break

        if key_id is None:
            # Vertex AI — info only, no API key. Auth + project setup live in `nazca setup`.
            click.echo(
                "\nVertex AI uses Google auth (no API key). Run:\n"
                "  nazca setup    # installs gcloud if needed, logs in, sets VERTEX_PROJECT + region\n"
                "No key is stored by nazca; your project/region are saved to config.ini.\n"
            )
            continue

        # Map credential key → display label for the prompt
        _labels: dict[str, str] = {
            "fal_key": "fal.ai API key (FAL_KEY)",
            "ark_api_key": "ModelArk API key (ARK_API_KEY)",
        }
        label = _labels.get(key_id, key_id)
        val = _prompt_secret(label)

        if val:
            set_value(key_id, val)
            click.echo(f"  ✓ {key_id} saved  →  {mask_value(val)}  [{config_path()}]\n")
        else:
            click.echo(f"  — skipped {key_id} (existing value unchanged)\n")

    click.echo(f"Done.  Config: {config_path()}")


@cli.command()
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Skip confirmations (auto-install + login).")
def setup(assume_yes: bool) -> None:
    """Install gcloud (if missing) and authenticate Vertex AI (one-time, interactive).

    Ensures the Google Cloud SDK is present, runs `gcloud auth application-default
    login`, and verifies a token mints. Run this in your terminal before using
    Vertex models from the CLI or the Claude Desktop MCP server.
    """
    from nazca.setup import run_setup

    raise SystemExit(run_setup(assume_yes=assume_yes))


@cli.group()
def config() -> None:
    """Manage nazca credential config (~/.config/nazca/config.ini)."""


@config.command(name="set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set a config KEY to VALUE (e.g. nazca config set fal_key sk-...)."""
    from nazca.credstore import KNOWN_KEYS, set_value

    if key not in KNOWN_KEYS:
        click.echo(
            f"Unknown key '{key}'. Known keys: {', '.join(KNOWN_KEYS)}",
            err=True,
        )
        raise SystemExit(1)
    set_value(key, value)
    click.echo(f"Set {key} (saved to config file).")


@config.command(name="get")
@click.argument("key")
def config_get(key: str) -> None:
    """Print the masked value and source of KEY."""
    from nazca.credstore import KNOWN_KEYS, _key_source, display_value

    if key not in KNOWN_KEYS:
        click.echo(
            f"Unknown key '{key}'. Known keys: {', '.join(KNOWN_KEYS)}",
            err=True,
        )
        raise SystemExit(1)
    val, source = _key_source(key)
    if val:
        click.echo(f"{key} = {display_value(key, val)}  [{source}]")
    else:
        click.echo(f"{key} = (unset)")


@config.command(name="path")
def config_path_cmd() -> None:
    """Print the resolved config file path."""
    from nazca.credstore import config_path

    click.echo(config_path())


@config.command(name="list")
def config_list() -> None:
    """List all known credentials with masked values and sources."""
    from nazca.credstore import KNOWN_KEYS, _key_source, display_value

    for key in KNOWN_KEYS:
        val, source = _key_source(key)
        if val:
            click.echo(f"{key} = {display_value(key, val)}  [{source}]")
        else:
            click.echo(f"{key} = (unset)")


@cli.command(name="models")
def models_cmd() -> None:
    """List all image and video models, including user overrides from models.json."""
    from nazca.capabilities import ops_str
    from nazca.image import MODEL_TIERS as IMG_TIERS
    from nazca.image import MODELS as IMG_MODELS
    from nazca.registry import all_overrides, models_path
    from nazca.video import ARK_VIDEO_MODELS, FAL_VIDEO_MODELS, VEO_ALIASES, VIDEO_MODEL_TIERS

    ov = all_overrides()
    img_ov = ov.get("image", {})
    vid_ov = ov.get("video", {})

    SH = 20
    BE = 12
    TI = 10
    ID = 32

    def _hdr():
        click.echo(f"  {'shorthand':<{SH}} {'backend':<{BE}} {'tier':<{TI}} {'model id':<{ID}} {'ops'}")
        click.echo(f"  {'-'*SH} {'-'*BE} {'-'*TI} {'-'*ID} {'-'*24}")

    # ---- IMAGE MODELS ----
    click.echo("\nIMAGE MODELS")
    _hdr()

    # built-in + overrides merged (override wins for same shorthand)
    all_img: dict[str, tuple[str, str, str, str]] = {}
    for sh, (mid, region, api, be) in IMG_MODELS.items():
        all_img[sh] = (mid, region, api, be)

    for sh, entry in img_ov.items():
        # override: may introduce new shorthands or replace built-ins
        mid = entry.get("id", sh)
        region = entry.get("region", "")
        api = entry.get("api", "gemini")
        be = entry.get("backend", "vertex")
        all_img[sh] = (mid, region, api, be)

    for sh in sorted(all_img):
        mid, region, api, be = all_img[sh]
        tier = img_ov.get(sh, {}).get("tier") or IMG_TIERS.get(sh, "")
        marker = "*" if sh in img_ov else " "
        click.echo(f"  {sh:<{SH}} {marker}{be:<{BE}} {tier:<{TI}} {mid:<{ID}} {ops_str(sh)}")

    # ---- VIDEO MODELS ----
    click.echo("\nVIDEO MODELS")
    _hdr()

    # Collect built-in video models
    all_vid: dict[str, tuple[str, str]] = {}
    for sh, full_id in VEO_ALIASES.items():
        all_vid[sh] = (full_id, "vertex")
    for sh, fal_id in FAL_VIDEO_MODELS.items():
        all_vid[sh] = (fal_id, "fal")
    for sh, ark_id in ARK_VIDEO_MODELS.items():
        all_vid[sh] = (ark_id, "modelark")

    for sh, entry in vid_ov.items():
        mid = entry.get("id", sh)
        be = entry.get("backend", "vertex")
        all_vid[sh] = (mid, be)

    for sh in sorted(all_vid):
        mid, be = all_vid[sh]
        tier = vid_ov.get(sh, {}).get("tier") or VIDEO_MODEL_TIERS.get(sh, "")
        marker = "*" if sh in vid_ov else " "
        click.echo(f"  {sh:<{SH}} {marker}{be:<{BE}} {tier:<{TI}} {mid:<{ID}} {ops_str(sh)}")

    # ---- footer ----
    mp = models_path()
    status = "exists" if mp.exists() else "not found"
    click.echo(f"\nOverride file: {mp} [{status}]")
    click.echo("  * = overridden by user models.json")
    click.echo("  ops = supported operations (see docs/media-modalities.md)")


if __name__ == "__main__":
    cli()
