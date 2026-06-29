"""nazca CLI — `nazca image`, `nazca video`, `nazca login`, `nazca config`."""

from __future__ import annotations

import json
import os
import sys
import typing

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
    ("OpenAI  (OPENAI_API_KEY)", "openai_api_key"),
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
@click.option(
    "-v", "--verbose",
    count=True,
    help="Increase log verbosity: -v INFO, -vv DEBUG. Overridden by NAZCA_LOG_LEVEL env.",
)
def cli(verbose: int) -> None:
    """Thin CLI for AI image + video generation. Claude-driven."""
    import nazca.log as _log

    env_level = os.environ.get("NAZCA_LOG_LEVEL")
    level: int | str = env_level if env_level else _log.level_from_verbosity(verbose)
    _log.configure(level)


# ---------------------------------------------------------------------------
# Shared CLI helpers — extracted from command bodies so they can be tested
# independently without invoking Click plumbing.
# ---------------------------------------------------------------------------

def _emit_backend_error(e: Exception) -> "typing.NoReturn":
    """Render a backend failure as a clean one-liner (no traceback) and exit 1.

    Rate-limit failures additionally point at the bulk tools — the per-lane paced
    `nazca batch` and the async, no-rpm-wall `nazca batch --vertex-batch` — which
    is the fix a newcomer who reached for parallel `nazca image` calls actually
    needs (the exact mistake that 429s a single Vertex lane).
    """
    from nazca.errors import RateLimitError

    click.echo(f"❌ {e}", err=True)
    if isinstance(e, RateLimitError):
        click.echo(
            "   ↳ rate limit persisted after retries. For bulk runs use `nazca batch` "
            "(auto-paced per model lane), or `nazca batch --vertex-batch` (async Vertex "
            "Batch — no per-minute wall, ~50% cheaper). More model lanes = more throughput; "
            "more local processes on one model do not (the cap is per-model rpm).",
            err=True,
        )
    raise SystemExit(1)


def _validate_or_exit(model: str | None, op: str, *, n_refs: int = 0) -> None:
    """Run capability check; on failure echo ❌ to stderr and exit 2.

    This is the verbatim try/except that was duplicated in image() and video().
    """
    from nazca.capabilities import CapabilityError, validate_op

    try:
        validate_op(model, op, n_refs=n_refs)
    except CapabilityError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(2) from e


def _validate_image_inputs(
    source: str | None,
    ref: tuple,
    do_upscale: bool,
    do_rmbg: bool,
    mask: str | None,
    do_outpaint: bool,
    op: str,
    prompt: str | None,
    modify: bool,
) -> None:
    """Validate image command mutual-exclusion rules; raise SystemExit(2) on error.

    Preserves the exact message text and err=True stream of the original checks
    in image() lines 164-189.
    """
    # At most one modify signal at a time.
    if sum([do_upscale, do_rmbg, bool(mask), do_outpaint]) > 1:
        click.echo("❌ choose one modify op: --upscale / --rmbg / --mask (inpaint) / --outpaint", err=True)
        raise SystemExit(2)

    if modify:
        if not source:
            click.echo(f"❌ {op} needs a SOURCE image: nazca image PATH ...", err=True)
            raise SystemExit(2)
        if ref:
            click.echo("❌ --ref is not used with modify ops (they modify SOURCE)", err=True)
            raise SystemExit(2)
        if op == "inpaint" and not prompt:
            click.echo("❌ inpaint needs -p/--prompt describing the masked region", err=True)
            raise SystemExit(2)
    else:
        if source:
            click.echo("❌ a positional SOURCE image is only for modify ops (--upscale/--rmbg/--mask/--outpaint); use --ref for references", err=True)
            raise SystemExit(2)
        if not prompt:
            click.echo("❌ -p/--prompt is required (omit only for --upscale/--rmbg/--outpaint)", err=True)
            raise SystemExit(2)


