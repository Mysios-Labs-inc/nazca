"""mediagen CLI — `mediagen image`, `mediagen video`, `mediagen login`, `mediagen config`."""

from __future__ import annotations

import json

import click

from mediagen import __version__


@click.group()
@click.version_option(__version__)
def cli() -> None:
    """Thin CLI for AI image + video generation. Claude-driven."""


@cli.command()
@click.option("-o", "--out", required=True, help="Output image path (.png).")
@click.option("-p", "--prompt", required=True, help="Generation prompt.")
@click.option("--ref", multiple=True, help="Reference image → image-to-image restyle. Repeatable (pro-image: up to 14).")
@click.option("--model", default=None, help="nano-banana (default,fast,ref) | nano-banana-3 (ref) | nano-banana-pro (ref, legible text, 14 refs) | imagen-4 | imagen-4-fast | imagen-3 (t2i only)")
@click.option("--aspect", "aspect_ratio", default="9:16", help="Aspect ratio.")
@click.option("--size", default="2K", type=click.Choice(["1K", "2K", "4K"]), help="Output res (gemini-3 only; 2.5-flash stays 1K).")
@click.option("--tier", default=None, type=click.Choice(["cheap", "premium"]), help="Cost tier: pick cheap or premium default model. Ignored when --model is given.")
@click.option("--dry-run", is_flag=True, help="Print the planned request; no API call.")
def image(out, prompt, ref, model, aspect_ratio, size, tier, dry_run):
    """Generate (or restyle with --ref) one image via Vertex Gemini / Imagen."""
    from mediagen.image import generate_image, select_model

    # --model wins; --tier only supplies a default when --model is absent
    resolved_model = model or select_model(tier)

    result = generate_image(
        out, prompt, ref=list(ref) or None, model=resolved_model,
        aspect_ratio=aspect_ratio, size=size, dry_run=dry_run,
    )
    if dry_run:
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(f"✅ {result}")


@cli.command()
@click.option("-o", "--out", required=True, help="Output video path (.mp4).")
@click.option("-s", "--start", required=True, help="Start frame image.")
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
    """Generate a Veo clip from a start frame (+ optional end frame) on Vertex."""
    from mediagen.video import generate_video, select_model

    # --model wins; --tier only supplies a default when --model is absent
    resolved_model = model or select_model(tier)

    result = generate_video(
        out, start, prompt, end=end, model=resolved_model, duration=duration,
        aspect_ratio=aspect_ratio, resolution=resolution,
        generate_audio=audio, dry_run=dry_run,
    )
    click.echo(f"{'📝' if dry_run else '✅'} {result}")


@cli.command()
def login() -> None:
    """Interactively store API credentials in ~/.config/mediagen/config.ini.

    Press Enter to skip a credential and leave it unchanged.
    """
    from mediagen.credstore import config_path, set_value

    click.echo("Enter credentials (press Enter to skip / leave unchanged).")

    fal_key = click.prompt(
        "  fal.ai API key (FAL_KEY)",
        hide_input=True,
        default="",
        show_default=False,
    )
    if fal_key:
        set_value("fal_key", fal_key)
        click.echo("  fal_key saved.")

    ark_key = click.prompt(
        "  ByteDance ModelArk key (ARK_API_KEY)",
        hide_input=True,
        default="",
        show_default=False,
    )
    if ark_key:
        set_value("ark_api_key", ark_key)
        click.echo("  ark_api_key saved.")

    click.echo(f"\nConfig file: {config_path()}")


@cli.group()
def config() -> None:
    """Manage mediagen credential config (~/.config/mediagen/config.ini)."""


@config.command(name="set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set a config KEY to VALUE (e.g. mediagen config set fal_key sk-...)."""
    from mediagen.credstore import KNOWN_KEYS, set_value

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
    from mediagen.credstore import KNOWN_KEYS, _key_source, mask_value

    if key not in KNOWN_KEYS:
        click.echo(
            f"Unknown key '{key}'. Known keys: {', '.join(KNOWN_KEYS)}",
            err=True,
        )
        raise SystemExit(1)
    val, source = _key_source(key)
    if val:
        click.echo(f"{key} = {mask_value(val)}  [{source}]")
    else:
        click.echo(f"{key} = (unset)")


@config.command(name="path")
def config_path_cmd() -> None:
    """Print the resolved config file path."""
    from mediagen.credstore import config_path

    click.echo(config_path())


@config.command(name="list")
def config_list() -> None:
    """List all known credentials with masked values and sources."""
    from mediagen.credstore import KNOWN_KEYS, _key_source, mask_value

    for key in KNOWN_KEYS:
        val, source = _key_source(key)
        if val:
            click.echo(f"{key} = {mask_value(val)}  [{source}]")
        else:
            click.echo(f"{key} = (unset)")


if __name__ == "__main__":
    cli()
