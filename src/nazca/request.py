"""Typed request objects for the backend seam.

`generate_image` / `modify_image` / `generate_video` / `edit_video` resolve a
model spec and then hand a single request dataclass to the backend, which owns
all body-building, dispatch, extraction, and dry-run plan rendering. This is what
lets the call sites collapse the old `if backend_name == ...` ladders into one
`backend.run_image(...)` / `backend.run_video(...)` call.

The fields mirror the original public function parameters exactly — no new knobs.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImageRequest:
    """Everything a backend needs to render or modify one image.

    `op` is None for plain generation; for source-image modify ops it is one of
    image.MODIFY_OPS ("upscale" | "bg_remove" | "inpaint" | "outpaint"), in which
    case `source` is the input image and `mask`/`upscale_factor`/`expand` apply.

    `est_cost_usd` is precomputed by the orchestrator (cost estimation is keyed by
    the user-facing shorthand, which the backend does not see) and echoed into the
    generation dry-run plan.
    """

    prompt: str = ""
    refs: list[str] = field(default_factory=list)
    aspect_ratio: str | None = None
    size: str | None = None
    quality: str | None = None
    output_format: str | None = None
    transparent: bool = False
    # modify ops (op is None for plain generation)
    op: str | None = None
    source: str | None = None
    mask: str | None = None
    upscale_factor: int = 2
    expand: int = 256
    # precomputed estimate for the dry-run plan
    est_cost_usd: float | None = None
    dry_run: bool = False


@dataclass
class VideoRequest:
    """Everything a backend needs to generate or edit one video clip.

    `op` is None for plain generation (t2v / i2v / keyframe inferred from
    start/end); for video-edit ops it is one of video.VIDEO_EDIT_OPS
    ("reframe" | "v2v" | "extend"), in which case `source` is the input clip URL.
    """

    prompt: str = ""
    start: str | None = None
    end: str | None = None
    refs: list[str] = field(default_factory=list)
    aspect_ratio: str = "9:16"
    resolution: str = "720p"
    duration: int = 8
    audio: bool = False
    # avatar / lip-sync: a driving audio track (image + audio → talking-head video)
    audio_path: str | None = None
    # edit ops (op is None for plain generation)
    op: str | None = None
    source: str | None = None
    dry_run: bool = False


@dataclass
class AudioRequest:
    """Everything a backend needs to synthesize one audio clip (text-to-speech).

    `op` is "tts" today (text → speech). `voice` selects a named voice; `output_format`
    is the container (mp3/wav). `est_cost_usd` is precomputed and echoed into the plan.
    """

    text: str = ""
    voice: str | None = None
    output_format: str = "mp3"
    op: str = "tts"
    est_cost_usd: float | None = None
    dry_run: bool = False


@dataclass
class ThreeDRequest:
    """Everything a backend needs to generate one 3D asset (GLB mesh).

    `op` is "t23d" (text → 3D) or "i23d" (image → 3D); `source` is the input image
    for i23d. `est_cost_usd` is precomputed and echoed into the plan.
    """

    prompt: str = ""
    source: str | None = None
    op: str = "t23d"
    est_cost_usd: float | None = None
    dry_run: bool = False
