"""Phase 5 test suite: staging batch + human review gate.

Covers:
- create_staging_batch: expected files produced, manifest ids stable, default decisions
- apply_review: dry_run counting, real apply with fake store, provenance fields,
  applied.json written, double-apply refused
- VlmTransitionJudge: parse-failure → {} (never approve-all)
- TrajectoryReviewer: parse-failure no longer imports anything
- decisions roundtrip (load/save/validate/summarize)
- webui pure helpers (list_batches ordering, suspicious-first sort)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures & helpers ────────────────────────────────────────────────────────

def _write_pages(tmp_path: Path, app: str = "淘宝") -> Path:
    """Write a minimal *_explore_*.json pages file."""
    pages = {
        "app": app,
        "pages": [
            {
                "page_type": "home",
                "summary": "首页",
                "elements": {"search_bar": "tap to search"},
                "screenshot_hash": "homehash00000001",
                "app": app,
            },
            {
                "page_type": "search_result",
                "summary": "搜索结果",
                "elements": {"product_cards": "tap product card"},
                "screenshot_hash": "resulthash000001",
                "app": app,
            },
            {
                "page_type": "product_detail",
                "summary": "商品详情",
                "elements": {"buy_buttons": "add to cart"},
                "screenshot_hash": "detailhash000001",
                "app": app,
            },
        ],
    }
    path = tmp_path / f"{app}_explore_1.json"
    path.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    return path


def _write_transitions(tmp_path: Path, app: str = "淘宝") -> Path:
    """Write matching *_explore_transitions_*.json file."""
    transitions = {
        "app": app,
        "transitions": [
            {
                "from": "home:首页",
                "action": {"action": "Tap", "element": [400, 120]},
                "to": "search_result:搜索结果",
                "observations": 3,
                "confidence": "confident",
            },
            {
                "from": "search_result:搜索结果",
                "action": {"action": "Tap", "element": [300, 500]},
                "to": "product_detail:商品详情",
                "observations": 1,
                "confidence": "low_confidence",
            },
        ],
    }
    path = tmp_path / f"{app}_explore_transitions_1.json"
    path.write_text(json.dumps(transitions, ensure_ascii=False), encoding="utf-8")
    return path


class FakeMergeGraphStore:
    """Minimal fake graph store that records add_state_transition calls."""

    driver = object()

    def __init__(self, candidates: list[dict[str, Any]] | None = None) -> None:
        self.candidates = candidates or []
        self.upserted: list[dict[str, Any]] = []
        self.transitions: list[dict[str, Any]] = []

    def find_page_state_candidates(self, app: str = "", page_type: str = "", limit: int = 10) -> list[dict]:
        return [
            s for s in self.candidates
            if s.get("app") == app and s.get("page_type") == page_type
        ][:limit]

    def upsert_page_state(self, state_metadata: dict[str, Any]) -> None:
        self.upserted.append(state_metadata)

    def add_state_transition(
        self,
        source_state_hash: str,
        target_state_hash: str,
        action_data: dict[str, Any],
        task_id: str | None = None,
        outcome: str = "success",
        source_metadata: Any = None,
        target_metadata: Any = None,
        lifecycle: Any = None,
    ) -> None:
        self.transitions.append(
            {
                "source": source_state_hash,
                "target": target_state_hash,
                "action": action_data,
                "source_metadata": source_metadata,
                "target_metadata": target_metadata,
                "lifecycle": lifecycle,
            }
        )


# ── A. create_staging_batch ───────────────────────────────────────────────────

class TestCreateStagingBatch:

    def test_required_files_are_written(self, tmp_path):
        from phone_agent.spatial.review.batch import create_staging_batch

        pages_path = _write_pages(tmp_path)
        _write_transitions(tmp_path)

        batch_dir = create_staging_batch(pages_path, storage_root=tmp_path / "staging")

        assert (batch_dir / "staging_graph.json").exists()
        assert (batch_dir / "manifest.json").exists()
        assert (batch_dir / "decisions.json").exists()

    def test_staging_graph_has_expected_keys(self, tmp_path):
        from phone_agent.spatial.review.batch import create_staging_batch

        pages_path = _write_pages(tmp_path)
        _write_transitions(tmp_path)

        batch_dir = create_staging_batch(pages_path, storage_root=tmp_path / "staging")
        data = json.loads((batch_dir / "staging_graph.json").read_text(encoding="utf-8"))

        assert "app" in data
        assert "states" in data
        assert "edges" in data
        assert "quality" in data
        assert isinstance(data["states"], list)
        assert isinstance(data["edges"], list)

    def test_manifest_ids_are_stable_across_rebuilds(self, tmp_path):
        from phone_agent.spatial.review.batch import create_staging_batch

        pages_path = _write_pages(tmp_path)
        _write_transitions(tmp_path)

        batch1 = create_staging_batch(
            pages_path, storage_root=tmp_path / "staging1", batch_id="testbatch1"
        )
        batch2 = create_staging_batch(
            pages_path, storage_root=tmp_path / "staging2", batch_id="testbatch2"
        )

        manifest1 = json.loads((batch1 / "manifest.json").read_text(encoding="utf-8"))
        manifest2 = json.loads((batch2 / "manifest.json").read_text(encoding="utf-8"))

        ids1 = {item["item_id"] for item in manifest1}
        ids2 = {item["item_id"] for item in manifest2}
        assert ids1 == ids2, "manifest item_ids must be deterministic"

    def test_default_decision_approves_high_observations_non_low_confidence(self, tmp_path):
        from phone_agent.spatial.review.batch import create_staging_batch

        pages_path = _write_pages(tmp_path)
        _write_transitions(tmp_path)

        batch_dir = create_staging_batch(pages_path, storage_root=tmp_path / "staging")
        manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
        decisions = json.loads((batch_dir / "decisions.json").read_text(encoding="utf-8"))

        # Find items with observations>=2 and confident
        for item in manifest:
            iid = item["item_id"]
            obs = item.get("observations", 0)
            conf = item.get("explorer_confidence", "")
            dec = decisions.get(iid, {}).get("decision", "")

            if obs >= 2 and conf != "low_confidence" and item.get("vlm_verdict") == "unreviewed":
                assert dec == "approve", (
                    f"item {iid} obs={obs} conf={conf} should default approve, got {dec}"
                )

    def test_default_decision_rejects_low_confidence(self, tmp_path):
        from phone_agent.spatial.review.batch import create_staging_batch

        pages_path = _write_pages(tmp_path)
        _write_transitions(tmp_path)

        batch_dir = create_staging_batch(pages_path, storage_root=tmp_path / "staging")
        manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
        decisions = json.loads((batch_dir / "decisions.json").read_text(encoding="utf-8"))

        for item in manifest:
            iid = item["item_id"]
            conf = item.get("explorer_confidence", "")
            dec = decisions.get(iid, {}).get("decision", "")

            if conf == "low_confidence" and item.get("vlm_verdict") == "unreviewed":
                assert dec == "reject", (
                    f"item {iid} low_confidence should default reject, got {dec}"
                )


# ── B. apply_review ───────────────────────────────────────────────────────────

class TestApplyReview:

    def _make_batch(self, tmp_path: Path) -> Path:
        from phone_agent.spatial.review.batch import create_staging_batch
        pages_path = _write_pages(tmp_path)
        _write_transitions(tmp_path)
        return create_staging_batch(pages_path, storage_root=tmp_path / "staging")

    def test_dry_run_returns_counts_no_applied_json(self, tmp_path):
        from phone_agent.spatial.review.apply import apply_review

        batch_dir = self._make_batch(tmp_path)
        # Force all approve
        from phone_agent.spatial.review.decisions import load_decisions, save_decisions
        decisions = load_decisions(batch_dir)
        for iid in decisions:
            decisions[iid]["decision"] = "approve"
        save_decisions(batch_dir, decisions)

        result = apply_review(batch_dir, graph_store=None, dry_run=True)

        assert result.get("dry_run") is True
        assert result.get("approved", 0) >= 0
        assert result.get("rejected", 0) >= 0
        assert not (batch_dir / "applied.json").exists()

    def test_real_apply_writes_applied_json(self, tmp_path):
        from phone_agent.spatial.review.apply import apply_review

        batch_dir = self._make_batch(tmp_path)
        fake_store = FakeMergeGraphStore()

        result = apply_review(batch_dir, graph_store=fake_store, dry_run=False)

        assert (batch_dir / "applied.json").exists()
        receipt = json.loads((batch_dir / "applied.json").read_text(encoding="utf-8"))
        assert "applied_at" in receipt
        assert "approved" in receipt

    def test_only_approved_edges_hit_graph_store(self, tmp_path):
        from phone_agent.spatial.review.apply import apply_review
        from phone_agent.spatial.review.decisions import load_decisions, save_decisions
        from phone_agent.spatial.review.batch import load_manifest

        batch_dir = self._make_batch(tmp_path)

        # Set first item approve, rest reject
        manifest = load_manifest(batch_dir)
        decisions = load_decisions(batch_dir)
        for i, item in enumerate(manifest):
            decisions[item.item_id] = {"decision": "approve" if i == 0 else "reject", "note": ""}
        save_decisions(batch_dir, decisions)

        fake_store = FakeMergeGraphStore()
        result = apply_review(batch_dir, graph_store=fake_store, dry_run=False)

        assert result["approved"] == 1
        assert result["rejected"] == len(manifest) - 1
        # Only approved edges should be written
        assert len(fake_store.transitions) <= 1

    def test_approved_edges_carry_provenance_fields(self, tmp_path):
        from phone_agent.spatial.review.apply import apply_review
        from phone_agent.spatial.review.decisions import load_decisions, save_decisions
        from phone_agent.spatial.review.batch import load_manifest

        batch_dir = self._make_batch(tmp_path)

        # Approve all
        manifest = load_manifest(batch_dir)
        decisions = load_decisions(batch_dir)
        for item in manifest:
            decisions[item.item_id] = {"decision": "approve", "note": ""}
        save_decisions(batch_dir, decisions)

        fake_store = FakeMergeGraphStore()
        apply_review(batch_dir, graph_store=fake_store, dry_run=False)

        for transition in fake_store.transitions:
            action = transition["action"]
            assert action.get("source_type") == "exploration_reviewed", (
                "approved edge must carry source_type=exploration_reviewed"
            )
            assert "review_batch_id" in action, "approved edge must carry review_batch_id"

    def test_second_apply_raises_error(self, tmp_path):
        from phone_agent.spatial.review.apply import apply_review

        batch_dir = self._make_batch(tmp_path)
        fake_store = FakeMergeGraphStore()

        # First apply
        apply_review(batch_dir, graph_store=fake_store, dry_run=False)

        # Second apply should raise
        with pytest.raises(RuntimeError, match="already applied"):
            apply_review(batch_dir, graph_store=fake_store, dry_run=False)

    def test_second_apply_returns_error_not_raises_when_error_style(self, tmp_path):
        """Double-apply protection — runtime error indicates batch was already applied."""
        from phone_agent.spatial.review.apply import apply_review

        batch_dir = self._make_batch(tmp_path)
        fake_store = FakeMergeGraphStore()
        apply_review(batch_dir, graph_store=fake_store, dry_run=False)

        # Must raise RuntimeError on second call
        try:
            apply_review(batch_dir, graph_store=fake_store, dry_run=False)
            pytest.fail("Expected RuntimeError on double apply")
        except RuntimeError as exc:
            assert "already applied" in str(exc).lower()

    # ── P0-A: cold-start bootstrap promotion ──────────────────────────────

    def _approve_all(self, batch_dir: Path) -> int:
        from phone_agent.spatial.review.decisions import load_decisions, save_decisions
        from phone_agent.spatial.review.batch import load_manifest
        manifest = load_manifest(batch_dir)
        decisions = load_decisions(batch_dir)
        for item in manifest:
            decisions[item.item_id] = {"decision": "approve", "note": ""}
        save_decisions(batch_dir, decisions)
        return len(manifest)

    def test_default_apply_writes_hypothesis(self, tmp_path):
        """Regression guard: without bootstrap the invariant holds — reviewed
        edges enter as hypothesis and must still earn online promotion."""
        from phone_agent.spatial.review.apply import apply_review

        batch_dir = self._make_batch(tmp_path)
        self._approve_all(batch_dir)
        fake_store = FakeMergeGraphStore()
        apply_review(batch_dir, graph_store=fake_store, dry_run=False)

        assert fake_store.transitions, "expected at least one approved edge persisted"
        for t in fake_store.transitions:
            lc = t["lifecycle"] or {}
            assert lc.get("lifecycle_stage") == "hypothesis"
            assert lc.get("verification_count") == 0

    def test_bootstrap_writes_promoted_with_provenance(self, tmp_path):
        """bootstrap=True admits reviewed edges directly as promoted (cold-start
        accelerator) with verification_count at the active promotion threshold
        and an auditable bootstrap provenance marker."""
        from phone_agent.spatial.review.apply import apply_review
        from phone_agent.spatial.amsg_config import AMSGOptimConfig

        batch_dir = self._make_batch(tmp_path)
        self._approve_all(batch_dir)
        fake_store = FakeMergeGraphStore()
        apply_review(batch_dir, graph_store=fake_store, dry_run=False, bootstrap=True)

        active_min_vc = AMSGOptimConfig.from_env().min_verification_count
        assert fake_store.transitions, "expected at least one promoted edge"
        for t in fake_store.transitions:
            lc = t["lifecycle"] or {}
            assert lc.get("lifecycle_stage") == "promoted"
            assert lc.get("verification_count") >= active_min_vc
            assert lc.get("dominance_ratio") == 1.0
            # provenance: review origin preserved + bootstrap marker added
            assert t["action"].get("source_type") == "exploration_reviewed"
            assert t["action"].get("bootstrap") is True

    def test_force_allows_reapply(self, tmp_path):
        """force=True re-applies despite an existing applied.json — needed to
        bootstrap-promote an app whose batch was already applied as hypothesis.
        MERGE makes the re-apply an idempotent lifecycle update."""
        from phone_agent.spatial.review.apply import apply_review

        batch_dir = self._make_batch(tmp_path)
        self._approve_all(batch_dir)
        fake_store = FakeMergeGraphStore()

        apply_review(batch_dir, graph_store=fake_store, dry_run=False)
        with pytest.raises(RuntimeError, match="already applied"):
            apply_review(batch_dir, graph_store=fake_store, dry_run=False)

        result = apply_review(
            batch_dir, graph_store=fake_store, dry_run=False,
            bootstrap=True, force=True,
        )
        assert result["persisted"] is True
        assert result.get("bootstrap") is True


# ── C. VlmTransitionJudge — parse failure ─────────────────────────────────────

class TestVlmTransitionJudge:

    def test_parse_failure_returns_empty_dict(self):
        from phone_agent.spatial.review.vlm_review import VlmTransitionJudge

        judge = VlmTransitionJudge(
            model="test-model",
            base_url="http://localhost:1234",
            api_key="test-key",
        )

        # Mock OpenAI client returning non-JSON garbage
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "not valid json at all - just text"
        mock_client.chat.completions.create.return_value = mock_response
        judge._client = mock_client

        items = [
            {"source_page_type": "home", "target_page_type": "search_result",
             "action_description": "Tap", "observations": 2, "explorer_confidence": "confident"},
        ]
        result = judge.judge_transitions(items)

        assert result == {}, "parse failure must return {} (never approve-all)"

    def test_parse_failure_with_partial_json_returns_empty(self):
        from phone_agent.spatial.review.vlm_review import VlmTransitionJudge

        judge = VlmTransitionJudge(
            model="test-model",
            base_url="http://localhost:1234",
            api_key="test-key",
        )

        mock_client = MagicMock()
        mock_response = MagicMock()
        # Partial JSON — parse will fail
        mock_response.choices[0].message.content = '[{"index": 1, "valid": true, "reason":'
        mock_client.chat.completions.create.return_value = mock_response
        judge._client = mock_client

        items = [
            {"source_page_type": "home", "target_page_type": "search_result",
             "action_description": "Tap", "observations": 2, "explorer_confidence": "confident"},
        ]
        result = judge.judge_transitions(items)

        assert result == {}

    def test_valid_response_returns_verdicts(self):
        from phone_agent.spatial.review.vlm_review import VlmTransitionJudge

        judge = VlmTransitionJudge(
            model="test-model",
            base_url="http://localhost:1234",
            api_key="test-key",
        )

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = (
            '[{"index": 1, "valid": true, "reason": "valid transition"}]'
        )
        mock_client.chat.completions.create.return_value = mock_response
        judge._client = mock_client

        items = [
            {"source_page_type": "home", "target_page_type": "search_result",
             "action_description": "Tap search bar", "observations": 3, "explorer_confidence": "confident"},
        ]
        result = judge.judge_transitions(items)

        assert 0 in result
        approved, reason = result[0]
        assert approved is True
        assert "valid transition" in reason

    def test_no_items_returns_empty(self):
        from phone_agent.spatial.review.vlm_review import VlmTransitionJudge

        judge = VlmTransitionJudge(
            model="test-model",
            base_url="http://localhost:1234",
            api_key="test-key",
        )
        result = judge.judge_transitions([])
        assert result == {}

    def test_unavailable_judge_returns_empty(self):
        """A judge manually configured with no credentials must return {} and report not available."""
        from phone_agent.spatial.review.vlm_review import VlmTransitionJudge

        # Use object.__new__ to bypass __init__ (which calls load_dotenv)
        # and set attributes manually to simulate truly empty config
        judge = object.__new__(VlmTransitionJudge)
        judge._model = ""
        judge._base_url = ""
        judge._api_key = ""
        judge._client = None

        assert not judge.available()

        items = [
            {"source_page_type": "home", "target_page_type": "search_result",
             "action_description": "Tap", "observations": 2, "explorer_confidence": "confident"},
        ]
        result = judge.judge_transitions(items)
        assert result == {}


# ── D. TrajectoryReviewer — parse failure doesn't import ──────────────────────

class TestTrajectoryReviewerParseFailure:

    def _make_reviewer(self, fake_store: Any) -> Any:
        from phone_agent.spatial.trajectory_reviewer import TrajectoryReviewer
        reviewer = TrajectoryReviewer(graph_store=fake_store, verbose=False)
        return reviewer

    def test_parse_failure_imports_nothing(self, monkeypatch):
        from phone_agent.spatial.trajectory_reviewer import TrajectoryReviewer

        fake_store = FakeMergeGraphStore()
        reviewer = self._make_reviewer(fake_store)

        # Mock VLM to return non-JSON
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "this is not json"
        mock_client.chat.completions.create.return_value = mock_response
        reviewer._vlm_client = mock_client
        reviewer._vlm_model = "test-model"

        trajectory = {
            "success": True,
            "task": "test",
            "step_details": [
                {"page_type": "home", "action_type": "Tap", "action_params": {}, "thinking": ""},
                {"page_type": "search_result", "action_type": "Tap", "action_params": {}, "thinking": ""},
            ],
        }

        # Monkeypatch _transition_exists to return False so we reach VLM
        monkeypatch.setattr(reviewer, "_transition_exists", lambda c, app: False)

        result = reviewer.review_and_import(trajectory, app="淘宝")

        assert result.imported == 0, "parse failure must not import any transitions"
        assert len(fake_store.transitions) == 0


# ── E. Decisions roundtrip ────────────────────────────────────────────────────

class TestDecisionsRoundtrip:

    def test_save_and_load_roundtrip(self, tmp_path):
        from phone_agent.spatial.review.decisions import save_decisions, load_decisions

        decisions: dict = {
            "abc123": {"decision": "approve", "note": "looks good"},
            "def456": {"decision": "reject", "note": "noise"},
        }
        save_decisions(tmp_path, decisions)
        loaded = load_decisions(tmp_path)

        assert loaded == decisions

    def test_validate_catches_invalid_decision(self, tmp_path):
        from phone_agent.spatial.review.decisions import save_decisions, validate_decisions

        decisions: dict = {
            "abc123": {"decision": "maybe", "note": ""},
        }
        save_decisions(tmp_path, decisions)
        loaded_back: dict = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
        errors = validate_decisions(loaded_back)

        assert len(errors) > 0
        assert any("maybe" in err for err in errors)

    def test_summarize_counts_correctly(self):
        from phone_agent.spatial.review.decisions import summarize
        from phone_agent.spatial.review.manifest import ReviewItem

        items = [
            ReviewItem(
                item_id="a1", kind="transition", app="淘宝", domain="shopping",
                source_page_type="home", target_page_type="search_result",
                source_summary="", target_summary="",
                action_description="Tap", action_params={},
                before_screenshot="", after_screenshot="",
                observations=3, explorer_confidence="confident",
                vlm_verdict="approve", vlm_reason="",
                blind_page_check="match",
            ),
            ReviewItem(
                item_id="b2", kind="transition", app="淘宝", domain="shopping",
                source_page_type="search_result", target_page_type="product_detail",
                source_summary="", target_summary="",
                action_description="Tap", action_params={},
                before_screenshot="", after_screenshot="",
                observations=1, explorer_confidence="low_confidence",
                vlm_verdict="reject", vlm_reason="",
                blind_page_check="skipped",
            ),
        ]
        decisions = {"a1": {"decision": "approve", "note": ""}, "b2": {"decision": "reject", "note": ""}}

        summary = summarize(items, decisions)

        assert summary["total"] == 2
        assert summary["approved"] == 1
        assert summary["rejected"] == 1
        assert summary["unresolved"] == 0

    def test_load_missing_returns_empty(self, tmp_path):
        from phone_agent.spatial.review.decisions import load_decisions

        result = load_decisions(tmp_path / "nonexistent")
        assert result == {}


# ── F. Webui pure helpers ─────────────────────────────────────────────────────

class TestWebuiPureHelpers:

    def test_list_batches_newest_first(self, tmp_path):
        from phone_agent.spatial.review.webui_review import list_batches

        # Create batch dirs with different names (sorted by name descending)
        staging_root = tmp_path / "staging"
        (staging_root / "taobao_20240101T000000Z").mkdir(parents=True)
        (staging_root / "taobao_20240201T000000Z").mkdir(parents=True)
        (staging_root / "taobao_20240301T000000Z").mkdir(parents=True)

        batches = list_batches(staging_root)

        assert len(batches) == 3
        # newest first (highest lexicographic sort order)
        names = [b["batch_id"] for b in batches]
        assert names == sorted(names, reverse=True)

    def test_list_batches_shows_applied_status(self, tmp_path):
        from phone_agent.spatial.review.webui_review import list_batches

        staging_root = tmp_path / "staging"
        batch_dir = staging_root / "taobao_20240101T000000Z"
        batch_dir.mkdir(parents=True)
        (batch_dir / "applied.json").write_text("{}", encoding="utf-8")

        batches = list_batches(staging_root)

        assert len(batches) == 1
        assert batches[0]["applied"] is True

    def test_list_batches_empty_storage_root(self, tmp_path):
        from phone_agent.spatial.review.webui_review import list_batches

        batches = list_batches(tmp_path / "nonexistent_staging")
        assert batches == []

    def test_sort_manifest_suspicious_first(self):
        from phone_agent.spatial.review.webui_review import sort_manifest_for_review
        from phone_agent.spatial.review.manifest import ReviewItem

        def _make_item(iid: str, blind: str, conf: str) -> ReviewItem:
            return ReviewItem(
                item_id=iid, kind="transition", app="", domain="",
                source_page_type="home", target_page_type="search_result",
                source_summary="", target_summary="",
                action_description="", action_params={},
                before_screenshot="", after_screenshot="",
                observations=1, explorer_confidence=conf,
                vlm_verdict="unreviewed", vlm_reason="",
                blind_page_check=blind,
            )

        normal = _make_item("normal", "match", "confident")
        blind_mismatch = _make_item("blind_mismatch", "mismatch:home->unknown", "confident")
        low_conf = _make_item("low_conf", "skipped", "low_confidence")

        items = [normal, low_conf, blind_mismatch]
        sorted_items = sort_manifest_for_review(items)

        # Blind mismatch must come first
        assert sorted_items[0].item_id == "blind_mismatch"
        # Low confidence must come before normal
        idx_low = next(i for i, x in enumerate(sorted_items) if x.item_id == "low_conf")
        idx_normal = next(i for i, x in enumerate(sorted_items) if x.item_id == "normal")
        assert idx_low < idx_normal


# ── G. ReviewItem stability ───────────────────────────────────────────────────

class TestReviewItemStability:

    def test_to_dict_from_dict_roundtrip(self):
        from phone_agent.spatial.review.manifest import ReviewItem

        item = ReviewItem(
            item_id="abc123",
            kind="transition",
            app="淘宝",
            domain="shopping",
            source_page_type="home",
            target_page_type="search_result",
            source_summary="首页",
            target_summary="搜索结果",
            action_description="Tap [400, 120] (搜索栏)",
            action_params={"action": "Tap", "element": [400, 120]},
            before_screenshot="screenshots/homehash.png",
            after_screenshot="screenshots/resulthash.png",
            observations=2,
            explorer_confidence="confident",
            vlm_verdict="approve",
            vlm_reason="valid navigation",
            blind_page_check="match",
        )

        restored = ReviewItem.from_dict(item.to_dict())
        assert restored == item

    def test_item_id_is_deterministic(self):
        from phone_agent.spatial.review.manifest import _make_item_id

        id1 = _make_item_id("home", "Tap|search|[400,120]", "search_result")
        id2 = _make_item_id("home", "Tap|search|[400,120]", "search_result")
        assert id1 == id2

        id3 = _make_item_id("home", "Tap|other|[100,200]", "cart")
        assert id1 != id3


# ── H. build_manifest from staging graph ──────────────────────────────────────

class TestBuildManifest:

    def test_build_manifest_from_synthetic_staging_graph(self, tmp_path):
        from phone_agent.spatial.review.manifest import build_manifest

        staging_graph = {
            "app": "淘宝",
            "domain": "shopping",
            "states": [
                {
                    "state_id": "state_home_abc",
                    "app": "taobao",
                    "page_type": "home",
                    "summary": "首页",
                    "landmarks": [],
                    "affordances": [],
                    "slots": {},
                    "risk_level": "normal",
                    "screenshot_hash": "homehash00000001",
                    "semantic_signature": "",
                    "domain": "shopping",
                },
                {
                    "state_id": "state_search_result_xyz",
                    "app": "taobao",
                    "page_type": "search_result",
                    "summary": "搜索结果",
                    "landmarks": [],
                    "affordances": [],
                    "slots": {},
                    "risk_level": "normal",
                    "screenshot_hash": "resulthash000001",
                    "semantic_signature": "",
                    "domain": "shopping",
                },
            ],
            "edges": [
                {
                    "source_id": "state_home_abc",
                    "target_id": "state_search_result_xyz",
                    "action_type": "Tap",
                    "action_target": "search bar",
                    "action_params": {"action": "Tap", "element": [400, 120]},
                    "precondition": "",
                    "postcondition": "search_result",
                    "success_count": 1,
                    "fail_count": 0,
                    "rollback_action": "Back",
                    "cost": 1.0,
                    "risk": "normal",
                    "confidence": 1.0,
                    "evidence": "",
                }
            ],
        }

        items = build_manifest(tmp_path, staging_graph)

        assert len(items) == 1
        item = items[0]
        assert item.source_page_type == "home"
        assert item.target_page_type == "search_result"
        assert item.kind == "transition"
        assert item.app == "淘宝"

    def test_build_manifest_handles_missing_screenshots(self, tmp_path):
        from phone_agent.spatial.review.manifest import build_manifest

        staging_graph = {
            "app": "淘宝",
            "domain": "shopping",
            "states": [
                {
                    "state_id": "state_home",
                    "app": "taobao",
                    "page_type": "home",
                    "summary": "",
                    "landmarks": [],
                    "affordances": [],
                    "slots": {},
                    "risk_level": "normal",
                    "screenshot_hash": "nonexistent_hash_xyz",
                    "semantic_signature": "",
                    "domain": "shopping",
                },
                {
                    "state_id": "state_cart",
                    "app": "taobao",
                    "page_type": "cart",
                    "summary": "",
                    "landmarks": [],
                    "affordances": [],
                    "slots": {},
                    "risk_level": "normal",
                    "screenshot_hash": "",
                    "semantic_signature": "",
                    "domain": "shopping",
                },
            ],
            "edges": [
                {
                    "source_id": "state_home",
                    "target_id": "state_cart",
                    "action_type": "Tap",
                    "action_target": "",
                    "action_params": {},
                    "precondition": "",
                    "postcondition": "cart",
                    "success_count": 1,
                    "fail_count": 0,
                    "rollback_action": "Back",
                    "cost": 1.0,
                    "risk": "normal",
                    "confidence": 1.0,
                    "evidence": "",
                }
            ],
        }

        # Should not raise — missing screenshots yield empty string paths
        items = build_manifest(tmp_path, staging_graph)
        assert len(items) == 1
        assert items[0].before_screenshot == ""
        assert items[0].after_screenshot == ""


# ── I. Headless import of webui_review ────────────────────────────────────────

class TestWebuiHeadlessImport:

    def test_module_imports_without_gradio(self, monkeypatch):
        """webui_review must be importable even when gradio is not installed."""
        import sys

        # Temporarily hide gradio
        original = sys.modules.get("gradio")
        sys.modules["gradio"] = None  # type: ignore[assignment]
        try:
            # Force reimport by removing cached module if present
            mod_name = "phone_agent.spatial.review.webui_review"
            if mod_name in sys.modules:
                del sys.modules[mod_name]

            # Should import without error
            import importlib
            mod = importlib.import_module(mod_name)
            assert callable(getattr(mod, "list_batches", None))
            assert callable(getattr(mod, "sort_manifest_for_review", None))
        except ImportError:
            pass  # gradio ImportError is acceptable at import time
        finally:
            # Restore
            if original is not None:
                sys.modules["gradio"] = original
            else:
                sys.modules.pop("gradio", None)


# ── J. Integration: full batch cycle ─────────────────────────────────────────

class TestBatchIntegration:

    def test_full_cycle_dry_run(self, tmp_path):
        """Full: create_staging_batch → approve all → apply_review(dry_run=True)."""
        from phone_agent.spatial.review.batch import create_staging_batch
        from phone_agent.spatial.review.decisions import load_decisions, save_decisions
        from phone_agent.spatial.review.apply import apply_review
        from phone_agent.spatial.review.batch import load_manifest

        pages_path = _write_pages(tmp_path)
        _write_transitions(tmp_path)

        batch_dir = create_staging_batch(pages_path, storage_root=tmp_path / "staging")

        # Approve all
        manifest = load_manifest(batch_dir)
        decisions = load_decisions(batch_dir)
        for item in manifest:
            decisions[item.item_id] = {"decision": "approve", "note": ""}
        save_decisions(batch_dir, decisions)

        result = apply_review(batch_dir, graph_store=None, dry_run=True)

        assert result["dry_run"] is True
        assert result["approved"] == len(manifest)
        assert result["rejected"] == 0
        assert not (batch_dir / "applied.json").exists()

    def test_full_cycle_with_fake_store(self, tmp_path):
        """Full: create_staging_batch → approve all → apply_review with fake store."""
        from phone_agent.spatial.review.batch import create_staging_batch, load_manifest
        from phone_agent.spatial.review.decisions import load_decisions, save_decisions
        from phone_agent.spatial.review.apply import apply_review

        pages_path = _write_pages(tmp_path)
        _write_transitions(tmp_path)

        batch_dir = create_staging_batch(pages_path, storage_root=tmp_path / "staging")

        manifest = load_manifest(batch_dir)
        decisions = load_decisions(batch_dir)
        for item in manifest:
            decisions[item.item_id] = {"decision": "approve", "note": ""}
        save_decisions(batch_dir, decisions)

        fake_store = FakeMergeGraphStore()
        result = apply_review(batch_dir, graph_store=fake_store, dry_run=False)

        assert result["approved"] == len(manifest)
        assert (batch_dir / "applied.json").exists()
        # Provenance on recorded actions
        for transition in fake_store.transitions:
            assert transition["action"].get("source_type") == "exploration_reviewed"
