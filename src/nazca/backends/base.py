"""Backend interface — shared plumbing + per-modality capability protocols.

`Backend` is a lightweight base class carrying only the plumbing every provider
shares — credential handling, endpoint building, HTTP mechanics, image encoding.
It deliberately does NOT declare the generation methods: a backend exposes
``run_image`` / ``run_video`` / ``run_audio`` / ``run_3d`` *only* for the
modalities it actually supports (Interface Segregation).

Which modalities a backend supports is expressed by the ``@runtime_checkable``
capability protocols below (``SupportsImage`` etc.). A backend satisfies a
protocol structurally — just by defining the matching ``run_<modality>`` method —
so no explicit inheritance is needed. ``require_capability`` turns an unsupported
route into a clear ``BackendError`` at the dispatch boundary (replacing the old
unreachable NotImplementedError stubs on the base class).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from nazca.errors import BackendError

if TYPE_CHECKING:
    from nazca.request import (
        AudioRequest,
        ImageRequest,
        SpeechToSpeechRequest,
        ThreeDRequest,
        TranscriptionRequest,
        VideoRequest,
    )
    from nazca.resolve import ResolvedModel


class Backend:
    """Provider plumbing: credential + endpoint + HTTP, plus image (de)coding."""

    name: str = "base"

    def auth_token(self) -> str:
        """Mint/return this backend's credential. Called only on dispatch (lazy)."""
        raise NotImplementedError

    def build_url(self, model: str, op: str, location: str | None = None) -> str:
        """Endpoint for `model` and operation `op` (e.g. generateContent, predict)."""
        raise NotImplementedError

    def post(self, url: str, body: dict, token: str) -> dict:
        """HTTP POST a JSON body, return decoded JSON.

        Abstract: each backend implements its own POST.
        """
        raise NotImplementedError

    def encode_image_b64(
        self, path: str | Path, max_edge: int | None = None, fmt: str = "JPEG"
    ) -> tuple[str, str]:
        """Return (base64, mime) for an image, optionally downscaled to max_edge."""
        raise NotImplementedError


# --------------------------------------------------------------- capability protocols


@runtime_checkable
class SupportsImage(Protocol):
    """A backend that can generate or modify images."""

    def run_image(self, resolved: ResolvedModel, req: ImageRequest) -> bytes | dict: ...


@runtime_checkable
class SupportsVideo(Protocol):
    """A backend that can generate or edit video."""

    def run_video(self, resolved: ResolvedModel, req: VideoRequest) -> bytes | dict: ...


@runtime_checkable
class SupportsAudio(Protocol):
    """A backend that can synthesize audio (text-to-speech)."""

    def run_audio(self, resolved: ResolvedModel, req: AudioRequest) -> bytes | dict: ...


@runtime_checkable
class SupportsThreeD(Protocol):
    """A backend that can generate 3D assets (GLB)."""

    def run_3d(self, resolved: ResolvedModel, req: ThreeDRequest) -> bytes | dict: ...


@runtime_checkable
class SupportsVoiceClone(Protocol):
    """A backend that can derive a reusable voice from audio samples (issue #122 A2)."""

    def voice_clone(self, title: str, audio_paths: list[str], **kwargs: object) -> dict: ...


@runtime_checkable
class SupportsVoiceDesign(Protocol):
    """A backend that can generate voice candidates from a text description (issue #122 A2)."""

    def voice_design(self, instruction: str, **kwargs: object) -> dict: ...


@runtime_checkable
class SupportsSpeechToSpeech(Protocol):
    """A backend that can convert a source audio file into a target voice's
    speech (issue #122 phase A3 — the "voice changer" op).
    """

    def speech_to_speech(
        self, resolved: ResolvedModel, req: SpeechToSpeechRequest
    ) -> bytes | dict: ...


@runtime_checkable
class SupportsStt(Protocol):
    """A backend that can transcribe an audio file to text (issue #122 A3).

    Unlike `SupportsAudio.run_audio` (text in, audio bytes out), `run_stt`
    takes a `TranscriptionRequest` (audio file in) and returns decoded JSON
    (text in, structured out) — never raw bytes, so it isn't folded into
    `SupportsAudio`.
    """

    def run_stt(self, resolved: ResolvedModel, req: TranscriptionRequest) -> dict: ...


# modality/op key -> (capability protocol, human label for the error message)
_CAPABILITY: dict[str, tuple[type, str]] = {
    "image": (SupportsImage, "images"),
    "video": (SupportsVideo, "video"),
    "audio": (SupportsAudio, "audio"),
    "3d": (SupportsThreeD, "3D"),
    "voice_clone": (SupportsVoiceClone, "voice cloning"),
    "voice_design": (SupportsVoiceDesign, "voice design"),
    "speech_to_speech": (SupportsSpeechToSpeech, "speech-to-speech voice conversion"),
    "stt": (SupportsStt, "speech-to-text"),
}


def require_capability(backend: Backend, modality: str) -> Backend:
    """Return `backend` if it supports `modality`, else raise a clear BackendError.

    Guards the dispatch boundary: routing a modality to a backend that does not
    implement it yields a friendly error (same wording as the old base stubs)
    instead of an AttributeError.
    """
    proto, label = _CAPABILITY[modality]
    if not isinstance(backend, proto):
        raise BackendError(f"backend '{backend.name}' does not support {label}")
    return backend
