"""Session memory file: the durable anchor against small-model forgetting.

Created by the milestone supervisor at task start (original task + product
info + subtasks), revised at every checkpoint, rendered into the GUI model's
context each step. The original task is NEVER rewritten — it is the anchor
the executor re-reads when it would otherwise drift.

Pure data module: no VLM dependency, fully unit-testable.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_MAX_FACTS_RENDERED = 5
_MAX_REVISIONS_KEPT = 30


@dataclass
class SubTask:
    step_id: int
    description: str
    target_page: str = ""
    status: str = "pending"  # pending | current | done | skipped
    completed_at_step: int = 0
    evidence: str = ""


@dataclass
class VerifiedFact:
    fact: str
    kind: str = "other"  # product_selected | price_confirmed | cart_added | filter_applied | other
    step: int = 0
    source: str = "vlm_checkpoint"  # vlm_checkpoint | mechanical


@dataclass
class RevisionEntry:
    step: int
    trigger: str  # init | interval | subtask_done | stagnation | finish_gate | final_confirm
    summary: str = ""
    changes: str = ""


@dataclass
class SessionMemoryFile:
    session_id: str
    original_task: str  # never rewritten — the anti-drift anchor
    platform: str = ""
    product: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, str] = field(default_factory=dict)
    subtasks: list[SubTask] = field(default_factory=list)
    verified_facts: list[VerifiedFact] = field(default_factory=list)
    revisions: list[RevisionEntry] = field(default_factory=list)
    vlm_call_count: int = 0
    status: str = "active"  # active | completed | failed
    created_at: str = ""
    updated_at: str = ""
    _path: Path | None = field(default=None, repr=False, compare=False)

    # ── construction ────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        task: str,
        vlm_plan: dict[str, Any] | None,
        *,
        sessions_dir: str | Path,
        platform: str = "",
    ) -> "SessionMemoryFile":
        """Build a memory file from the supervisor's initial decomposition."""
        plan = vlm_plan or {}
        ts = time.strftime("%Y%m%d_%H%M%S")
        slug = re.sub(r"[^\w一-鿿]", "", task)[:30]
        memory = cls(
            session_id=f"{ts}_{slug}",
            original_task=task,
            platform=platform,
            product={
                "name": str(plan.get("product") or ""),
                "target_action": str(plan.get("target_action") or ""),
            },
            constraints={
                k: str(v)
                for k, v in (plan.get("specs") or {}).items()
                if v and isinstance(plan.get("specs"), dict)
            },
            subtasks=[
                SubTask(
                    step_id=i,
                    description=str(item.get("description") or item),
                    target_page=str(item.get("target_page") or "") if isinstance(item, dict) else "",
                    status="current" if i == 1 else "pending",
                )
                for i, item in enumerate(plan.get("steps") or [], start=1)
                if (isinstance(item, dict) and item.get("description")) or isinstance(item, str)
            ],
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        if plan.get("search_query"):
            memory.constraints.setdefault("query", str(plan["search_query"]))
        memory.revisions.append(RevisionEntry(step=0, trigger="init", summary="任务拆解完成"))
        memory._path = Path(sessions_dir) / f"{memory.session_id}.json"
        return memory

    # ── persistence (atomic, best-effort) ───────────────────────────────

    def save(self) -> bool:
        if self._path is None:
            return False
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = self.to_dict()
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, self._path)
            return True
        except Exception:
            return False  # in-memory object stays the source of truth

    @classmethod
    def load(cls, path: str | Path) -> "SessionMemoryFile | None":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            memory = cls(
                session_id=data["session_id"],
                original_task=data["original_task"],
                platform=data.get("platform", ""),
                product=data.get("product", {}),
                constraints=data.get("constraints", {}),
                subtasks=[SubTask(**s) for s in data.get("subtasks", [])],
                verified_facts=[VerifiedFact(**f) for f in data.get("verified_facts", [])],
                revisions=[RevisionEntry(**r) for r in data.get("revisions", [])],
                vlm_call_count=data.get("vlm_call_count", 0),
                status=data.get("status", "active"),
                created_at=data.get("created_at", ""),
                updated_at=data.get("updated_at", ""),
            )
            memory._path = Path(path)
            return memory
        except Exception:
            return None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_path", None)
        return data

    # ── checkpoint merging ──────────────────────────────────────────────

    def apply_checkpoint(self, result: Any, step: int) -> None:
        """Merge a supervisor CheckpointResult into the memory file."""
        completed = set(int(i) for i in getattr(result, "completed_step_ids", []) or [])
        for sub in self.subtasks:
            if sub.step_id in completed and sub.status != "done":
                sub.status = "done"
                sub.completed_at_step = step
                sub.evidence = "vlm_checkpoint"

        existing = {f.fact for f in self.verified_facts}
        for item in getattr(result, "new_facts", []) or []:
            fact = str(item.get("fact") or "").strip() if isinstance(item, dict) else str(item).strip()
            if fact and fact not in existing:
                kind = str(item.get("kind") or "other") if isinstance(item, dict) else "other"
                self.verified_facts.append(
                    VerifiedFact(fact=fact, kind=kind, step=step)
                )
                existing.add(fact)

        self.revisions.append(RevisionEntry(
            step=step,
            trigger=str(getattr(result, "trigger", "") or "checkpoint"),
            summary=str(getattr(result, "current_situation", "") or "")[:200],
            changes=str(getattr(result, "changes", "") or ""),
        ))
        del self.revisions[:-_MAX_REVISIONS_KEPT]
        self.vlm_call_count += 1
        self.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")

    def sync_subtasks_from_plan(self, plan: Any) -> None:
        """Mirror a revised TaskPlan back into the memory file's subtasks."""
        steps = getattr(plan, "steps", None)
        if not steps:
            return
        by_id = {s.step_id: s for s in self.subtasks}
        rebuilt: list[SubTask] = []
        for step in steps:
            existing = by_id.get(step.step_id)
            if existing is not None:
                existing.description = step.description
                existing.target_page = step.target_page
                existing.status = step.status
                rebuilt.append(existing)
            else:
                rebuilt.append(SubTask(
                    step_id=step.step_id,
                    description=step.description,
                    target_page=step.target_page,
                    status=step.status,
                ))
        self.subtasks = rebuilt

    def finalize(self, success: bool) -> None:
        self.status = "completed" if success else "failed"
        self.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save()

    # ── rendering ───────────────────────────────────────────────────────

    def render_injection(self) -> str:
        """~12-line digest injected into the GUI model's context each step."""
        lines = ["【会话记忆】", f"原始任务: {self.original_task}"]
        product_name = self.product.get("name") or ""
        constraint_text = "，".join(
            f"{k}={v}" for k, v in self.constraints.items() if k != "query"
        )
        if product_name or constraint_text:
            line = f"目标商品: {product_name}" if product_name else "目标商品: (未指定)"
            if constraint_text:
                line += f"（约束: {constraint_text}）"
            lines.append(line)
        if self.verified_facts:
            lines.append("已验证事实:")
            for fact in self.verified_facts[-_MAX_FACTS_RENDERED:]:
                lines.append(f"  ✔ {fact.fact}（Step {fact.step}）")
        if self.subtasks:
            lines.append("子任务进度:")
            for sub in self.subtasks:
                marker = {
                    "done": "[done]", "current": "[current]",
                    "pending": "[pending]", "skipped": "[skipped]",
                }.get(sub.status, "[?]")
                lines.append(f"  {sub.step_id}. {marker} {sub.description}")
        return "\n".join(lines)

    def brief_line(self) -> str:
        """One-line progress written to state.overall_progress."""
        done = sum(1 for s in self.subtasks if s.status == "done")
        total = len(self.subtasks)
        current = next(
            (s.description for s in self.subtasks if s.status == "current"), ""
        )
        line = f"已完成{done}/{total}个子任务"
        if current:
            line += f"；当前: {current}"
        return line