def _validate_video_inputs(
    source: str | None,
    start: str | None,
    end: str | None,
    prompt: str | None,
    op: str,
    in_edit_ops: bool,
) -> None:
    """Validate video command mutual-exclusion rules; raise SystemExit(2) on error.

    Preserves the exact message text and err=True stream of the original checks
    in video() lines 468-504.
    """
    if in_edit_ops:
        flag = op.replace("_", "-")
        if not source:
            click.echo(f"❌ --{flag} needs a SOURCE video URL: nazca video CLIP_URL --{flag}", err=True)
            raise SystemExit(2)
        if start or end:
            click.echo(f"❌ --start/--end are for frame ops; --{flag} takes a SOURCE video, not frames", err=True)
            raise SystemExit(2)
        if op in ("v2v", "extend") and not prompt:
            click.echo(f"❌ --{op} needs -p/--prompt", err=True)
            raise SystemExit(2)
    else:
        if source:
            click.echo("❌ a positional SOURCE video is only for video-edit ops (--reframe/--v2v/--extend); use --start for a frame", err=True)
            raise SystemExit(2)
        if end and not start:
            click.echo("❌ --end requires --start (keyframe interpolation needs both frames)", err=True)
            raise SystemExit(2)
        if not prompt:
            click.echo("❌ -p/--prompt is required for t2v/i2v/keyframe", err=True)
            raise SystemExit(2)


def _emit_image_result(
    result,
    dry_run: bool,
    modify: bool,
    resolved_model: str | None,
    default_model: str,
    aspect_ratio: str,
    size: str,
    quality: str,
) -> None:
    """Print image command success output.

    dry_run path:   json.dumps(result, indent=2)
    success path:   ✅ {result}
                    💵 {cost}   (generation ops only, when a cost label exists)

    Preserves the exact output from image() lines 225-237.
    """
    if dry_run:
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(f"✅ {result}")
        if not modify:
            from nazca.image import image_cost_label

            cost = image_cost_label(
                resolved_model or default_model,
                aspect_ratio=aspect_ratio, size=size, quality=quality,
            )
            if cost:
                click.echo(f"💵 {cost}")


def _emit_video_result(result, dry_run: bool) -> None:
    """Print video command success output (shared by edit ops and frame ops).

    Preserves the exact output from video() lines 492 and 522:
        📝 {result}   (dry-run)
        ✅ {result}   (real run)
    """
    click.echo(f"{'📝' if dry_run else '✅'} {result}")


# ---------------------------------------------------------------------------


