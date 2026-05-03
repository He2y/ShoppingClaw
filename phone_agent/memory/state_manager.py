"""
State Manager - Unified state tracking for PhoneAgent.

Centralizes all state management logic that was previously split between
agent.py and MemoryManager, ensuring consistent state tracking across
the memory system.
"""

from typing import Optional


class StateManager:
    """统��的状���追踪管理器

    负责管理 UI 状态���生命周期��包括：
    - 当���状态和前��状态���追踪
    - ��务开始和结��状态���记录
    - ��态历史��维护
    """

    def __init__(self):
        self._current_state_id: Optional[str] = None
        self._prev_state_id: Optional[str] = None
        self._task_start_state_id: Optional[str] = None
        self._task_end_state_id: Optional[str] = None
        self._state_history: list[str] = []

    def compute_state_id(self, screenshot_hash: str, semantic_layout: str) -> str:
        """计算��定的���态标识

        Args:
            screenshot_hash: 截图的��希值
            semantic_layout: 语义布局��述 (app + page + key_elements)

        Returns:
            状态标识��

        Note:
            Phase 2: 使用 semantic_layout + screenshot_hash 前缀
            Phase 3: ���级为 View Hierarchy Hash (Android uiautomator)
        """
        # 使用语��布局���为主要标识��截图���希作为辅��
        # 这��相同���面即使截图��有不���也能匹配
        return f"state_{semantic_layout}_{screenshot_hash[:8]}"

    def update_state(self, new_state_id: str) -> tuple[Optional[str], str]:
        """更新状��，返回 (prev_state_id, current_state_id)

        Args:
            new_state_id: 新��状态���识

        Returns:
            (前��状态ID, 当��状态ID) 元组
        """
        self._prev_state_id = self._current_state_id
        self._current_state_id = new_state_id
        self._state_history.append(new_state_id)
        return self._prev_state_id, self._current_state_id

    def start_task(self, initial_state_id: str):
        """任务���始时��置初���状态

        Args:
            initial_state_id: 任务开��时的���态ID
        """
        self._task_start_state_id = initial_state_id
        self._current_state_id = initial_state_id
        self._prev_state_id = None
        self._state_history = [initial_state_id]

    def end_task(self, final_state_id: str):
        """任���结束时记��最终���态

        Args:
            final_state_id: 任务���束时��状态ID
        """
        self._task_end_state_id = final_state_id

    def get_current_state(self) -> Optional[str]:
        """获取���前状态ID"""
        return self._current_state_id

    def get_prev_state(self) -> Optional[str]:
        """获取��一状���ID"""
        return self._prev_state_id

    def get_task_states(self) -> tuple[Optional[str], Optional[str]]:
        """��回 (start_state_id, end_state_id)"""
        return self._task_start_state_id, self._task_end_state_id

    def get_state_history(self) -> list[str]:
        """获取状态��史列表"""
        return self._state_history.copy()

    def reset(self):
        """���置所有状��（用于��任务���"""
        self._current_state_id = None
        self._prev_state_id = None
        self._task_start_state_id = None
        self._task_end_state_id = None
        self._state_history = []
