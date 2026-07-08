"""Compatibility shim - the explorer now lives in ``phone_agent.memory.exploration``.

Legacy imports (`from phone_agent.memory.offline_explorer import OfflineExplorer`)
keep working. ``load_dotenv`` stays importable here for old monkeypatch targets;
new code should patch ``phone_agent.memory.exploration.classifier.load_dotenv``.
"""

from dotenv import load_dotenv  # noqa: F401  (legacy monkeypatch target)

from phone_agent.memory.exploration.classifier import (  # noqa: F401
    PageClassifier,
    _ClassifierProvider,
    _CROP_BOTTOM_RATIO,
    _CROP_TOP_RATIO,
)
from phone_agent.memory.exploration.classifier_prompts import (  # noqa: F401
    _CLASSIFIER_FAST_SYSTEM_PROMPT,
    _CLASSIFIER_SYSTEM_PROMPT,
)
from phone_agent.memory.exploration.cli import main  # noqa: F401
from phone_agent.memory.exploration.explorer import (  # noqa: F401
    OfflineExplorer,
    _build_taobao_task,
)
from phone_agent.memory.exploration.prompts import _build_exploration_system_prompt  # noqa: F401
from phone_agent.memory.exploration.types import (  # noqa: F401
    CoverageReport,
    CoverageTarget,
    ExplorationStep,
    PageInfo,
    ShoppingPageType,
    Trajectory,
    _HIGH_RISK_PAGE_TYPES,
    _PAGE_TYPE_MAP,
    _PAGE_TYPE_SUMMARY,
    _SCREEN_CHANGE_HASH_LEN,
    _SPEC_TRIGGER_TOKENS,
    _UNSAFE_ACTION_TOKENS,
)

if __name__ == "__main__":
    raise SystemExit(main())
