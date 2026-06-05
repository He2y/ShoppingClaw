"""Trajectory Reviewer — VLM-powered quality gate for graph self-evolution.

After a task completes, reviews the recorded trajectory using a strong VLM
to extract validated page transitions.  Only approved transitions are
imported into Neo4j, preventing graph pollution from noise (dialogs,
loading screens, mis-classifications).

Pipeline:
  1. Reconstruct transition chain from step_details (page_type + action)
  2. Filter obvious non-transitions (same page, finish, unknown)
  3. Check which transitions are NEW (not already in graph)
  4. Ask strong VLM to validate new transitions
  5. Import approved transitions to Neo4j
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TransitionCandidate:
    """A potential new graph transition extracted from a trajectory."""

    source_page: str
    target_page: str
    action_type: str
    action_params: dict[str, Any]
    thinking_context: str
    step_index: int

    @property
    def edge_key(self) -> str:
        return f"{self.source_page}|{self.action_type}|{self.target_page}"


@dataclass
class ReviewResult:
    """Result of trajectory review."""

    total_steps: int = 0
    transitions_extracted: int = 0
    already_in_graph: int = 0
    new_candidates: int = 0
    vlm_approved: int = 0
    imported: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)


class TrajectoryReviewer:
    """Reviews completed trajectories and imports validated transitions.

    Uses the strong VLM (AMSG_STRONG_VLM) for transition validation,
    same model used by PageClassifier and Pre-Plan.
    """

    def __init__(
        self,
        graph_store: Any,
        verbose: bool = True,
    ) -> None:
        self._graph_store = graph_store
        self._verbose = verbose
        self._vlm_client = None
        self._vlm_model = ""

    def _ensure_vlm(self) -> bool:
        """Lazy-init strong VLM client."""
        if self._vlm_client is not None:
            return True
        from dotenv import load_dotenv
        load_dotenv()
        key = os.getenv("AMSG_STRONG_VLM_API_KEY", "")
        url = os.getenv("AMSG_STRONG_VLM_BASE_URL", "")
        model = os.getenv("AMSG_STRONG_VLM_MODEL", "")
        if not (key and url and model):
            return False
        from openai import OpenAI
        self._vlm_client = OpenAI(api_key=key, base_url=url)
        self._vlm_model = model
        return True

    def review_and_import(
        self,
        trajectory: dict[str, Any],
        app: str = "淘宝",
    ) -> ReviewResult:
        """Review a trajectory and import validated new transitions.

        Args:
            trajectory: Entry from pending_trajectories.json with step_details
            app: App name for Neo4j node matching

        Returns:
            ReviewResult with counts and details
        """
        result = ReviewResult()
        steps = trajectory.get("step_details", [])
        result.total_steps = len(steps)

        if not steps or not trajectory.get("success"):
            return result

        # 1. Extract transition chain
        candidates = self._extract_transitions(steps)
        result.transitions_extracted = len(candidates)

        if not candidates:
            return result

        # 2. Check which are new (not in graph)
        new_candidates = []
        for c in candidates:
            if self._transition_exists(c, app):
                result.already_in_graph += 1
            else:
                new_candidates.append(c)
        result.new_candidates = len(new_candidates)

        if not new_candidates:
            if self._verbose:
                print(f"[trajectory] 无新转换 ({result.already_in_graph} 已存在)")
            return result

        # 3. VLM validation
        if not self._ensure_vlm():
            if self._verbose:
                print("[trajectory] Strong VLM 不可用，跳过审核")
            return result

        approved = self._vlm_validate(new_candidates, trajectory.get("task", ""))
        result.vlm_approved = len(approved)

        if not approved:
            if self._verbose:
                print(f"[trajectory] VLM 审核: {len(new_candidates)} 候选, 0 通过")
            return result

        # 4. Import to Neo4j
        for c in approved:
            success = self._import_transition(c, app)
            if success:
                result.imported += 1
                result.details.append({
                    "transition": f"{c.source_page} → {c.target_page}",
                    "action": c.action_type,
                    "status": "imported",
                })
                if self._verbose:
                    print(f"[trajectory] ✅ 导入: {c.source_page} → {c.target_page} ({c.action_type})")

        if self._verbose:
            print(
                f"[trajectory] 审核完成: {result.transitions_extracted} 转换, "
                f"{result.already_in_graph} 已有, {result.new_candidates} 新发现, "
                f"{result.vlm_approved} VLM通过, {result.imported} 已导入"
            )
        return result

    # ── Step 1: Extract transitions ─────────────────────────────────

    def _extract_transitions(
        self, steps: list[dict[str, Any]]
    ) -> list[TransitionCandidate]:
        """Build transition chain from consecutive step page_types."""
        candidates = []
        for i in range(len(steps) - 1):
            src_page = steps[i].get("page_type", "")
            tgt_page = steps[i + 1].get("page_type", "")
            action_type = steps[i].get("action_type", "")

            if not src_page or not tgt_page:
                continue
            if src_page == tgt_page:
                continue
            if action_type in ("finish", "unknown", "Wait"):
                continue
            if src_page == "unknown" or tgt_page == "unknown":
                continue

            candidates.append(TransitionCandidate(
                source_page=src_page,
                target_page=tgt_page,
                action_type=action_type,
                action_params=steps[i].get("action_params", {}),
                thinking_context=steps[i].get("thinking", "")[:100],
                step_index=i,
            ))
        return candidates

    # ── Step 2: Check existence ─────────────────────────────────────

    # Page types that PageClassifier may confuse with each other.
    # If a transition exists for any equivalent type, treat it as existing.
    _EQUIVALENT_PAGE_TYPES = {
        "store": ("store", "category"),
        "category": ("store", "category"),
        "home": ("home",),
        "search_input": ("search_input",),
        "search_result": ("search_result",),
        "product_detail": ("product_detail",),
        "spec_selection": ("spec_selection",),
        "cart": ("cart",),
        "checkout": ("checkout",),
        "dialog": ("dialog", "permission"),
        "permission": ("dialog", "permission"),
    }

    def _transition_exists(self, candidate: TransitionCandidate, app: str) -> bool:
        """Check if this transition (or a semantically equivalent one) exists."""
        if not self._graph_store or not getattr(self._graph_store, "driver", None):
            return False

        src_variants = self._EQUIVALENT_PAGE_TYPES.get(
            candidate.source_page, (candidate.source_page,)
        )
        tgt_variants = self._EQUIVALENT_PAGE_TYPES.get(
            candidate.target_page, (candidate.target_page,)
        )

        try:
            with self._graph_store.driver.session(database=self._graph_store.database) as s:
                result = s.run(
                    """
                    MATCH (src:UIState)-[:NEXT_ACTION]->(a:Action)-[:PRODUCES]->(tgt:UIState)
                    WHERE src.page_type IN $src_list AND tgt.page_type IN $tgt_list
                      AND a.type = $atype
                      AND ($app = '' OR src.app CONTAINS $app)
                    RETURN count(*) AS cnt
                    """,
                    src_list=list(src_variants),
                    tgt_list=list(tgt_variants),
                    atype=candidate.action_type,
                    app=app,
                )
                return result.single()["cnt"] > 0
        except Exception:
            return False

    # ── Step 3: VLM validation ──────────────────────────────────────

    def _vlm_validate(
        self,
        candidates: list[TransitionCandidate],
        task: str,
    ) -> list[TransitionCandidate]:
        """Ask strong VLM to validate which transitions are genuine."""
        transitions_text = "\n".join(
            f"{i+1}. {c.source_page} → {c.target_page} (action={c.action_type}, "
            f"context=\"{c.thinking_context[:60]}\")"
            for i, c in enumerate(candidates)
        )

        prompt = (
            "你是一个手机 App 页面导航图谱的质量审核员。\n\n"
            f"用户完成了任务: \"{task}\"\n\n"
            "以下是从执行轨迹中提取的新页面转换（图谱中尚未记录的）。\n"
            "请判断每条转换是否是**真实有效的页面导航**。\n\n"
            "有效转换的标准:\n"
            "- 从一个明确的页面类型跳转到另一个明确的页面类型\n"
            "- 操作类型与转换方向合理（如 Tap 搜索按钮 → search_result）\n"
            "- 不是由临时弹窗、网络错误、广告等干扰造成的\n\n"
            "无效转换的例子:\n"
            "- dialog → home（关闭广告弹窗，不是稳定的导航路径）\n"
            "- 由于页面分类错误导致的虚假转换\n\n"
            f"待审核转换:\n{transitions_text}\n\n"
            "请逐条判断，输出 JSON 格式:\n"
            '[{"index": 1, "valid": true, "reason": "从搜索结果进入店铺，合理路径"}, ...]'
        )

        try:
            response = self._vlm_client.chat.completions.create(
                model=self._vlm_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
                temperature=0.0,
                stream=False,
            )
            content = response.choices[0].message.content or ""

            import re
            json_match = re.search(r"\[[\s\S]*\]", content)
            if not json_match:
                return candidates  # Parse failure → approve all (conservative)

            verdicts = json.loads(json_match.group())
            approved = []
            for v in verdicts:
                idx = v.get("index", 0) - 1
                if 0 <= idx < len(candidates) and v.get("valid"):
                    approved.append(candidates[idx])
                    if self._verbose:
                        print(f"[trajectory] VLM ✅ {candidates[idx].edge_key}: {v.get('reason', '')[:40]}")
                elif 0 <= idx < len(candidates):
                    if self._verbose:
                        print(f"[trajectory] VLM ❌ {candidates[idx].edge_key}: {v.get('reason', '')[:40]}")
            return approved

        except Exception as e:
            if self._verbose:
                print(f"[trajectory] VLM 审核失败: {e}")
            return []

    # ── Step 4: Import to Neo4j ─────────────────────────────────────

    def _import_transition(
        self,
        candidate: TransitionCandidate,
        app: str,
    ) -> bool:
        """Write a validated transition to Neo4j."""
        if not self._graph_store or not getattr(self._graph_store, "driver", None):
            return False

        now_ms = int(time.time() * 1000)
        element = candidate.action_params.get("element", [500, 500])
        region = candidate.action_params.get("region", "")

        try:
            with self._graph_store.driver.session(database=self._graph_store.database) as s:
                # Find existing UIState nodes
                src_result = s.run(
                    "MATCH (n:UIState) WHERE n.page_type = $pt AND n.app CONTAINS $app "
                    "RETURN n.state_id AS sid LIMIT 1",
                    pt=candidate.source_page, app=app,
                )
                src_rec = src_result.single()

                tgt_result = s.run(
                    "MATCH (n:UIState) WHERE n.page_type = $pt AND n.app CONTAINS $app "
                    "RETURN n.state_id AS sid LIMIT 1",
                    pt=candidate.target_page, app=app,
                )
                tgt_rec = tgt_result.single()

                # Create missing UIState nodes
                src_id = src_rec["sid"] if src_rec else f"state_{app}_{candidate.source_page}_auto_{now_ms}"
                tgt_id = tgt_rec["sid"] if tgt_rec else f"state_{app}_{candidate.target_page}_auto_{now_ms}"

                if not src_rec:
                    s.run(
                        "CREATE (n:UIState {state_id: $sid, app: $app, page_type: $pt})",
                        sid=src_id, app=app, pt=candidate.source_page,
                    )
                if not tgt_rec:
                    s.run(
                        "CREATE (n:UIState {state_id: $sid, app: $app, page_type: $pt})",
                        sid=tgt_id, app=app, pt=candidate.target_page,
                    )

                # Create Action node + relationships
                action_id = f"act_auto_{candidate.source_page}_{candidate.target_page}_{now_ms}"
                target_locator = json.dumps({
                    "coordinate_space": "normalized_1000",
                    "element": element,
                })
                dist_json = json.dumps({"outcomes": {candidate.target_page: 1}})

                s.run(
                    """
                    MATCH (src:UIState {state_id: $src_id})
                    MATCH (tgt:UIState {state_id: $tgt_id})
                    CREATE (src)-[:NEXT_ACTION {confidence: 1.0, frequency: 1}]->(action:Action {
                        action_id: $action_id,
                        type: $atype,
                        region: $region,
                        semantic_target: $semantic_target,
                        target_locator: $target_locator,
                        expected_postcondition: $tgt_pt,
                        source_page_type: $src_pt,
                        target_page_type: $tgt_pt,
                        lifecycle_stage: 'hypothesis',
                        verification_count: 1,
                        dominance_ratio: 1.0,
                        outcome_entropy: 0.0,
                        outcome_distribution_json: $dist,
                        risk_level: 'normal',
                        created_at: $now,
                        updated_at: $now
                    })-[:PRODUCES]->(tgt)
                    """,
                    src_id=src_id, tgt_id=tgt_id,
                    action_id=action_id,
                    atype=candidate.action_type,
                    region=region,
                    semantic_target=f"{candidate.action_type} → {candidate.target_page}",
                    target_locator=target_locator,
                    src_pt=candidate.source_page,
                    tgt_pt=candidate.target_page,
                    dist=dist_json,
                    now=now_ms,
                )
                return True
        except Exception as e:
            if self._verbose:
                print(f"[trajectory] Neo4j 导入失败: {e}")
            return False
