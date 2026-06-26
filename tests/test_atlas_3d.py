"""Atlas Cloud 3D (GLB) modality — dry-run plan + cost (PR7)."""

from __future__ import annotations

import json

from click.testing import CliRunner
from PIL import Image

from nazca.cli import cli
from nazca.cost import estimate_3d_cost
from nazca.threed import make_3d


def _png(path):
    Image.new("RGB", (16, 16), (40, 80, 120)).save(path)
    return str(path)


def test_make3d_t23d_dry_run(tmp_path):
    plan_path = make_3d(tmp_path / "a.glb", "a red car", model="atlas-hunyuan3d-rapid", dry_run=True)
    plan = json.loads(plan_path.read_text())
    assert plan["model"] == "tencent/hunyuan3d-rapid/text-to-3d"
    assert plan["body"]["prompt"] == "a red car"


def test_make3d_i23d_dry_run(tmp_path):
    plan_path = make_3d(
        tmp_path / "a.glb", source=_png(tmp_path / "s.png"), model="atlas-seed3d-2", dry_run=True
    )
    plan = json.loads(plan_path.read_text())
    assert plan["model"] == "bytedance/seed3d-v2.0/image-to-3d"
    assert plan["body"]["image_url"].startswith("<data-uri ")


def test_3d_cost():
    assert estimate_3d_cost("atlas-seed3d-2").usd == 0.353
    assert estimate_3d_cost("nope") is None


def test_cli_make3d_dry_run(tmp_path):
    r = CliRunner().invoke(cli, ["make3d", "a chair", "-o", str(tmp_path / "c.glb"), "--dry-run"])
    assert r.exit_code == 0
    assert "💵" in r.output
