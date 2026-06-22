"""Batch image generation — paced, multi-lane, idempotent (Tier 1, item B).

The DG bulk (840 imgs) exposed that nazca had no batching, no pacing, and no
resume — all of it was bolted onto external bash. This ports the proven approach
(`marketing/scripts/batch_gen.py`: 0 429s, true 2/min) into nazca.

Three ideas do the work:

1. **Pace request *starts*, not post-gen sleeps.** Vertex caps image gen at
   ~2 req/min per base model. Sleeping *after* each generation stacks the wait on
   top of the gen time (the DG run wasted ~40% — 52s between starts vs the 32s the
   quota actually needed). A `_StartPacer` gates each lane so starts are spaced at
   `60/rpm` seconds; the generation itself overlaps the wait.

2. **One lane per base model.** The 2/min cap is *per base model*, so distinct
   models have independent budgets. We group rows by model and run one worker
   thread (its own pacer) per model → N models = N×rpm combined throughput.

3. **Idempotent + resumable.** Rows whose `out` already exists are skipped, so a
   re-run only fills the gaps left by a crash or a partial run.

Per-row dispatch reuses `nazca.image.generate_image`, so it inherits the 429/503
backoff added in item 1A.

**Pacing is keyed off the model's backend.** Vertex's 2/min-per-base-model wall
wants throttled *starts* (the `_StartPacer` above). But latency-bound backends
like OpenAI's gpt-image-2 (~30–105s/img, no req/min wall) want the opposite —
*concurrency*, not a throttle. So an openai lane runs its rows through a small
worker pool with no inter-start delay, while vertex lanes keep their paced,
single-threaded behavior. The strategy is chosen per-lane from the backend, so
the existing Vertex pacing is untouched.
"""

from __future__ import annotations

import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from nazca.image import DEFAULT_MODEL, generate_image

# A small safety margin (seconds) added to each lane's start interval so we sit
# *under* the provider's req/min ceiling rather than exactly on it.
_MARGIN_S = 1.0

# Backends that are latency-bound (no req/min wall) and benefit from concurrent
# in-lane dispatch instead of start pacing. Default workers per such lane.
_CONCURRENT_BACKENDS = {"openai"}
_DEFAULT_LANE_WORKERS = 4


def backend_of(model: str) -> str:
    """The dispatch backend for a model shorthand (e.g. 'openai', 'vertex')."""
    from nazca.image import _resolve

    return _resolve(model)[3]

# Default ref-image extensions matched by --from-dir.
_IMAGE_GLOBS = ("*.png", "*.jpg", "*.jpeg", "*.webp")


class BatchError(RuntimeError):
    """Raised for manifest / configuration problems (not per-row gen failures)."""


@dataclass
class BatchRow:
    """One image to generate. `model` None means 'use the run default'."""

    out: Path
    prompt: str
    refs: list[str] = field(default_factory=list)
    model: str | None = None
    aspect: str | None = None
    size: str | None = None
    quality: str | None = None  # gpt-image-2 cost/speed lever; ignored elsewhere

    def resolved_model(self, default_model: str) -> str:
        return self.model or default_model


@dataclass
class RowResult:
    row: BatchRow
    status: str  # "ok" | "skipped" | "error" | "planned"
    detail: object | None = None  # error message, or the dry-run plan dict


# --------------------------------------------------------------------------- pacing
class _StartPacer:
    """Spaces request *starts* by `interval` seconds (token bucket, capacity 1).

    `wait()` blocks until the next start is allowed, then arms the gate for the
    one after. Generation time between calls counts toward the interval, so a slow
    gen incurs no extra wait. `_clock`/`_sleep` are injectable for testing.
    """

    def __init__(
        self,
        interval: float,
        *,
        _clock: Callable[[], float] = time.monotonic,
        _sleep: Callable[[float], None] = time.sleep,
    ):
        self.interval = max(0.0, interval)
        self._clock = _clock
        self._sleep = _sleep
        self._next: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._next is not None and now < self._next:
            self._sleep(self._next - now)
        start = self._clock()
        self._next = start + self.interval


def lane_interval(rpm: float) -> float:
    """Seconds between starts for a lane at `rpm` requests/min (+ safety margin)."""
    if rpm <= 0:
        raise BatchError(f"--rpm must be > 0, got {rpm}")
    return 60.0 / rpm + _MARGIN_S


