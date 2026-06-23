"""Unit tests for the module-level CLI helpers in nazca.cli.

These exercise the extracted helpers directly — without going through Click's
invoke machinery — so failures produce clean assertions rather than output
string searches.
"""

from __future__ import annotations

import json

import pytest

from nazca.cli import (
    _emit_image_result,
    _emit_video_result,
    _validate_image_inputs,
    _validate_or_exit,
    _validate_video_inputs,
)

# ---------------------------------------------------------------------------
# _validate_or_exit
# ---------------------------------------------------------------------------

class TestValidateOrExit:
    def test_passes_for_known_supported_op(self):
        # nano-banana supports t2i; no exception should be raised.
        _validate_or_exit("nano-banana", "t2i", n_refs=0)

    def test_exits_2_for_unsupported_op(self, capsys):
        # nano-banana does not support 'upscale'; must echo ❌ and exit 2.
        with pytest.raises(SystemExit) as exc:
            _validate_or_exit("nano-banana", "upscale")
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "❌" in captured.err
        assert "does not support" in captured.err
        assert "upscale" in captured.err

    def test_exits_2_for_unsupported_compose(self, capsys):
        # flux-schnell supports i2i but not compose (2 refs).
        with pytest.raises(SystemExit) as exc:
            _validate_or_exit("flux-schnell", "compose", n_refs=2)
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "❌" in captured.err

    def test_none_model_does_not_crash(self):
        # Unknown / None model: validate_op is a no-op for unknown ids.
        _validate_or_exit(None, "t2i")

    def test_reframe_on_veo_model_exits_2(self, capsys):
        # Validates the video path too: veo-3.1 can't reframe.
        with pytest.raises(SystemExit) as exc:
            _validate_or_exit("veo-3.1", "reframe")
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "does not support 'reframe'" in captured.err


# ---------------------------------------------------------------------------
# _validate_image_inputs
# ---------------------------------------------------------------------------

class TestValidateImageInputs:
    """Test the mutual-exclusion rules for the image command."""

    def _call(self, **kw):
        """Helper with sensible defaults; override only what the test cares about."""
        defaults = dict(
            source=None, ref=(), do_upscale=False, do_rmbg=False,
            mask=None, do_outpaint=False, op="t2i", prompt="a cat", modify=False,
        )
        defaults.update(kw)
        _validate_image_inputs(**defaults)

    def test_valid_t2i_passes(self):
        self._call()  # no error

    def test_valid_modify_upscale_passes(self):
        self._call(source="s.png", do_upscale=True, op="upscale", prompt=None, modify=True)

    def test_two_modify_flags_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self._call(source="s.png", do_upscale=True, do_rmbg=True, modify=True)
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "choose one modify op" in captured.err

    def test_modify_without_source_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self._call(source=None, do_upscale=True, op="upscale", modify=True)
        assert exc.value.code == 2
        assert "needs a SOURCE" in capsys.readouterr().err

    def test_modify_with_ref_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self._call(source="s.png", ref=("r.png",), do_upscale=True, op="upscale", modify=True)
        assert exc.value.code == 2
        assert "--ref is not used" in capsys.readouterr().err

    def test_inpaint_without_prompt_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self._call(source="s.png", mask="m.png", op="inpaint", prompt=None, modify=True)
        assert exc.value.code == 2
        assert "inpaint needs -p" in capsys.readouterr().err

    def test_gen_with_source_exits_2(self, capsys):
        # Positional source with a gen op is a usage mistake.
        with pytest.raises(SystemExit) as exc:
            self._call(source="s.png", op="t2i", modify=False)
        assert exc.value.code == 2
        assert "only for modify ops" in capsys.readouterr().err

    def test_gen_without_prompt_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self._call(prompt=None, modify=False)
        assert exc.value.code == 2
        assert "prompt is required" in capsys.readouterr().err

    def test_exact_message_choose_one_modify_op(self, capsys):
        """Verify the exact error string consumers depend on."""
        with pytest.raises(SystemExit):
            self._call(source="s.png", do_upscale=True, do_outpaint=True, modify=True)
        msg = capsys.readouterr().err
        assert msg.startswith("❌ choose one modify op: --upscale / --rmbg / --mask (inpaint) / --outpaint")


# ---------------------------------------------------------------------------
# _validate_video_inputs
# ---------------------------------------------------------------------------

