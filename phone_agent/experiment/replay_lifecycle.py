"""CLI: replay saved trajectories into the lifecycle pipeline and report.

Usage::

    python -m phone_agent.experiment.replay_lifecycle \
        --trajectories memory_db/default/trajectories \
        --out papers/amsg-trajreplay-report.md

Forces AMSG_CONFIG-independent behavior by constructing presets explicitly, so
the result does not depend on the ambient env var.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .report import build_report, render_markdown
from .replay import P0CRecoveryResult, ReplaySnapshot
from .trajectory import load_trajectories


def _report_to_dict(report) -> dict:
    def snap(s: ReplaySnapshot) -> dict:
        return {
            "config": s.config_name,
            "n_observations": s.n_observations,
            "n_edges": s.n_edges,
            "n_promoted": s.n_promoted,
            "n_candidate": s.n_candidate,
            "n_hypothesis": s.n_hypothesis,
            "n_multimodal": s.n_multimodal,
            "vc_histogram": s.vc_histogram,
            "stage_counts": s.stage_counts,
        }

    def p0c(p: P0CRecoveryResult) -> dict:
        return {
            "n_coord_edges": p.n_coord_edges,
            "n_recovered": p.n_recovered,
            "n_discriminating": p.n_discriminating,
            "n_discriminating_recovered": p.n_discriminating_recovered,
            "max_coord_vc": p.max_coord_vc,
            "all_recovered": p.all_recovered,
            "proven_on_real_data": p.proven_on_real_data,
        }

    c = report.corpus
    return {
        "corpus": {
            "n_trajectories": c.n_trajectories,
            "n_success": c.n_success,
            "n_failure": c.n_failure,
            "n_trivial": c.n_trivial,
            "total_steps": c.total_steps,
            "total_observations": c.total_observations,
            "dropped_observations": c.dropped_observations,
            "n_self_loops": c.n_self_loops,
            "n_cross_page": c.n_cross_page,
            "drop_rate": round(c.drop_rate, 4),
            "by_app": c.by_app,
            "trajs_per_task": c.trajs_per_task,
        },
        "sava": snap(report.sava),
        "legacy": snap(report.legacy),
        "n_sensitivity": report.sensitivity,
        "n_vlm_gated": report.n_vlm_gated,
        "p0c": p0c(report.p0c),
        "claims": [
            {"claim": v.claim, "supported": v.supported, "evidence": v.evidence}
            for v in report.claims
        ],
        "collection_asks": report.collection_asks,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AMSG offline TrajReplay lifecycle runner")
    ap.add_argument(
        "--trajectories",
        default="memory_db/default/trajectories",
        help="directory of saved *.json trajectories",
    )
    ap.add_argument(
        "--out",
        default="papers/amsg-trajreplay-report.md",
        help="markdown report output path (use '-' to skip writing)",
    )
    ap.add_argument("--json", default=None, help="optional JSON dump path")
    ap.add_argument("--generated-at", default="", help="timestamp string for the report header")
    ap.add_argument("--quiet", action="store_true", help="suppress stdout summary")
    args = ap.parse_args(argv)

    trajs = load_trajectories(args.trajectories)
    report = build_report(trajs)
    md = render_markdown(report, generated_at=args.generated_at)

    if args.out and args.out != "-":
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")

    if args.json:
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(_report_to_dict(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if not args.quiet:
        from .report import _summary_lines

        print(f"Loaded {report.corpus.n_trajectories} trajectories from {args.trajectories}")
        for line in _summary_lines(report):
            print(f"  - {line}")
        print()
        print("RQ2 sub-claim support on this corpus:")
        for v in report.claims:
            print(f"  [{'YES' if v.supported else 'NO '}] {v.claim}")
        print()
        print("Real-device collection still needs:")
        for i, ask in enumerate(report.collection_asks, 1):
            print(f"  {i}. {ask}")
        if args.out and args.out != "-":
            print()
            print(f"Full report written to {args.out}")
        if args.json:
            print(f"JSON written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
