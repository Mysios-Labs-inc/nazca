"""MCP server — exposes nazca's image + video generation over the Model Context
Protocol (stdio transport) so the Claude Desktop app can drive it.

This is a thin adapter: it does no generation logic of its own, it just maps MCP
tool calls onto :func:`nazca.image.generate_image` and
:func:`nazca.video.generate_video`. Same Vertex/fal/ModelArk backends, same auth
(each user's own `gcloud` ADC + optional FAL_KEY / ARK_API_KEY).

Run locally:        nazca-mcp
Claude Desktop:     register as an MCP server (see README → "Use with Claude Desktop").

Output files land in $NAZCA_OUTPUT_DIR (default: ~/nazca-output), since an MCP
server has no meaningful working directory of its own.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from nazca import image as image_mod
from nazca import video as video_mod

mcp = FastMCP("nazca")


def _output_dir() -> Path:
    """Where bare filenames are written.

    Priority:
      1. $NAZCA_OUTPUT_DIR if set (explicit override).
      2. The current working directory — MCP hosts (Claude Desktop / Cowork
         agent mode) launch the server with cwd set to their session/outputs
         folder, which is exactly where they surface generated files. Writing
         there makes a bare filename land where the host can show it.
      3. ~/nazca-output as a last resort (e.g. cwd is "/" or not writable, as
         can happen for a plain desktop chat launch).
    """
    env = os.getenv("NAZCA_OUTPUT_DIR")
    if env:
        d = Path(env)
    else:
        cwd = Path.cwd()
        d = cwd if (cwd != cwd.root and os.access(cwd, os.W_OK)) else Path.home() / "nazca-output"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_out(filename: str) -> Path:
    """Resolve a caller-supplied filename to a safe output path.

    Absolute paths are honored as-is (the caller knows what they want); bare
    names go in the output dir (the host's working dir by default — see
    _output_dir), so the host can surface the file in chat.
    """
    p = Path(filename).expanduser()
    if not p.is_absolute():
        p = _output_dir() / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@mcp.tool()
def list_models() -> str:
    """List the available image and video model shorthands with their tiers.

    Use this to discover what `model` values generate_image / generate_video accept.
    """
    lines = ["IMAGE MODELS:"]
    for short, tier in image_mod.MODEL_TIERS.items():
        spec = image_mod.MODELS.get(short)
        backend = spec[3] if spec else "?"
        lines.append(f"  {short:18} {tier:8} ({backend})")
    lines.append("")
    lines.append("VIDEO MODELS:")
    for short, tier in video_mod.VIDEO_MODEL_TIERS.items():
        lines.append(f"  {short:18} {tier:8}")
    lines.append("")
    lines.append("Tip: prefix 'vertex:' / 'fal:' / 'modelark:' + a raw provider id to bypass the table.")
    return "\n".join(lines)


@mcp.tool()
def generate_image(
    prompt: str,
    filename: str = "image.png",
    ref: list[str] | None = None,
    model: str | None = None,
    aspect_ratio: str = "9:16",
    size: str = "2K",
    dry_run: bool = False,
) -> list:
    """Generate (or restyle, when `ref` is given) one image and save it to disk.

    Args:
        prompt: Text description of the image to generate.
        filename: Output filename. A bare name (e.g. "cat.png") is saved in the
            current working directory, which is where the host surfaces files —
            prefer this so the image shows up in chat. An absolute path is used
            as-is. ($NAZCA_OUTPUT_DIR overrides the directory if set.)
        ref: Optional list of reference image paths. Only nano-banana (Gemini)
            models support references; nano-banana-pro accepts up to 14. Imagen
            models are text-to-image only and reject refs.
        model: Model shorthand (see list_models) or "<backend>:<raw-id>". Defaults
            to nano-banana.
        aspect_ratio: e.g. "9:16", "16:9", "1:1", "4:3", "3:4".
        size: "1K" | "2K" | "4K" (honored by gemini-3 image models only).
        dry_run: If true, return the request plan without calling any API
            (no credentials needed).

    Returns the saved path (and an inline preview of the image).
    """
    out = _resolve_out(filename)
    result = image_mod.generate_image(
        out,
        prompt,
        ref=ref,
        model=model,
        aspect_ratio=aspect_ratio,
        size=size,
        dry_run=dry_run,
    )
    if dry_run:
        import json

        return [f"DRY RUN — no API call made:\n{json.dumps(result, indent=2)}"]
    path = Path(result)
    return [f"Saved image to {path}", Image(path=str(path))]


@mcp.tool()
def generate_video(
    prompt: str,
    start: str,
    filename: str = "video.mp4",
    end: str | None = None,
    model: str | None = None,
    duration: int = 8,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
    generate_audio: bool = False,
    dry_run: bool = False,
) -> str:
    """Generate a video clip from a start frame (+ optional end frame).

    Args:
        prompt: Text description of the motion / scene.
        start: Path to the start frame image (required — nazca video is
            image-to-video).
        filename: Output filename. A bare name (e.g. "clip.mp4") is saved in the
            current working directory, which is where the host surfaces files —
            prefer this so the video shows up in chat. An absolute path is used
            as-is. ($NAZCA_OUTPUT_DIR overrides the directory if set.)
        end: Optional path to an end frame for keyframe interpolation (Vertex Veo
            and some fal models).
        model: Model shorthand (see list_models) or "<backend>:<raw-id>". Defaults
            to the configured Veo model.
        duration: Clip length in seconds.
        aspect_ratio: e.g. "9:16", "16:9".
        resolution: e.g. "720p", "1080p" (Vertex Veo).
        generate_audio: Whether to generate audio (Vertex Veo).
        dry_run: If true, write the request plan to <filename>.request.json and
            return its path without calling any API.

    Note: video generation is long-running (polls until done); expect this to
    take a while. Returns the saved path.
    """
    out = _resolve_out(filename)
    result = video_mod.generate_video(
        out,
        start,
        prompt,
        end=end,
        model=model,
        duration=duration,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        generate_audio=generate_audio,
        dry_run=dry_run,
    )
    path = Path(result)
    if dry_run:
        return f"DRY RUN — request plan written to {path}"
    return f"Saved video to {path}"


def main() -> None:
    """Entry point for the `nazca-mcp` console script (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