# --------------------------------------------------------------------------- manifest
def _coerce_refs(value: object) -> list[str]:
    """Accept a list, a single path, or a ';'/'|'-separated string of paths."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value)
    parts = [p.strip() for chunk in text.split(";") for p in chunk.split("|")]
    return [p for p in parts if p]


def _row_from_mapping(rec: dict, defaults: dict) -> BatchRow:
    out = rec.get("out") or rec.get("output")
    prompt = rec.get("prompt")
    if not out or not prompt:
        raise BatchError(f"manifest row missing required 'out'/'prompt': {rec}")
    refs = _coerce_refs(rec.get("refs") if rec.get("refs") is not None else rec.get("ref"))
    return BatchRow(
        out=Path(str(out)),
        prompt=str(prompt),
        refs=refs,
        model=(rec.get("model") or defaults.get("model")),
        aspect=(rec.get("aspect") or rec.get("aspect_ratio") or defaults.get("aspect")),
        size=(rec.get("size") or defaults.get("size")),
        quality=(rec.get("quality") or defaults.get("quality")),
    )


def load_manifest(path: str | Path, defaults: dict | None = None) -> list[BatchRow]:
    """Parse a JSONL or CSV manifest into rows. Format inferred from the suffix."""
    defaults = defaults or {}
    path = Path(path)
    if not path.is_file():
        raise BatchError(f"manifest not found: {path}")

    suffix = path.suffix.lower()
    rows: list[BatchRow] = []
    if suffix in (".jsonl", ".ndjson", ".json"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                raise BatchError(f"{path}:{lineno}: invalid JSON: {e}") from e
            rows.append(_row_from_mapping(rec, defaults))
    elif suffix == ".csv":
        with path.open(newline="") as fh:
            for rec in csv.DictReader(fh):
                rows.append(_row_from_mapping(rec, defaults))
    else:
        raise BatchError(f"unsupported manifest type '{suffix}' (use .jsonl or .csv)")

    if not rows:
        raise BatchError(f"manifest is empty: {path}")
    return rows


def rows_from_dir(
    src_dir: str | Path,
    prompt: str,
    out_dir: str | Path,
    *,
    models: list[str] | None = None,
    aspect: str | None = None,
    size: str | None = None,
    quality: str | None = None,
    globs: Iterable[str] = _IMAGE_GLOBS,
) -> list[BatchRow]:
    """Build rows from a directory of ref images. `prompt` may use `{stem}`/`{name}`.

    With multiple `models`, each input fans out to every model, written under a
    per-model subdir so outputs never collide.
    """
    src = Path(src_dir)
    if not src.is_dir():
        raise BatchError(f"--from-dir is not a directory: {src}")
    out_root = Path(out_dir)
    model_list = models or [None]  # None → run default model on a single lane

    files = sorted({p for g in globs for p in src.glob(g)})
    if not files:
        raise BatchError(f"no images ({', '.join(globs)}) found in {src}")

    rows: list[BatchRow] = []
    for f in files:
        text = prompt.format(stem=f.stem, name=f.name) if "{" in prompt else prompt
        for model in model_list:
            sub = out_root / model if (model and len(model_list) > 1) else out_root
            rows.append(
                BatchRow(
                    out=sub / f"{f.stem}.png",
                    prompt=text,
                    refs=[str(f)],
                    model=model,
                    aspect=aspect,
                    size=size,
                    quality=quality,
                )
            )
    return rows


# --------------------------------------------------------------------------- planning
@dataclass
class BatchPlan:
    lanes: dict[str, list[BatchRow]]  # model → rows
    rpm: float
    pending: int  # rows that would actually dispatch (out missing)
    skipped: int  # rows whose out already exists

    @property
    def total(self) -> int:
        return self.pending + self.skipped

    def eta_seconds(self) -> float:
        """Wall-clock estimate from the slowest *rpm-paced* lane's start-spacing.

        Concurrent (latency-bound) lanes have no start throttle, so their wall
        time is gen latency — which we don't model here — and they're excluded.
        """
        interval = lane_interval(self.rpm)
        worst = 0
        for model, rows in self.lanes.items():
            if backend_of(model) in _CONCURRENT_BACKENDS:
                continue
            pend = sum(1 for r in rows if not r.out.exists())
            worst = max(worst, pend)
        return max(0, worst - 1) * interval

    def summary_lines(self) -> list[str]:
        eta_min = self.eta_seconds() / 60.0
        paced = [m for m in self.lanes if backend_of(m) not in _CONCURRENT_BACKENDS]
        combined = self.rpm * len(paced)
        lines = [
            f"batch: {self.total} rows · {self.pending} to generate · {self.skipped} already done",
            f"lanes: {len(paced)} paced model(s) × {self.rpm:g}/min = {combined:g}/min combined",
            f"  est. wall time ≈ {eta_min:.1f} min (slowest paced lane; lanes run in parallel)",
        ]
        for model, rows in sorted(self.lanes.items()):
            pend = sum(1 for r in rows if not r.out.exists())
            mode = "concurrent" if backend_of(model) in _CONCURRENT_BACKENDS else f"{self.rpm:g}/min"
            lines.append(f"  · {model}: {len(rows)} rows ({pend} pending) [{mode}]")
        return lines


def plan_batch(
    rows: list[BatchRow],
    *,
    rpm: float = 2.0,
    default_model: str = DEFAULT_MODEL,
    only_models: set[str] | None = None,
) -> BatchPlan:
    """Group rows into per-model lanes and compute the dispatch/skip counts."""
    lanes: dict[str, list[BatchRow]] = {}
    for row in rows:
        model = row.resolved_model(default_model)
        if only_models is not None and model not in only_models:
            continue
        lanes.setdefault(model, []).append(row)
    if not lanes:
        raise BatchError("no rows to run (did --models filter everything out?)")

    pending = sum(1 for rs in lanes.values() for r in rs if not r.out.exists())
    skipped = sum(1 for rs in lanes.values() for r in rs if r.out.exists())
    return BatchPlan(lanes=lanes, rpm=rpm, pending=pending, skipped=skipped)


# --------------------------------------------------------------------------- execution
def _gen_kwargs(row: BatchRow) -> dict:
    """Per-row kwargs for `generate_image`. `quality` is only included when set,
    so callers/fakes that predate the quality lever keep their signature."""
    kwargs = dict(
        ref=row.refs or None, model=row.model,
        aspect_ratio=row.aspect, size=row.size,
    )
    if row.quality is not None:
        kwargs["quality"] = row.quality
    return kwargs


def _dispatch_row(
    row: BatchRow,
    *,
    dry_run: bool,
    on_event: Callable[[str, BatchRow, object], None],
) -> RowResult:
    """Generate (or plan) one row. Always returns a RowResult — never raises, so
    one bad row can't sink the lane or the worker pool it runs in."""
    if row.out.exists():
        on_event("skipped", row, None)
        return RowResult(row, "skipped")

    if dry_run:
        try:
            plan = generate_image(
                row.out, row.prompt, dry_run=True, **_gen_kwargs(row),
            )
        except Exception as e:  # a bad row must not sink the whole plan preview
            on_event("error", row, e)
            return RowResult(row, "error", str(e))
        on_event("planned", row, plan)
        return RowResult(row, "planned", plan)

    try:
        # mkdir is inside the try so a filesystem failure becomes a per-row
        # error, not an exception that escapes the lane and sinks the batch.
        row.out.parent.mkdir(parents=True, exist_ok=True)
        generate_image(row.out, row.prompt, **_gen_kwargs(row))
        on_event("ok", row, None)
        return RowResult(row, "ok")
    except Exception as e:  # one bad row must not sink the lane
        on_event("error", row, e)
        return RowResult(row, "error", str(e))