class TestValidateVideoInputs:
    """Test the mutual-exclusion rules for the video command."""

    def _edit(self, **kw):
        defaults = dict(source="https://cdn/c.mp4", start=None, end=None, prompt=None, op="reframe", in_edit_ops=True)
        defaults.update(kw)
        _validate_video_inputs(**defaults)

    def _frame(self, **kw):
        defaults = dict(source=None, start=None, end=None, prompt="fly", op="t2v", in_edit_ops=False)
        defaults.update(kw)
        _validate_video_inputs(**defaults)

    # edit ops
    def test_valid_reframe_passes(self):
        self._edit()

    def test_valid_v2v_passes(self):
        self._edit(op="v2v", prompt="neon")

    def test_edit_without_source_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self._edit(source=None)
        assert exc.value.code == 2
        assert "needs a SOURCE" in capsys.readouterr().err

    def test_edit_with_start_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self._edit(start="s.png")
        assert exc.value.code == 2
        assert "are for frame ops" in capsys.readouterr().err

    def test_v2v_without_prompt_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self._edit(op="v2v", prompt=None)
        assert exc.value.code == 2
        assert "needs -p" in capsys.readouterr().err

    def test_extend_without_prompt_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self._edit(op="extend", prompt=None)
        assert exc.value.code == 2
        assert "needs -p" in capsys.readouterr().err

    # frame ops
    def test_valid_t2v_passes(self):
        self._frame()

    def test_frame_with_source_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self._frame(source="https://cdn/c.mp4")
        assert exc.value.code == 2
        assert "only for video-edit ops" in capsys.readouterr().err

    def test_end_without_start_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self._frame(end="e.png", start=None)
        assert exc.value.code == 2
        assert "--end requires --start" in capsys.readouterr().err

    def test_frame_without_prompt_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self._frame(prompt=None)
        assert exc.value.code == 2
        assert "prompt is required" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _emit_image_result
# ---------------------------------------------------------------------------

class TestEmitImageResult:
    DEFAULT_MODEL = "nano-banana"

    def _call(self, result, dry_run, modify, resolved_model=None, **kw):
        defaults = dict(aspect_ratio="9:16", size="2K", quality="high")
        defaults.update(kw)
        _emit_image_result(
            result, dry_run, modify,
            resolved_model or self.DEFAULT_MODEL, self.DEFAULT_MODEL,
            defaults["aspect_ratio"], defaults["size"], defaults["quality"],
        )

    def test_dry_run_prints_json(self, capsys):
        payload = {"url": "http://x", "model": "nano-banana"}
        self._call(payload, dry_run=True, modify=False)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["url"] == "http://x"

    def test_success_prints_checkmark(self, capsys, tmp_path):
        out_path = tmp_path / "out.png"
        self._call(out_path, dry_run=False, modify=True)
        out = capsys.readouterr().out
        assert f"✅ {out_path}" in out

    def test_success_gen_emits_cost_when_available(self, capsys, tmp_path, monkeypatch):
        # image_cost_label returns a non-empty string → 💵 line must appear.
        import nazca.image as img_mod
        monkeypatch.setattr(img_mod, "image_cost_label", lambda *a, **kw: "$0.013 / image")
        out_path = tmp_path / "out.png"
        self._call(out_path, dry_run=False, modify=False)
        out = capsys.readouterr().out
        assert "✅" in out
        assert "💵 $0.013 / image" in out

    def test_success_modify_does_not_emit_cost(self, capsys, tmp_path, monkeypatch):
        # modify ops never show a cost line even if image_cost_label would return one.
        import nazca.image as img_mod
        monkeypatch.setattr(img_mod, "image_cost_label", lambda *a, **kw: "$0.013 / image")
        out_path = tmp_path / "out.png"
        self._call(out_path, dry_run=False, modify=True)
        out = capsys.readouterr().out
        assert "💵" not in out

    def test_success_gen_no_cost_when_label_empty(self, capsys, tmp_path, monkeypatch):
        import nazca.image as img_mod
        monkeypatch.setattr(img_mod, "image_cost_label", lambda *a, **kw: "")
        out_path = tmp_path / "out.png"
        self._call(out_path, dry_run=False, modify=False)
        out = capsys.readouterr().out
        assert "💵" not in out

    def test_dry_run_does_not_print_checkmark(self, capsys):
        self._call({"k": "v"}, dry_run=True, modify=False)
        out = capsys.readouterr().out
        assert "✅" not in out


# ---------------------------------------------------------------------------
# _emit_video_result
# ---------------------------------------------------------------------------

class TestEmitVideoResult:
    def test_dry_run_prints_memo_emoji(self, capsys, tmp_path):
        out_path = tmp_path / "o.request.json"
        _emit_video_result(out_path, dry_run=True)
        out = capsys.readouterr().out
        assert f"📝 {out_path}" in out

    def test_real_run_prints_checkmark(self, capsys, tmp_path):
        out_path = tmp_path / "o.mp4"
        _emit_video_result(out_path, dry_run=False)
        out = capsys.readouterr().out
        assert f"✅ {out_path}" in out

    def test_dry_run_does_not_print_checkmark(self, capsys, tmp_path):
        out_path = tmp_path / "o.request.json"
        _emit_video_result(out_path, dry_run=True)
        assert "✅" not in capsys.readouterr().out

    def test_real_run_does_not_print_memo_emoji(self, capsys, tmp_path):
        out_path = tmp_path / "o.mp4"
        _emit_video_result(out_path, dry_run=False)
        assert "📝" not in capsys.readouterr().out
