"""nazca — thin CLI for AI image + video generation.

image → t2i / i2i / compose, plus modify ops (upscale, bg-remove, inpaint,
        outpaint) via Vertex Gemini-Imagen, fal, and ModelArk Seedream.
video → t2v / i2v / keyframe (Vertex Veo), plus video-edit ops (reframe, v2v,
        extend) via fal. batch → paced + Vertex Batch.

Capability-aware: each model declares the ops it supports (see capabilities.py /
docs/media-modalities.md); the CLI infers the op from your flags and validates it.
Claude-driven: each command does one thing and prints the output path.

Public library API (importable as ``from nazca import ...``):
    generate_image, modify_image  — image generation and modification
    generate_video, edit_video    — video generation and editing
    ModelSpec                     — typed model specification dataclass
    BackendError, RateLimitError  — shared exception hierarchy
"""

__version__ = "0.9.0"

# Public API — only light-weight, always-available symbols here.
# Heavy CLI deps (questionary, mcp) are NOT imported at this level.
from nazca.errors import BackendError, RateLimitError
from nazca.image import generate_image, modify_image
from nazca.models import ModelSpec
from nazca.video import edit_video, generate_video

__all__ = [
    "__version__",
    "generate_image",
    "modify_image",
    "generate_video",
    "edit_video",
    "ModelSpec",
    "BackendError",
    "RateLimitError",
]
