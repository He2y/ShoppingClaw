"""Gradio review page for staging batches.

Launch:
    python -m phone_agent.spatial.review.webui_review --port 7861

Gradio import is guarded so headless test environments can import the module
without a Gradio installation.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Pure helper functions (testable without UI) ────────────────────────────


def list_batches(storage_root: str | Path = "memory_db/staging") -> list[dict[str, Any]]:
    """List staging batches newest-first with status info."""
    from .batch import list_batches as _list
    return _list(storage_root)


def format_card_text(item: Any) -> str:
    """Format a ReviewItem as a markdown card string for display."""
    lines = [
        f"**{item.source_page_type} → {item.target_page_type}**",
        f"**动作**: {item.action_description}",
        f"**来源摘要**: {item.source_summary}",
        f"**目标摘要**: {item.target_summary}",
        f"**观测次数**: {item.observations}",
        f"**探索置信度**: {item.explorer_confidence or '未知'}",
        f"**VLM 判定**: {item.vlm_verdict}",
    ]
    if item.vlm_reason:
        lines.append(f"**VLM 理由**: {item.vlm_reason}")

    warnings = []
    if item.blind_page_check.startswith("mismatch"):
        warnings.append(f"⚠️ 分类存疑: {item.blind_page_check}")
    if item.explorer_confidence == "low_confidence":
        warnings.append("⚠️ 低置信度")
    for w in warnings:
        lines.append(w)

    return "\n\n".join(lines)


def sort_manifest_for_review(items: list[Any]) -> list[Any]:
    """Sort manifest items: suspicious ones first (mismatch / low_confidence), then rest."""
    def _priority(item: Any) -> int:
        if item.blind_page_check.startswith("mismatch"):
            return 0
        if item.explorer_confidence == "low_confidence":
            return 1
        return 2

    return sorted(items, key=_priority)


def run_vlm_review_on_batch(batch_dir: Path) -> int:
    """Run VLM review + blind check over unreviewed manifest items.  Returns updated count."""
    from .batch import load_manifest, save_manifest, load_staging_graph
    from .vlm_review import VlmTransitionJudge
    from .manifest import ReviewItem

    manifest = load_manifest(batch_dir)
    staging_graph = load_staging_graph(batch_dir)

    unreviewed = [item for item in manifest if item.vlm_verdict == "unreviewed"]
    if not unreviewed:
        return 0

    judge = VlmTransitionJudge()
    app = staging_graph.get("app", "")

    # Collect page type names for blind classify
    page_type_names: list[str] = list({
        s.get("page_type", "")
        for s in staging_graph.get("states", [])
        if isinstance(s, dict) and s.get("page_type")
    })

    items_dicts = [
        {
            "source_page_type": it.source_page_type,
            "target_page_type": it.target_page_type,
            "action_description": it.action_description,
            "observations": it.observations,
            "explorer_confidence": it.explorer_confidence,
        }
        for it in unreviewed
    ]

    vlm_results = judge.judge_transitions(items_dicts, task_context=f"app={app}")

    updated_count = 0
    # Build new manifest list
    id_to_new: dict[str, ReviewItem] = {}
    for i, item in enumerate(unreviewed):
        verdict = "unreviewed"
        reason = ""
        if i in vlm_results:
            approved, reason = vlm_results[i]
            verdict = "approve" if approved else "reject"
            updated_count += 1

        blind_check = "skipped"
        if item.before_screenshot and item.after_screenshot and judge.available():
            before_path = batch_dir / item.before_screenshot
            after_path = batch_dir / item.after_screenshot
            result = judge.blind_classify_pair(before_path, after_path, page_type_names)
            if result:
                before_cls, after_cls = result
                if (
                    before_cls == item.source_page_type
                    and after_cls == item.target_page_type
                ):
                    blind_check = "match"
                else:
                    blind_check = f"mismatch:{before_cls}->{after_cls}"

        id_to_new[item.item_id] = ReviewItem(
            item_id=item.item_id,
            kind=item.kind,
            app=item.app,
            domain=item.domain,
            source_page_type=item.source_page_type,
            target_page_type=item.target_page_type,
            source_summary=item.source_summary,
            target_summary=item.target_summary,
            action_description=item.action_description,
            action_params=item.action_params,
            before_screenshot=item.before_screenshot,
            after_screenshot=item.after_screenshot,
            observations=item.observations,
            explorer_confidence=item.explorer_confidence,
            vlm_verdict=verdict,
            vlm_reason=reason,
            blind_page_check=blind_check,
        )

    new_manifest = [
        id_to_new.get(item.item_id, item)
        for item in manifest
    ]
    save_manifest(batch_dir, new_manifest)
    return updated_count


# ── Gradio UI ──────────────────────────────────────────────────────────────


def _build_app(storage_root: str = "memory_db/staging") -> Any:
    """Build and return the Gradio Blocks app."""
    try:
        import gradio as gr
    except ImportError as e:
        raise ImportError(
            "Gradio is required for webui_review: pip install gradio"
        ) from e

    from .batch import load_manifest, save_manifest, load_staging_graph, is_applied
    from .decisions import load_decisions, save_decisions, build_default_decisions, summarize
    from .apply import apply_review
    from .manifest import ReviewItem

    state_batch_dir: dict = {"path": None}
    state_items: dict = {"items": [], "index": 0}

    def _get_batch_choices() -> list[str]:
        batches = list_batches(storage_root)
        choices = []
        for b in batches:
            label = b["batch_id"]
            if b["applied"]:
                label += " [已应用]"
            choices.append(label)
        return choices

    def _batch_overview(batch_dir: Path, items: list) -> str:
        """Funnel + pre-filled decision stats so nothing looks silently lost."""
        parts: list[str] = []
        try:
            staging = load_staging_graph(batch_dir)
            quality = staging.get("quality") or {}
            seen = quality.get("transitions_seen")
            src_path = staging.get("source_transitions_path") or ""
            rejected_at_exploration = None
            if src_path and Path(src_path).exists():
                raw = json.loads(Path(src_path).read_text(encoding="utf-8"))
                rejected_at_exploration = len(raw.get("rejected_transitions") or [])
            funnel = f"探索记录 {seen} 条转移"
            if rejected_at_exploration:
                funnel += f"（另有 {rejected_at_exploration} 条在探索期被规则拒绝，不进入审核）"
            funnel += f" → 审核候选 {len(items)} 条"
            parts.append(funnel)
        except Exception:
            pass
        try:
            decisions = load_decisions(batch_dir)
            n_approve = sum(
                1 for it in items
                if decisions.get(it.item_id, {}).get("decision") == "approve"
            )
            parts.append(f"当前决策: approve {n_approve} / reject {len(items) - n_approve}")
        except Exception:
            pass
        return "；".join(parts)

    def _load_batch(batch_label: str) -> tuple:
        batch_id = batch_label.replace(" [已应用]", "").strip()
        batch_dir = Path(storage_root) / batch_id
        state_batch_dir["path"] = batch_dir

        items = load_manifest(batch_dir)
        items = sort_manifest_for_review(items)
        state_items["items"] = items
        state_items["index"] = 0

        total = len(items)
        overview = _batch_overview(batch_dir, items)
        if total == 0:
            return ("暂无候选条目", "", None, None, "approve", f"{overview}\n\n0 / 0")

        item = items[0]
        decisions = load_decisions(batch_dir)
        current_decision = decisions.get(item.item_id, {}).get("decision", "approve")
        before_img = _img_path(batch_dir, item.before_screenshot)
        after_img = _img_path(batch_dir, item.after_screenshot)
        return (
            format_card_text(item),
            item.item_id,
            before_img,
            after_img,
            current_decision,
            f"{overview}\n\n1 / {total}",
        )

    def _navigate(direction: int) -> tuple:
        items = state_items["items"]
        total = len(items)
        if total == 0:
            return ("", "", None, None, "approve", "0 / 0")
        new_idx = max(0, min(total - 1, state_items["index"] + direction))
        state_items["index"] = new_idx
        item = items[new_idx]
        batch_dir = state_batch_dir["path"]
        decisions = load_decisions(batch_dir) if batch_dir else {}
        current_decision = decisions.get(item.item_id, {}).get("decision", "approve")
        before_img = _img_path(batch_dir, item.before_screenshot)
        after_img = _img_path(batch_dir, item.after_screenshot)
        return (
            format_card_text(item),
            item.item_id,
            before_img,
            after_img,
            current_decision,
            f"{new_idx + 1} / {total}",
        )

    def _save_decision(item_id: str, decision: str) -> str:
        batch_dir = state_batch_dir["path"]
        if not batch_dir or not item_id:
            return "未选择批次"
        decisions = load_decisions(batch_dir)
        entry = decisions.get(item_id, {})
        entry["decision"] = decision
        decisions[item_id] = entry
        save_decisions(batch_dir, decisions)
        return f"已保存: {item_id[:8]}... → {decision}"

    def _accept_all_vlm() -> str:
        batch_dir = state_batch_dir["path"]
        if not batch_dir:
            return "未选择批次"
        items = load_manifest(batch_dir)
        decisions = load_decisions(batch_dir)
        updated = 0
        for item in items:
            if item.vlm_verdict in ("approve", "reject"):
                entry = decisions.get(item.item_id, {})
                entry["decision"] = item.vlm_verdict
                decisions[item.item_id] = entry
                updated += 1
        save_decisions(batch_dir, decisions)
        return f"已采纳 {updated} 条 VLM 判定"

    def _run_vlm(progress=gr.Progress()) -> str:
        batch_dir = state_batch_dir["path"]
        if not batch_dir:
            return "未选择批次"
        progress(0, desc="运行 VLM 审核...")
        count = run_vlm_review_on_batch(batch_dir)
        # Reload items with updated vlm verdicts
        items = load_manifest(batch_dir)
        items = sort_manifest_for_review(items)
        state_items["items"] = items
        state_items["index"] = 0
        return f"VLM 审核完成，更新 {count} 条"

    def _apply(confirmed: bool) -> str:
        if not confirmed:
            return "请先勾选确认框再应用"
        batch_dir = state_batch_dir["path"]
        if not batch_dir:
            return "未选择批次"
        if is_applied(batch_dir):
            return "该批次已经应用过，不可重复"
        db = os.getenv("AMSG_RUNTIME_GRAPH_DATABASE", "shopping-spatial-v4")
        try:
            from phone_agent.memory.graph_store import GraphStore
            graph_store = GraphStore(database=db)
        except Exception as exc:
            logger.warning("apply: could not connect to GraphStore: %s", exc)
            graph_store = None
        try:
            result = apply_review(batch_dir, graph_store=graph_store)
        except RuntimeError as exc:
            return f"应用失败: {exc}"
        finally:
            if graph_store is not None:
                try:
                    graph_store.close()
                except Exception:
                    pass
        report = result.get("promote_report") or {}
        promoted = report.get("transitions_promoted", "?")
        filtered = report.get("transitions_filtered", 0)
        text = (
            f"✅ 已应用: 批准 {result.get('approved', 0)} 条 / 拒绝 {result.get('rejected', 0)} 条；"
            f"实际入图 {promoted} 条, persisted={result.get('persisted', False)}"
        )
        if filtered:
            text += f"\n⚠️ 有 {filtered} 条批准的边在入图阶段被内部规则过滤，请检查日志"
        if not result.get("persisted", False):
            text += "\n⚠️ Neo4j 未连接，本次未持久化"
        return text

    def _img_path(batch_dir: Path | None, rel: str) -> str | None:
        if not batch_dir or not rel:
            return None
        p = batch_dir / rel
        return str(p) if p.exists() else None

    _EMPTY_VIEW = ("暂无批次", "", None, None, "approve", "0 / 0")

    def _init_load() -> tuple:
        """Populate the dropdown AND load the first batch.

        A dropdown whose initial value never *changes* (single batch) fires
        no change event, leaving the page stuck at 0/0 — load explicitly.
        """
        choices = _get_batch_choices()
        if not choices:
            return (gr.Dropdown(choices=[], value=None), *_EMPTY_VIEW)
        first = choices[0]
        return (gr.Dropdown(choices=choices, value=first), *_load_batch(first))

    with gr.Blocks(title="AMSG Staging Review") as app:
        gr.Markdown("## AMSG 探索批次人工审核")

        with gr.Row():
            batch_dropdown = gr.Dropdown(
                choices=_get_batch_choices(),
                label="选择批次",
                interactive=True,
            )
            refresh_btn = gr.Button("刷新列表")

        progress_label = gr.Markdown("0 / 0")

        with gr.Row():
            prev_btn = gr.Button("◀ 上一条")
            next_btn = gr.Button("下一条 ▶")

        item_id_box = gr.Textbox(label="item_id", interactive=False, visible=False)
        card_text = gr.Markdown(label="候选转换")

        with gr.Row():
            before_img = gr.Image(label="操作前截图", type="filepath")
            after_img = gr.Image(label="操作后截图", type="filepath")

        decision_radio = gr.Radio(
            choices=["approve", "reject"],
            value="approve",
            label="决策",
        )
        save_status = gr.Textbox(label="保存状态", interactive=False)

        with gr.Row():
            vlm_accept_btn = gr.Button("采纳全部VLM判定")
            vlm_run_btn = gr.Button("运行VLM审核")

        with gr.Row():
            confirm_check = gr.Checkbox(label="确认应用到图谱")
            apply_btn = gr.Button("应用到图谱 (apply)")

        apply_status = gr.Textbox(label="应用状态", interactive=False)

        # ── Event bindings ──────────────────────────────────────────

        view_outputs = [card_text, item_id_box, before_img, after_img, decision_radio, progress_label]

        # Load on page open — a single-batch dropdown never fires change.
        app.load(
            fn=_init_load,
            inputs=[],
            outputs=[batch_dropdown, *view_outputs],
        )

        batch_dropdown.change(
            fn=_load_batch,
            inputs=[batch_dropdown],
            outputs=view_outputs,
        )
        # select fires even when the same value is re-picked.
        batch_dropdown.select(
            fn=_load_batch,
            inputs=[batch_dropdown],
            outputs=view_outputs,
        )

        refresh_btn.click(
            fn=_init_load,
            inputs=[],
            outputs=[batch_dropdown, *view_outputs],
        )

        prev_btn.click(
            fn=lambda: _navigate(-1),
            inputs=[],
            outputs=view_outputs,
        )

        next_btn.click(
            fn=lambda: _navigate(1),
            inputs=[],
            outputs=view_outputs,
        )

        # .input fires only on USER interaction. .change also fires when
        # navigation programmatically sets the radio to the next item's
        # pre-filled value, racing with the item_id update — that swapped
        # two items' decisions in a real review session.
        decision_radio.input(
            fn=_save_decision,
            inputs=[item_id_box, decision_radio],
            outputs=[save_status],
        )

        vlm_accept_btn.click(fn=_accept_all_vlm, outputs=[save_status])
        vlm_run_btn.click(fn=_run_vlm, outputs=[save_status])
        apply_btn.click(fn=_apply, inputs=[confirm_check], outputs=[apply_status])

    return app


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="AMSG staging batch review UI")
    parser.add_argument("--port", type=int, default=7861)
    parser.add_argument("--storage", default="memory_db/staging")
    args = parser.parse_args()

    app = _build_app(storage_root=args.storage)
    app.launch(server_port=args.port)


if __name__ == "__main__":
    main()