def _run_lane(
    model: str,
    rows: list[BatchRow],
    pacer: _StartPacer,
    *,
    dry_run: bool,
    on_event: Callable[[str, BatchRow, object], None],
    workers: int = 1,
) -> list[RowResult]:
    """Run one model lane.

    Default (`workers == 1`): paced, single-threaded — gate each start through
    `pacer` so vertex lanes honor the 2/min wall (gen time overlaps the wait).

    `workers > 1`: latency-bound backend (e.g. openai gpt-image-2) — dispatch
    rows through a worker pool with no inter-start delay (the pacer is unused).
    """
    if dry_run or workers <= 1:
        results: list[RowResult] = []
        for row in rows:
            if not dry_run and not row.out.exists():
                pacer.wait()  # gate the *start* — gen overlaps the next interval
            results.append(_dispatch_row(row, dry_run=dry_run, on_event=on_event))
        return results

    # Concurrent lane: rows run in parallel, no throttle. Preserve input order.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(
            pool.map(
                lambda r: _dispatch_row(r, dry_run=dry_run, on_event=on_event),
                rows,
            )
        )


def run_batch(
    plan: BatchPlan,
    *,
    dry_run: bool = False,
    concurrency: int | None = None,
    lane_workers: int = _DEFAULT_LANE_WORKERS,
    on_event: Callable[[str, BatchRow, object], None] | None = None,
    _pacer_factory: Callable[[float], _StartPacer] | None = None,
) -> list[RowResult]:
    """Execute a plan, one worker thread per model lane, lanes in parallel.

    Pacing is chosen per-lane from the model's backend:
      · vertex (and other rpm-walled backends) → paced, single-threaded
        (`_StartPacer` at `plan.rpm`), preserving the 2/min-per-base-model wall.
      · latency-bound backends (openai gpt-image-2) → concurrent in-lane
        dispatch with no throttle, up to `lane_workers` parallel rows.

    `concurrency` caps simultaneous *lanes* (default: one thread per lane).
    `lane_workers` caps parallel rows within a concurrent (openai) lane.
    `on_event(status, row, detail)` is called as each row resolves.
    """
    on_event = on_event or (lambda *a: None)
    make_pacer = _pacer_factory or (lambda interval: _StartPacer(interval))
    interval = lane_interval(plan.rpm)
    max_workers = concurrency or len(plan.lanes)

    def lane_task(item: tuple[str, list[BatchRow]]) -> list[RowResult]:
        model, lane_rows = item
        workers = lane_workers if backend_of(model) in _CONCURRENT_BACKENDS else 1
        return _run_lane(
            model, lane_rows, make_pacer(interval),
            dry_run=dry_run, on_event=on_event, workers=workers,
        )

    results: list[RowResult] = []
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        for lane_results in pool.map(lane_task, plan.lanes.items()):
            results.extend(lane_results)
    return results