@cli.command()
@click.argument("source", required=False, type=click.Path())
@click.option("-o", "--out", required=True, help="Output image path (.png).")
@click.option("-p", "--prompt", default=None, help="Generation prompt (not needed for --upscale/--rmbg).")
@click.option("--ref", multiple=True, help="Reference image → image-to-image restyle. Repeatable (pro-image: up to 14). Optional role suffix: PATH:subject|style|identity (e.g. look.png:style); bare PATH is untyped.")
@click.option("--upscale", "do_upscale", is_flag=True, help="Upscale SOURCE image (fal clarity-upscaler).")
@click.option("--scale", "upscale_factor", default=2, type=click.IntRange(1, 4), help="Upscale factor 1-4 (with --upscale).")
@click.option("--rmbg", "do_rmbg", is_flag=True, help="Remove background from SOURCE → transparent PNG (fal birefnet).")
@click.option("--mask", default=None, type=click.Path(), help="Mask image → inpaint SOURCE (white pixels = region to edit). Needs -p.")
@click.option("--outpaint", "do_outpaint", is_flag=True, help="Outpaint/expand SOURCE canvas (fal flux-2-pro/outpaint).")
@click.option("--expand", default=256, type=click.IntRange(1, 2048), help="Outpaint pixels per side (with --outpaint).")
@click.option("--model", default=None, help="nano-banana (default,fast,ref) | nano-banana-2 (ref) | nano-banana-pro (ref, legible text, 14 refs) | imagen-4 | imagen-4-fast | imagen-3 (t2i only) | gpt-image-2 (OpenAI; legible text/ads, ref up to 5)")
@click.option("--aspect", "aspect_ratio", default="9:16", help="Aspect ratio.")
@click.option("--size", default="2K", type=click.Choice(["1K", "2K", "4K"]), help="Output res (gemini-3 only; 2.5-flash stays 1K).")
@click.option("--quality", default="high", type=click.Choice(["low", "medium", "high", "auto"]), help="gpt-image-2 only: cost/speed lever (medium ≈ 4× cheaper & faster than high). Ignored by other models.")
@click.option("--format", "output_format", default="png", type=click.Choice(["png", "jpeg", "webp"]), help="Output image format (gpt-image-2 only for jpeg/webp; default png).")
@click.option("--transparent", is_flag=True, help="Transparent background (gpt-image-2 only; sets background:transparent).")
@click.option("--style", "do_style", is_flag=True, help="Style transfer: apply a --ref image's look to the prompt (Atlas style-transfer models).")
@click.option("--tier", default=None, type=click.Choice(["cheap", "premium"]), help="Cost tier: pick cheap or premium default model. Ignored when --model is given.")
@click.option("--dry-run", is_flag=True, help="Print the planned request; no API call.")
def image(source, out, prompt, ref, do_upscale, do_rmbg, mask, do_outpaint, expand, upscale_factor, model, aspect_ratio, size, quality, output_format, transparent, do_style, tier, dry_run):
    """Generate, restyle (--ref), or modify (SOURCE + --upscale/--rmbg/--mask/--outpaint) an image.

    \b
      nazca image -p "a cat"                       # t2i
      nazca image -p "..." --ref a.png             # i2i (restyle)
      nazca image photo.png --upscale              # upscale (no prompt)
      nazca image photo.png --rmbg                 # background removal → transparent PNG
      nazca image photo.png --mask m.png -p "..."  # inpaint the masked region
      nazca image photo.png --outpaint --expand 320  # extend the canvas
      nazca image -p "..." --format jpeg           # output as JPEG (gpt-image-2 support varies)
      nazca image -p "..." --transparent           # transparent bg (gpt-image-2 only)
    """
    from nazca.capabilities import (
        CapabilityError,
        infer_image_op,
        parse_ref,
        role_annotation,
        validate_ref_roles,
    )
    from nazca.image import (
        DEFAULT_MODEL,
        MODIFY_OPS,
        default_modify_model,
        generate_image,
        modify_image,
        select_model,
    )

    # The op is inferred from the flags: modify signals win, then --style, else refs count.
    op = infer_image_op(len(ref), upscale=do_upscale, bg_remove=do_rmbg, mask=bool(mask), outpaint=do_outpaint, style=do_style)
    modify = op in MODIFY_OPS

    # Validate mutual-exclusion rules (raises SystemExit(2) on conflict).
    _validate_image_inputs(source, ref, do_upscale, do_rmbg, mask, do_outpaint, op, prompt, modify)
    if op == "style" and not ref:
        click.echo("❌ --style needs a --ref style image (and -p/--prompt for the content)", err=True)
        raise SystemExit(2)

    if modify:
        resolved_model = model or default_modify_model(op)
    else:
        resolved_model = model or select_model(tier)

    # Capability check — raises SystemExit(2) if the model can't perform op.
    _validate_or_exit(resolved_model or DEFAULT_MODEL, op, n_refs=len(ref))

    # Ref roles: parse `path:role` specs, validate against the model, and label each
    # typed ref in the prompt (the only mechanism — no backend has a per-ref role
    # field). Untyped refs produce no annotation, so output is unchanged from before.
    eff_prompt, ref_paths = prompt, list(ref)
    if not modify:
        try:
            parsed_refs = [parse_ref(r) for r in ref]
            validate_ref_roles(resolved_model or DEFAULT_MODEL, [role for _, role in parsed_refs])
        except CapabilityError as e:
            click.echo(f"❌ {e}", err=True)
            raise SystemExit(2) from e
        ref_paths = [p for p, _ in parsed_refs]
        annotation = role_annotation(parsed_refs)
        if annotation:
            eff_prompt = f"{prompt}\n\n{annotation}"

    from nazca.errors import BackendError

    try:
        if modify:
            result = modify_image(
                out, source, op=op, model=resolved_model, prompt=prompt, mask=mask,
                upscale_factor=upscale_factor, expand=expand, dry_run=dry_run,
            )
        else:
            result = generate_image(
                out, eff_prompt, ref=ref_paths or None, model=resolved_model,
                aspect_ratio=aspect_ratio, size=size, quality=quality, output_format=output_format,
                transparent=transparent,
                # only force `op` for ops a backend can't infer (style); leave None for
                # t2i/i2i/compose so the fal backend keeps treating op=None as "generate".
                op=("style" if op == "style" else None),
                dry_run=dry_run,
            )
    except BackendError as e:  # 429s/auth/HTTP errors → clean one-liner, not a traceback
        _emit_backend_error(e)
    _emit_image_result(result, dry_run, modify, resolved_model, DEFAULT_MODEL, aspect_ratio, size, quality)


