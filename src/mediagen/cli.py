"""mediagen CLI — `mediagen image` and `mediagen video`."""

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
@click.option("--ref", default=None, help="Reference image → image-to-image restyle.")
@click.option("--model", default=None, help="nano-banana | nano-banana-pro | seedream | flux | gemini-2.5-flash-image")
@click.option("--provider", default=None, type=click.Choice(["fal", "gemini"]), help="Force provider (auto from model).")
@click.option("--aspect", "aspect_ratio", default="9:16", help="Aspect ratio (fal).")
@click.option("--dry-run", is_flag=True, help="Print the planned request; no API call.")
def image(out, prompt, ref, model, provider, aspect_ratio, dry_run):
    """Generate (or restyle with --ref) one image via fal or Gemini."""
    from mediagen.image import generate_image

    result = generate_image(
        out, prompt, ref=ref, model=model, provider=provider,
        aspect_ratio=aspect_ratio, dry_run=dry_run,
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
@click.option("--dry-run", is_flag=True, help="Write request JSON; no API call / no credits.")
def video(out, start, prompt, end, model, duration, aspect_ratio, resolution, audio, dry_run):
    """Generate a Veo clip from a start frame (+ optional end frame) on Vertex."""
    from mediagen.video import generate_video

    result = generate_video(
        out, start, prompt, end=end, model=model, duration=duration,
        aspect_ratio=aspect_ratio, resolution=resolution,
        generate_audio=audio, dry_run=dry_run,
    )
    click.echo(f"{'📝' if dry_run else '✅'} {result}")


if __name__ == "__main__":
    cli()
