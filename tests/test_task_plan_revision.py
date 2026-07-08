"""TaskPlan.apply_revision: atomic milestone revisions."""

from phone_agent.task_plan import TaskPlan


def _plan() -> TaskPlan:
    return TaskPlan.from_vlm_output("买蓝牙耳机", {
        "steps": [
            {"description": "搜索蓝牙耳机", "target_page": "search_result"},
            {"description": "设置价格筛选", "target_page": "filter_panel"},
            {"description": "选择商品加购", "target_page": "spec_selection"},
        ],
        "search_query": "蓝牙耳机",
    })


def test_from_vlm_output_assigns_step_ids():
    plan = _plan()
    assert [s.step_id for s in plan.steps] == [1, 2, 3]
    assert plan.steps[0].status == "current"


def test_revision_marks_done_and_replaces_pending():
    plan = _plan()
    summary = plan.apply_revision(
        completed_step_ids=[1],
        revised_subtasks=[
            {"description": "重新打开筛选面板并填写最低最高价", "target_page": "filter_panel"},
            {"description": "确认筛选后选择商品", "target_page": "product_detail"},
        ],
    )

    assert plan.steps[0].status == "done"
    assert [s.description for s in plan.steps[1:]] == [
        "重新打开筛选面板并填写最低最高价", "确认筛选后选择商品",
    ]
    assert [s.step_id for s in plan.steps] == [1, 4, 5]   # new ids monotonic
    assert plan.current_step().description.startswith("重新打开筛选面板")
    assert plan.steps[1].status == "current"
    assert "done:+1" in summary and "revised:2条" in summary


def test_empty_revision_keeps_pending_tail():
    plan = _plan()
    plan.apply_revision(completed_step_ids=[1], revised_subtasks=[])
    assert [s.description for s in plan.steps] == [
        "搜索蓝牙耳机", "设置价格筛选", "选择商品加购",
    ]
    assert plan.steps[0].status == "done"
    assert plan.current_step().description == "设置价格筛选"


def test_invalid_revision_rejected_keeps_tail():
    plan = _plan()
    summary = plan.apply_revision(
        completed_step_ids=[],
        revised_subtasks=[{"description": "   "}, "not a dict"],
    )
    assert len(plan.steps) == 3                            # tail preserved
    assert "rejected" in summary
    assert plan.current_step().description == "搜索蓝牙耳机"


def test_done_steps_never_rewritten():
    plan = _plan()
    plan.steps[0].status = "done"
    plan.apply_revision(
        completed_step_ids=[],
        revised_subtasks=[{"description": "全新计划", "target_page": "home"}],
    )
    assert plan.steps[0].description == "搜索蓝牙耳机"
    assert plan.steps[0].status == "done"
    assert plan.steps[1].description == "全新计划"


def test_revision_caps_total_steps():
    plan = _plan()
    plan.apply_revision(
        completed_step_ids=[],
        revised_subtasks=[{"description": f"步骤{i}"} for i in range(20)],
    )
    assert len(plan.steps) <= TaskPlan.MAX_STEPS