@cli.command(name="try-on")
@click.argument("person", type=click.Path(exists=True, dir_okay=False))
@click.argument("garments", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--out", required=True, help="Output image path (.png).")
@click.option("--model", default=None, help="Try-on model shorthand (default: try-on).")
@click.option("--dry-run", is_flag=True, help="Print the planned request; no API call.")
def try_on(person, garments, out, model, dry_run):
    """Virtual try-on: dress PERSON in one or more GARMENT images.

    \b
      nazca try-on me.jpg dress.jpg -o look.png
      nazca try-on me.jpg top.jpg bottom.jpg -o look.png
    """
    from nazca.errors import BackendError
    from nazca.image import DEFAULT_MODEL, DEFAULT_TRYON_MODEL, try_on_image

    resolved_model = model or DEFAULT_TRYON_MODEL

    # Capability check — raises SystemExit(2) if the model can't perform try_on.
    _validate_or_exit(resolved_model, "try_on", n_refs=len(garments))

    try:
        result = try_on_image(
            out, person, garments, model=resolved_model, dry_run=dry_run,
        )
    except BackendError as e:  # 429s/auth/HTTP errors → clean one-liner, not a traceback
        _emit_backend_error(e)
    _emit_image_result(
        result, dry_run, modify=True, resolved_model=resolved_model,
        default_model=DEFAULT_MODEL, aspect_ratio="1:1", size="2K", quality="high",
    )


@cli.command()
@click.argument("source", type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--out", required=True, help="Output image path.")
@click.option("--lut", required=True, help="Look: a name (resolved in $NAZCA_LUT_DIR / ~/.config/nazca/luts) or a path to a .cube / HALD .png.")
@click.option("--strength", default=1.0, type=click.FloatRange(0, 1), help="Blend graded↔original (1.0 = full grade).")
@click.option("--grain", default=0.0, type=click.FloatRange(0, 1), help="Monochrome film grain intensity (0 = off).")
@click.option("--grain-size", "grain_size", default=1, type=click.IntRange(1, 4), help="Grain coarseness (1 = fine).")
def grade(source, out, lut, strength, grain, grain_size):
    """Apply a color LUT to an image (local, free, deterministic)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    from nazca.grade import apply_grade, load_lut

    try:
        table = load_lut(lut)
        # exif_transpose honors camera orientation so the visual top is the
        # real top (keeps later head-safe crops correct on EXIF-rotated inputs).
        with Image.open(source) as src:
            img = ImageOps.exif_transpose(src)
        apply_grade(
            img, table, strength=strength, grain=grain, grain_size=grain_size
        ).save(out)
    except (ValueError, OSError, UnidentifiedImageError) as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(2) from e
    click.echo(f"✅ {out}")


@cli.command(name="format")
@click.argument("source", type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--out", required=True, help="Output image path.")
@click.option(
    "--preset",
    required=True,
    type=click.Choice(["9:16", "4:5", "1:1", "2:3", "16:9"]),
    help="Target platform aspect.",
)
@click.option(
    "--gravity",
    default="north",
    type=click.Choice(["center", "north", "south"]),
    help="Vertical anchor for portrait crops (north keeps heads).",
)
def format_cmd(source, out, preset, gravity):
    """Head-safe crop to a platform aspect preset (local, free, deterministic)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    from nazca.grade import crop_to_preset

    try:
        # exif_transpose so "north" anchors on the visual top of EXIF-rotated
        # inputs — otherwise a head-safe crop could trim the wrong edge.
        with Image.open(source) as src:
            img = ImageOps.exif_transpose(src)
        crop_to_preset(img, preset, gravity=gravity).save(out)
    except (ValueError, OSError, UnidentifiedImageError) as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(2) from e
    click.echo(f"✅ {out}")


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
@click.option("--quality", default=None, type=click.Choice(["low", "medium", "high", "auto"]), help="gpt-image-2 only: cost/speed lever for rows that omit it (medium ≈ 4× cheaper/faster than high).")
@click.option("--concurrency", default=None, type=int, help="Max concurrent model lanes (default: one per model).")
@click.option("--vertex-batch", "vertex_batch", is_flag=True, help="Use async Vertex Batch (no RPM wall, −50%, 1K only). Needs --gcs.")
@click.option("--gcs", "gcs", default=None, help="gs://bucket/prefix for Vertex Batch input/output (with --vertex-batch).")
@click.option("--max-cost", "max_cost", default=None, type=float,
              help="Budget ceiling in USD: refuse to dispatch if the estimated plan cost exceeds it.")
@click.option("--status", "show_status", is_flag=True,
              help="Verify only: diff each row's `out` against the filesystem, report done/pending, exit 1 if any pending. No API calls.")
@click.option("--dry-run", is_flag=True, help="Print the plan + per-row requests; no API calls.")
def batch_cmd(manifest, from_dir, prompt, out_dir, rpm, models, aspect, size, quality, concurrency, vertex_batch, gcs, max_cost, show_status, dry_run):
    """Generate many images, paced per model lane (idempotent + resumable).

    Two input modes:

    \b
      nazca batch jobs.jsonl                     # manifest: one row per image
      nazca batch --from-dir refs/ --prompt "…"  # one row per ref image in a dir

    Rows already present at their `out` path are skipped, so a re-run only fills
    gaps. Pacing is chosen per-lane from the model's backend: Vertex lanes keep
    the 2/min-per-base-model start throttle (N models ≈ N×rpm combined), while
    latency-bound gpt-image-2 (openai) lanes run rows concurrently — no rpm wall.
    Throughput scales with *model lanes*, not local processes: the cap is per-model
    rpm, so running the same model in parallel shells just 429s one shared lane.

    \b
    Manifest rows are JSONL (one JSON object per line) or CSV, with fields:
      out      (req)  output image path, e.g. "out/img01.png"   [alias: output]
      prompt   (req)  generation prompt
      ref      (opt)  one ref path, a list, or ";"/"|"-joined    [alias: refs]
      model    (opt)  model shorthand; falls back to the run default
      aspect   (opt)  aspect ratio, e.g. "9:16"                  [alias: aspect_ratio]
      size     (opt)  1K|2K|4K (gemini-3 only; --vertex-batch forces 1K)
      quality  (opt)  low|medium|high|auto (gpt-image-2 only)
    CLI --aspect/--size/--quality supply defaults for rows that omit them.

    \b
    --quality (low|medium|high|auto) is the gpt-image-2 cost/speed lever and is
    ignored by other backends. --vertex-batch routes Gemini-image rows through
    async Vertex Batch inference (no per-minute quota, 50% cheaper, 1K-only
    output) via a --gcs bucket. --status re-checks a manifest against the
    filesystem after a run (use it to catch rows that died mid-batch).
    """
    from nazca.batch import (
        BatchError,
        batch_status,
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
                from_dir, prompt, out_dir, models=model_list,
                aspect=aspect, size=size, quality=quality,
            )
            only = None  # --models already applied as fan-out
        elif manifest:
            defaults = {"aspect": aspect, "size": size, "quality": quality}
            rows = load_manifest(manifest, defaults=defaults)
            only = set(model_list) if model_list else None
        else:
            raise BatchError("provide a MANIFEST path or --from-dir")
    except BatchError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(2) from e

    # --- Status / verify (no API calls) ----------------------------------
    if show_status:
        if only:  # honor the manifest --models filter so status matches what a run would do
            rows = [r for r in rows if (r.model or DEFAULT_MODEL) in only]
        status = batch_status(rows)
        for line in status.summary_lines():
            click.echo(line)
        raise SystemExit(1 if status.pending else 0)

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

    # Budget gate: refuse to dispatch a plan that costs more than --max-cost. The
    # check is on the *priced* total, so unpriced rows (raw ids / video) can't be
    # bounded — we say so rather than pretend the ceiling is a guarantee.
    if max_cost is not None:
        pc = plan.cost()
        caveat = f" (+{pc.unpriced} row(s) unpriced — actual may be higher)" if pc.unpriced else ""
        if pc.total_usd > max_cost:
            verb = "would exceed" if dry_run else "exceeds"
            click.echo(f"❌ estimated cost {pc.label()} {verb} --max-cost ${max_cost:.2f}{caveat}", err=True)
            if not dry_run:
                click.echo("   nothing dispatched — raise --max-cost or trim the batch.", err=True)
                raise SystemExit(2)
        else:
            click.echo(f"  within --max-cost ${max_cost:.2f}{caveat}")

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
        elif stage == "submitted":
            # Surface the job id + output dir so a killed/expired run is recoverable.
            click.echo(f"  🔖 job {detail['job_name']} [{detail['location']}] → {detail['output_prefix']}")
        elif stage == "reauth":
            click.echo(f"  🔑 token expired mid-run — re-authenticated and retrying ({detail})")
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
        return

    # Real run: report what was written and surface per-row failures (e.g. a
    # safety-filtered row) by their CORRECT identity — not a buried stderr line.
    written = summary.get("written", 0)
    errors = summary.get("errors", []) or []
    click.echo(f"  ✅ {written} image(s) written · {len(errors)} failed")
    for err in errors:
        click.echo(f"  ❌ {err}", err=True)
    if errors:
        raise SystemExit(1)
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
@click.argument("source", required=False, type=click.Path())
@click.option("-o", "--out", required=True, help="Output video path (.mp4).")
@click.option("-s", "--start", default=None, help="Start frame image. Omit for text-to-video (t2v).")
@click.option("-p", "--prompt", default=None, help="Motion prompt (optional for --reframe).")
@click.option("--end", default=None, help="Optional end frame (keyframe interpolation).")
@click.option("--reframe", "do_reframe", is_flag=True, help="Re-aspect a SOURCE video URL to --aspect (fal luma ray-2).")
@click.option("--v2v", "do_v2v", is_flag=True, help="Restyle/edit a SOURCE video URL from -p (fal wan-vace).")
@click.option("--extend", "do_extend", is_flag=True, help="Extend a SOURCE video URL by --duration 5|8s (fal pixverse). Needs -p.")
@click.option("--motion-control", "do_motion", is_flag=True, help="Motion-transfer: drive a SOURCE video URL's motion (Atlas Kling motion-control).")
@click.option("--video-upscale", "do_vupscale", is_flag=True, help="Upscale a SOURCE video URL to higher resolution (Atlas video-upscaler).")
@click.option("--effects", "do_effects", is_flag=True, help="Apply an effect template to a --start image (Atlas Kling effects).")
@click.option("--ref2v", "do_ref2v", is_flag=True, help="Reference-to-video: drive generation from --ref image(s) (Atlas ref2v models).")
@click.option("--ref", "ref", multiple=True, help="Reference image for --ref2v. Repeatable.")
@click.option("--avatar", "do_avatar", is_flag=True, help="Lip-sync talking head: animate a --start portrait with --audio-in (Atlas InfiniteTalk/OmniHuman/Kling avatar).")
@click.option("--audio-in", "audio_in", default=None, type=click.Path(), help="Driving audio track for --avatar (path or URL).")
@click.option("--model", default=None, help="Veo model (default: veo-3.1-fast-generate-001).")
@click.option("--duration", default=8, type=int, help="Seconds (Veo: 4/6/8; extend: 5 or 8 added).")
@click.option("--aspect", "aspect_ratio", default="9:16", help="9:16 or 16:9 (reframe: target aspect).")
@click.option("--resolution", default="720p", help="720p | 1080p.")
@click.option("--audio", is_flag=True, help="Let Veo generate audio.")
@click.option("--tier", default=None, type=click.Choice(["cheap", "premium"]), help="Cost tier: pick cheap or premium default model. Ignored when --model is given.")
@click.option("--dry-run", is_flag=True, help="Write request JSON; no API call / no credits.")
def video(source, out, start, prompt, end, do_reframe, do_v2v, do_extend, do_motion, do_vupscale, do_effects, do_ref2v, ref, do_avatar, audio_in, model, duration, aspect_ratio, resolution, audio, tier, dry_run):
    """Generate or edit a video.

    \b
      nazca video -p "..."                     # t2v
      nazca video -p "..." --start s.png       # i2v
      nazca video -p "..." --start s.png --end e.png  # keyframe
      nazca video CLIP_URL --reframe --aspect 9:16    # reframe a source video
      nazca video CLIP_URL --v2v -p "make it neon"    # restyle a source video
      nazca video CLIP_URL --extend -p "..." --duration 8  # lengthen a clip
    """
    from nazca import config
    from nazca.capabilities import infer_video_op
    from nazca.video import (
        VEO_ALIASES,
        VIDEO_EDIT_OPS,
        VeoError,
        default_video_edit_model,
        edit_video,
        generate_video,
        select_model,
        video_cost_label,
    )

    # At most one op selector.
    if sum([do_reframe, do_v2v, do_extend, do_motion, do_vupscale, do_effects, do_ref2v, do_avatar]) > 1:
        click.echo(
            "❌ choose one op: --reframe/--v2v/--extend/--motion-control/--video-upscale/--effects/--ref2v/--avatar",
            err=True,
        )
        raise SystemExit(2)

    op = infer_video_op(
        bool(start), bool(end), reframe=do_reframe, v2v=do_v2v, extend=do_extend,
        motion_control=do_motion, video_upscale=do_vupscale, effects=do_effects, ref2v=do_ref2v,
        avatar=do_avatar,
    )
    in_edit_ops = op in VIDEO_EDIT_OPS

    # ---- Atlas ref2v / effects / avatar: frame-style ops (--ref / --start / --audio-in) ----
    if op in ("ref2v", "effects", "avatar"):
        if source:
            click.echo("❌ a positional SOURCE is for source-video ops; ref2v/effects/avatar use --ref/--start/--audio-in", err=True)
            raise SystemExit(2)
        if op == "ref2v" and not ref:
            click.echo("❌ --ref2v needs at least one --ref image", err=True)
            raise SystemExit(2)
        if op == "effects" and not start:
            click.echo("❌ --effects needs a --start image", err=True)
            raise SystemExit(2)
        if op == "avatar" and not (start and audio_in):
            click.echo("❌ --avatar needs a --start portrait and --audio-in audio", err=True)
            raise SystemExit(2)
        resolved_model = model or select_model(tier)
        _validate_or_exit(resolved_model, op, n_refs=len(ref))
        result = generate_video(
            out, start, prompt or "", model=resolved_model, duration=duration,
            aspect_ratio=aspect_ratio, resolution=resolution, op=op,
            refs=list(ref) or None, audio_path=audio_in, dry_run=dry_run,
        )
        _emit_video_result(result, dry_run)
        return

    # ---- validate inputs (raises SystemExit(2) on conflict) ----------------
    _validate_video_inputs(source, start, end, prompt, op, in_edit_ops)

    # ---- video-edit ops (source VIDEO → video) -----------------------------
    if in_edit_ops:
        resolved_model = model or default_video_edit_model(op)
        _validate_or_exit(resolved_model, op)
        try:
            result = edit_video(
                out, source, op=op, model=resolved_model,
                aspect_ratio=aspect_ratio, prompt=prompt, duration=duration, dry_run=dry_run,
            )
        except VeoError as e:  # URL-only / bad-duration → clean error, not a traceback
            click.echo(f"❌ {e}", err=True)
            raise SystemExit(2) from e
        _emit_video_result(result, dry_run)
        return

    # ---- frame ops (t2v / i2v / keyframe) ----------------------------------
    # --model wins; --tier only supplies a default when --model is absent
    resolved_model = model or select_model(tier)
    # For validation, resolve the implicit default (config.VEO_MODEL is a raw Veo
    # id) to its shorthand so the default path is validated too. Unknown ids no-op.
    validate_target = resolved_model or {v: k for k, v in VEO_ALIASES.items()}.get(config.VEO_MODEL)
    _validate_or_exit(validate_target, op)

    from nazca.errors import BackendError

    # NB: do NOT pass `op` here — fal treats a non-None req.op as a video-EDIT op.
    # The atlas backend infers t2v/i2v/keyframe from start/end itself.
    try:
        result = generate_video(
            out, start, prompt, end=end, model=resolved_model, duration=duration,
            aspect_ratio=aspect_ratio, resolution=resolution,
            generate_audio=audio, dry_run=dry_run,
        )
    except BackendError as e:  # 429s/auth/HTTP errors → clean one-liner, not a traceback
        _emit_backend_error(e)
    _emit_video_result(result, dry_run)
    if dry_run:
        cost = video_cost_label(validate_target, duration=duration, resolution=resolution, audio=audio)
        if cost:
            click.echo(f"💵 {cost}")


@cli.command()
@click.argument("text", required=True)
@click.option("-o", "--out", required=True, help="Output audio path (.mp3/.wav).")
@click.option("--model", default=None, help="TTS model (default: atlas-tts-grok). Also: atlas-tts-elevenlabs-v3.")
@click.option("--voice", default=None, help="Named voice (model-specific).")
@click.option("--format", "output_format", default="mp3", type=click.Choice(["mp3", "wav"]), help="Audio container.")
@click.option("--tier", default=None, type=click.Choice(["cheap", "premium"]), help="Cost tier when --model is absent.")
@click.option("--dry-run", is_flag=True, help="Write the planned request; no API call.")
def speak(text, out, model, voice, output_format, tier, dry_run):
    """Synthesize speech from TEXT (text-to-speech).

    \b
      nazca speak "Hello world" -o hi.mp3
      nazca speak "..." -o v.mp3 --model atlas-tts-elevenlabs-v3 --voice rachel
    """
    from nazca.audio import AudioError, audio_cost_label, select_audio_model
    from nazca.audio import speak as _speak

    resolved_model = model or select_audio_model(tier)
    try:
        result = _speak(
            out, text, model=resolved_model, voice=voice,
            output_format=output_format, dry_run=dry_run,
        )
    except AudioError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(2) from e
    if dry_run:
        click.echo(f"📝 {result}")
        cost = audio_cost_label(resolved_model or "atlas-tts-grok", chars=len(text))
        if cost:
            click.echo(f"💵 {cost}")
    else:
        click.echo(f"✅ {result}")


@cli.command(name="make3d")
@click.argument("prompt", required=False)
@click.option("-o", "--out", required=True, help="Output 3D asset path (.glb).")
@click.option("--image", "source", default=None, type=click.Path(), help="Input image → image-to-3D (i23d). Omit for text-to-3D (t23d).")
@click.option("--model", default=None, help="3D model (default: atlas-hunyuan3d-rapid). Also: atlas-hunyuan3d-pro, atlas-seed3d-2.")
@click.option("--tier", default=None, type=click.Choice(["cheap", "premium"]), help="Cost tier when --model is absent.")
@click.option("--dry-run", is_flag=True, help="Write the planned request; no API call.")
def make3d(prompt, out, source, model, tier, dry_run):
    """Generate a 3D asset (GLB) from text PROMPT or an --image.

    \b
      nazca make3d "a red sports car" -o car.glb
      nazca make3d -o chair.glb --image chair.png --model atlas-seed3d-2
    """
    from nazca.threed import ThreeDError, make_3d, select_3d_model, threed_cost_label

    if not prompt and not source:
        click.echo("❌ make3d needs a text PROMPT or an --image", err=True)
        raise SystemExit(2)
    resolved_model = model or select_3d_model(tier)
    op = "i23d" if source else "t23d"
    _validate_or_exit(resolved_model, op)
    try:
        result = make_3d(out, prompt or "", source=source, model=resolved_model, dry_run=dry_run)
    except ThreeDError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(2) from e
    if dry_run:
        click.echo(f"📝 {result}")
        cost = threed_cost_label(resolved_model or "atlas-hunyuan3d-rapid")
        if cost:
            click.echo(f"💵 {cost}")
    else:
        click.echo(f"✅ {result}")


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
    from nazca.models import is_verified
    from nazca.registry import all_overrides, models_path
    from nazca.video import (
        ARK_VIDEO_MODELS,
        FAL_VIDEO_MODELS,
        VEO_ALIASES,
        VIDEO_EDIT_MODELS,
        VIDEO_MODEL_TIERS,
    )

    ov = all_overrides()
    img_ov = ov.get("image", {})
    vid_ov = ov.get("video", {})

    SH = 20
    BE = 12
    TI = 10
    ID = 32
    OP = 28  # ops column width (padded so the verify marker aligns)

    def _hdr():
        click.echo(f"  {'shorthand':<{SH}} {'backend':<{BE}} {'tier':<{TI}} {'model id':<{ID}} {'ops':<{OP}}")
        click.echo(f"  {'-'*SH} {'-'*BE} {'-'*TI} {'-'*ID} {'-'*OP}")

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
        warn = "" if is_verified(be) else "⚠"
        click.echo(f"  {sh:<{SH}} {marker}{be:<{BE}} {tier:<{TI}} {mid:<{ID}} {ops_str(sh):<{OP}} {warn}")

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
    for sh, fal_id in VIDEO_EDIT_MODELS.items():
        all_vid[sh] = (fal_id, "fal")

    for sh, entry in vid_ov.items():
        mid = entry.get("id", sh)
        be = entry.get("backend", "vertex")
        all_vid[sh] = (mid, be)

    for sh in sorted(all_vid):
        mid, be = all_vid[sh]
        tier = vid_ov.get(sh, {}).get("tier") or VIDEO_MODEL_TIERS.get(sh, "")
        marker = "*" if sh in vid_ov else " "
        warn = "" if is_verified(be) else "⚠"
        click.echo(f"  {sh:<{SH}} {marker}{be:<{BE}} {tier:<{TI}} {mid:<{ID}} {ops_str(sh):<{OP}} {warn}")

    # ---- footer ----
    mp = models_path()
    status = "exists" if mp.exists() else "not found"
    click.echo(f"\nOverride file: {mp} [{status}]")
    click.echo("  * = overridden by user models.json")
    click.echo("  ⚠ = cost/schema not live-verified (atlas · fal · modelark backends)")
    click.echo("  ops = supported operations (see docs/media-modalities.md)")


if __name__ == "__main__":
    cli()
