"""nazca — thin CLI for AI image + video generation.

image → t2i / i2i / compose, plus modify ops (upscale, bg-remove, inpaint,
        outpaint) via Vertex Gemini-Imagen, fal, and ModelArk Seedream.
video → t2v / i2v / keyframe (Vertex Veo), plus video-edit ops (reframe, v2v,
        extend) via fal. batch → paced + Vertex Batch.

Capability-aware: each model declares the ops it supports (see capabilities.py /
docs/media-modalities.md); the CLI infers the op from your flags and validates it.
Claude-driven: each command does one thing and prints the output path.
"""

__version__ = "0.4.0"
