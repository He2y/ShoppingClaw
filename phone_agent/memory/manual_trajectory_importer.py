"""Import MobiAgent manually recorded trajectories into SpatialGraphMemory.

Manual trajectories are richer than the old Neo4j projection: each run usually
contains page summaries in ``*.txt``, screenshots in ``*.jpg``, and VLM-style
reasoning/action records in ``react.json``.  This importer treats ``react.json``
as the primary action source and falls back to ``temp.json`` / ``actions.json``
only when needed.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .graph_store import GraphStore
from .spatial_graph_memory import GoalSpec, PageState, SpatialGraphMemory


@dataclass(frozen=True)
class ManualTrajectoryImportResult:
    trajectories_imported: int = 0
    pages_imported: int = 0
    transitions_imported: int = 0
    tasks_imported: int = 0
    skipped: tuple[str, ...] = ()
    apps: dict[str, int] = field(default_factory=dict)
    page_types: dict[str, int] = field(default_factory=dict)
    persisted_to_graph: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectories_imported": self.trajectories_imported,
            "pages_imported": self.pages_imported,
            "transitions_imported": self.transitions_imported,
            "tasks_imported": self.tasks_imported,
            "skipped": list(self.skipped),
            "apps": dict(self.apps),
            "page_types": dict(self.page_types),
            "persisted_to_graph": self.persisted_to_graph,
        }


class ManualTrajectoryImporter:
    """Convert manual trajectory folders into canonical spatial graph objects."""

    def __init__(self, graph_store: GraphStore | None = None):
        self.graph_store = graph_store
        self.memory = SpatialGraphMemory(graph_store)

    def import_directory(
        self,
        root: str | Path,
        *,
        persist: bool = True,
        limit: int | None = None,
    ) -> ManualTrajectoryImportResult:
        trajectory_dirs = self.find_trajectory_dirs(root)
        if limit is not None:
            trajectory_dirs = trajectory_dirs[:limit]

        skipped: list[str] = []
        apps: Counter[str] = Counter()
        page_types: Counter[str] = Counter()
        trajectories = pages = transitions = tasks = 0

        for directory in trajectory_dirs:
            try:
                result = self.import_trajectory_dir(directory, persist=persist)
            except Exception as exc:
                skipped.append(f"{directory}: {type(exc).__name__}: {exc}")
                continue

            trajectories += 1
            pages += result["pages"]
            transitions += result["transitions"]
            tasks += 1 if result["task_imported"] else 0
            apps[result["app"]] += 1
            page_types.update(result["page_types"])

        return ManualTrajectoryImportResult(
            trajectories_imported=trajectories,
            pages_imported=pages,
            transitions_imported=transitions,
            tasks_imported=tasks,
            skipped=tuple(skipped),
            apps=dict(apps),
            page_types=dict(page_types),
            persisted_to_graph=bool(persist and self.graph_store and getattr(self.graph_store, "driver", None)),
        )

    def import_trajectory_dir(self, directory: str | Path, *, persist: bool = True) -> dict[str, Any]:
        run_dir = Path(directory)
        metadata = self._read_json(run_dir / "actions.json")
        app = str(metadata.get("app_name") or self._path_part(run_dir, -3) or "unknown")
        task_type = str(metadata.get("task_type") or self._path_part(run_dir, -2) or "manual")
        descriptions = self._normalize_descriptions(metadata.get("task_description"))
        primary_task = descriptions[0] if descriptions else task_type
        action_records = self._read_action_records(run_dir)
        pages = self._read_pages(run_dir, app=app, task=primary_task)

        for state in pages:
            self.memory._local_states[state.state_id] = state
            if persist:
                self.memory._persist_page_state(state)

        transition_count = 0
        for index, action_record in enumerate(action_records):
            if index + 1 >= len(pages):
                break
            action = self._action_from_react_record(action_record, run_dir=run_dir, index=index + 1)
            self.memory.record_observation(pages[index], action, pages[index + 1], outcome="success")
            transition_count += 1

        goal_spec = GoalSpec.from_task(primary_task, app).to_dict()
        task_imported = False
        if persist and pages and self.graph_store and getattr(self.graph_store, "driver", None):
            task_id = self._task_id(app, task_type, run_dir)
            self.graph_store.upsert_task_target(
                target_id=task_id,
                app=app,
                task_type=task_type,
                descriptions=descriptions,
                start_state_id=pages[0].state_id,
                end_state_id=pages[-1].state_id,
                goal_spec=goal_spec,
                source_path=str(run_dir),
                source_type="manual",
            )
            task_imported = True

        return {
            "app": app,
            "task_type": task_type,
            "pages": len(pages),
            "transitions": transition_count,
            "task_imported": task_imported or not persist,
            "page_types": Counter(state.page_type for state in pages),
        }

    @staticmethod
    def find_trajectory_dirs(root: str | Path) -> list[Path]:
        return sorted(path.parent for path in Path(root).rglob("actions.json"))

    def _read_pages(self, run_dir: Path, *, app: str, task: str) -> list[PageState]:
        txt_files = sorted(
            (path for path in run_dir.glob("*.txt") if path.stem.isdigit()),
            key=lambda path: int(path.stem),
        )
        pages: list[PageState] = []
        for txt_path in txt_files:
            step = int(txt_path.stem)
            summary = txt_path.read_text(encoding="utf-8").strip()
            screenshot_path = run_dir / f"{step}.jpg"
            screenshot_hash = self._file_hash(screenshot_path) if screenshot_path.exists() else ""
            state = self.memory.build_page_state(
                ui_hash=screenshot_hash,
                semantic_layout=f"{app} {summary}",
                task=task,
                app=app,
                summary=summary,
            )
            pages.append(state)
        return pages

    def _read_action_records(self, run_dir: Path) -> list[dict[str, Any]]:
        for name in ("react.json", "temp.json"):
            path = run_dir / name
            data = self._read_json(path)
            if isinstance(data, list) and data:
                return [item for item in data if isinstance(item, dict)]

        metadata = self._read_json(run_dir / "actions.json")
        actions = metadata.get("actions")
        if isinstance(actions, list):
            return [item for item in actions if isinstance(item, dict)]
        return []

    @staticmethod
    def _action_from_react_record(record: dict[str, Any], *, run_dir: Path, index: int) -> dict[str, Any]:
        function = record.get("function") if isinstance(record.get("function"), dict) else {}
        params = function.get("parameters") if isinstance(function.get("parameters"), dict) else {}
        raw_name = str(function.get("name") or record.get("action") or record.get("type") or "unknown")
        action_name = {
            "click": "Tap",
            "tap": "Tap",
            "input": "Type",
            "type": "Type",
            "swipe": "Swipe",
            "back": "Back",
        }.get(raw_name.lower(), raw_name)
        target = params.get("target_element") or params.get("target") or params.get("element") or ""
        text = params.get("text") or record.get("text") or ""
        action: dict[str, Any] = {
            "_metadata": "do",
            "action": action_name,
            "action_type": action_name,
            "semantic_target": str(target or text or ""),
            "reasoning": str(record.get("reasoning") or ""),
            "source_type": "manual",
            "source_path": str(run_dir),
            "source_step": index,
            # Manual target elements are semantic labels, not coordinates.
            # Keep below direct-execution threshold so VLM/grounding resolves it.
            "confidence": 0.75,
        }
        if text:
            action["text"] = str(text)
        action["raw_function"] = {"name": raw_name, "parameters": params}
        return action

    @staticmethod
    def _normalize_descriptions(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if value is None:
            return []
        text = str(value).strip()
        return [text] if text else []

    @staticmethod
    def _read_json(path: Path) -> Any:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _file_hash(path: Path) -> str:
        return hashlib.md5(path.read_bytes()).hexdigest()

    @staticmethod
    def _path_part(path: Path, index: int) -> str:
        try:
            return path.parts[index]
        except IndexError:
            return ""

    @staticmethod
    def _task_id(app: str, task_type: str, run_dir: Path) -> str:
        digest = hashlib.md5(str(run_dir).encode("utf-8")).hexdigest()[:8]
        run_name = run_dir.name
        return f"{app}_{task_type}_{run_name}_{digest}"
