"""Atlas Cloud per-second video cost-table coverage (PR2)."""

from __future__ import annotations


def test_atlas_video_cost():
    from nazca.cost import estimate_video_cost

    # atlas-seedance-2-mini: flat $0.056/s — audio flag makes no difference
    est = estimate_video_cost("atlas-seedance-2-mini", duration=8, resolution="720p", audio=False)
    assert est is not None
    assert est.usd == round(0.056 * 8, 4)

    est_audio = estimate_video_cost("atlas-seedance-2-mini", duration=8, resolution="1080p", audio=True)
    assert est_audio is not None
    assert est_audio.usd == round(0.056 * 8, 4)

    # atlas-kling-v3-turbo: flat $0.095/s
    est = estimate_video_cost("atlas-kling-v3-turbo", duration=10, resolution="1080p", audio=False)
    assert est is not None
    assert est.usd == round(0.095 * 10, 4)

    est_audio = estimate_video_cost("atlas-kling-v3-turbo", duration=10, resolution="720p", audio=True)
    assert est_audio is not None
    assert est_audio.usd == round(0.095 * 10, 4)

    # atlas-veo-3.1: Atlas flat $0.20/s (distinct from Google Vertex "veo-3.1" tiered rate)
    est = estimate_video_cost("atlas-veo-3.1", duration=5, resolution="720p", audio=False)
    assert est is not None
    assert est.usd == round(0.20 * 5, 4)

    est_audio = estimate_video_cost("atlas-veo-3.1", duration=5, resolution="1080p", audio=True)
    assert est_audio is not None
    assert est_audio.usd == round(0.20 * 5, 4)
