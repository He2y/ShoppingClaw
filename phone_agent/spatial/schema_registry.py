"""Schema registry for common mobile and domain-specific spatial graphs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


@dataclass(frozen=True)
class PageTypeSpec:
    name: str
    risk: str = "normal"
    landmarks: tuple[str, ...] = ()
    affordances: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    description: str = ""
    vlm_hint: str = ""
    evidence_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransitionSpec:
    source: str
    target: str
    intent: str
    affordance: str = ""
    target_locator: dict[str, Any] = field(default_factory=dict)
    risk: str = "normal"
    rollback_action: str = "Back"
    exploration_hint: str = ""


@dataclass(frozen=True)
class ExplorationSpec:
    """Schema-level configuration for the offline exploration pipeline."""

    coverage_page_types: tuple[str, ...] = ()
    coverage_transitions: tuple[tuple[str, str], ...] = ()
    unsafe_tokens: tuple[str, ...] = ()
    risky_cta_tokens: tuple[str, ...] = ()
    risky_cta_allowed_pages: tuple[str, ...] = ()
    self_loop_input_pages: tuple[str, ...] = ()
    interference_page_types: tuple[str, ...] = ()
    trap_tokens: tuple[str, ...] = ()
    default_task: str = ""
    vlm_verify_transitions: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class MobileSchema:
    name: str
    extends: str = ""
    page_types: dict[str, PageTypeSpec] = field(default_factory=dict)
    transitions: tuple[TransitionSpec, ...] = ()
    app_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    intents: dict[str, dict[str, Any]] = field(default_factory=dict)
    exploration: ExplorationSpec = field(default_factory=ExplorationSpec)

    def page_spec(self, page_type: str) -> PageTypeSpec | None:
        return self.page_types.get(page_type)

    def outgoing(self, page_type: str) -> tuple[TransitionSpec, ...]:
        return tuple(item for item in self.transitions if item.source == page_type)

    def normalize_page_type(self, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized in self.page_types:
            return normalized
        for page_type, spec in self.page_types.items():
            aliases = {alias.lower() for alias in spec.aliases}
            if normalized in aliases:
                return page_type
        return normalized or "unknown"

    def page_type_matches(self, expected: str, observed: str) -> bool:
        expected = (expected or "").strip().lower()
        observed = (observed or "").strip().lower()
        if not expected or not observed:
            return False
        if expected == observed:
            return True
        expected_spec = self.page_spec(expected)
        observed_spec = self.page_spec(observed)
        if expected_spec and observed in {alias.lower() for alias in expected_spec.aliases}:
            return True
        if observed_spec and expected in {alias.lower() for alias in observed_spec.aliases}:
            return True
        return False

    def page_type_covered(self, expected: str, observed_page_types: set[str] | list[str] | tuple[str, ...]) -> bool:
        return any(self.page_type_matches(expected, observed) for observed in observed_page_types)


def _dedupe_ordered(seq: tuple[str, ...]) -> tuple[str, ...]:
    """Deduplicate preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


class SchemaRegistry:
    """Loads and merges AMSG schemas.

    The ``*.yaml`` files intentionally contain JSON-compatible YAML so this
    module does not need a PyYAML dependency.
    """

    def __init__(self, schema_dir: str | Path | None = None):
        self.schema_dir = Path(schema_dir) if schema_dir else SCHEMA_DIR
        self._cache: dict[str, MobileSchema] = {}

    def load(self, name: str) -> MobileSchema:
        key = name.replace(".yaml", "")
        if key in self._cache:
            return self._cache[key]
        raw = self._read_schema_file(key)
        schema = self._schema_from_raw(raw)
        self._cache[key] = schema
        return schema

    def merged(self, name: str) -> MobileSchema:
        schema = self.load(name)
        if not schema.extends:
            return schema
        base = self.merged(schema.extends)
        page_types = dict(base.page_types)
        page_types.update(schema.page_types)
        app_aliases = dict(base.app_aliases)
        app_aliases.update(schema.app_aliases)
        intents = dict(base.intents)
        intents.update(schema.intents)
        transitions = (*base.transitions, *schema.transitions)

        # Merge ExplorationSpec: token lists concatenate (dedupe); other fields child-replaces
        base_exp = base.exploration
        child_exp = schema.exploration
        merged_exp = ExplorationSpec(
            coverage_page_types=(
                child_exp.coverage_page_types if child_exp.coverage_page_types else base_exp.coverage_page_types
            ),
            coverage_transitions=(
                child_exp.coverage_transitions if child_exp.coverage_transitions else base_exp.coverage_transitions
            ),
            unsafe_tokens=_dedupe_ordered(base_exp.unsafe_tokens + child_exp.unsafe_tokens),
            risky_cta_tokens=_dedupe_ordered(base_exp.risky_cta_tokens + child_exp.risky_cta_tokens),
            risky_cta_allowed_pages=(
                child_exp.risky_cta_allowed_pages if child_exp.risky_cta_allowed_pages else base_exp.risky_cta_allowed_pages
            ),
            self_loop_input_pages=(
                child_exp.self_loop_input_pages if child_exp.self_loop_input_pages else base_exp.self_loop_input_pages
            ),
            interference_page_types=(
                child_exp.interference_page_types if child_exp.interference_page_types else base_exp.interference_page_types
            ),
            trap_tokens=_dedupe_ordered(base_exp.trap_tokens + child_exp.trap_tokens),
            default_task=child_exp.default_task if child_exp.default_task else base_exp.default_task,
            # child-replaces: schema-specific vlm_verify_transitions override the base
            vlm_verify_transitions=(
                child_exp.vlm_verify_transitions if child_exp.vlm_verify_transitions else base_exp.vlm_verify_transitions
            ),
        )
        return MobileSchema(
            name=schema.name,
            extends=schema.extends,
            page_types=page_types,
            transitions=transitions,
            app_aliases=app_aliases,
            intents=intents,
            exploration=merged_exp,
        )

    def normalize_app(self, app: str, schema_name: str = "shopping") -> str:
        value = (app or "").strip()
        if not value:
            return ""
        # AppRegistry is the single source of truth for app identity.
        from phone_agent.spatial.app_registry import get_default_app_registry

        record = get_default_app_registry().resolve(value)
        if record:
            return record.app_id
        # Fallback: legacy schema-level aliases (kept for custom schema dirs).
        lowered = value.lower()
        for schema in self._all_loaded_and_default(schema_name):
            for canonical, aliases in schema.app_aliases.items():
                alias_set = {canonical.lower(), *(alias.lower() for alias in aliases)}
                if lowered in alias_set:
                    return canonical
        return value

    def app_matches(self, app: str, app_filter: str | None, schema_name: str = "shopping") -> bool:
        if not app_filter:
            return True
        return self.normalize_app(app, schema_name).lower() == self.normalize_app(app_filter, schema_name).lower()

    def list_schemas(self) -> tuple[str, ...]:
        """Return names of all available domain schemas (excluding app_registry and apps/ subdirectory)."""
        excludes = {"app_registry"}
        names: list[str] = []
        for p in self.schema_dir.glob("*.yaml"):
            stem = p.stem
            if stem not in excludes:
                names.append(stem)
        return tuple(sorted(names))

    def _all_loaded_and_default(self, schema_name: str) -> tuple[MobileSchema, ...]:
        schemas = [self.merged(schema_name)]
        if schema_name != "common_mobile":
            schemas.append(self.merged("common_mobile"))
        return tuple(schemas)

    def _read_schema_file(self, name: str) -> dict[str, Any]:
        path = self.schema_dir / f"{name}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"AMSG schema not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _schema_from_raw(raw: dict[str, Any]) -> MobileSchema:
        page_types: dict[str, PageTypeSpec] = {}
        for name, item in (raw.get("page_types") or {}).items():
            item = item or {}
            page_types[name] = PageTypeSpec(
                name=name,
                risk=str(item.get("risk") or "normal"),
                landmarks=tuple(item.get("landmarks") or ()),
                affordances=tuple(item.get("affordances") or ()),
                aliases=tuple(item.get("aliases") or ()),
                description=str(item.get("description") or ""),
                vlm_hint=str(item.get("vlm_hint") or ""),
                evidence_tokens=tuple(item.get("evidence_tokens") or ()),
            )
        transitions = tuple(
            TransitionSpec(
                source=str(item.get("source") or ""),
                target=str(item.get("target") or ""),
                intent=str(item.get("intent") or ""),
                affordance=str(item.get("affordance") or ""),
                target_locator=dict(item.get("target_locator") or {}),
                risk=str(item.get("risk") or "normal"),
                rollback_action=str(item.get("rollback_action") or "Back"),
                exploration_hint=str(item.get("exploration_hint") or ""),
            )
            for item in (raw.get("transitions") or ())
        )
        aliases = {
            str(key): tuple(str(alias) for alias in value)
            for key, value in (raw.get("app_aliases") or {}).items()
        }
        exploration = SchemaRegistry._exploration_from_raw(raw.get("exploration") or {})
        return MobileSchema(
            name=str(raw.get("name") or "unknown"),
            extends=str(raw.get("extends") or ""),
            page_types=page_types,
            transitions=transitions,
            app_aliases=aliases,
            intents=dict(raw.get("intents") or {}),
            exploration=exploration,
        )

    @staticmethod
    def _exploration_from_raw(raw: dict[str, Any]) -> ExplorationSpec:
        raw_transitions = raw.get("coverage_transitions") or ()
        coverage_transitions = tuple(
            (str(item[0]), str(item[1]))
            for item in raw_transitions
            if isinstance(item, (list, tuple)) and len(item) >= 2
        )
        raw_vlm_verify = raw.get("vlm_verify_transitions") or ()
        vlm_verify_transitions = tuple(
            (str(item[0]), str(item[1]))
            for item in raw_vlm_verify
            if isinstance(item, (list, tuple)) and len(item) >= 2
        )
        return ExplorationSpec(
            coverage_page_types=tuple(str(s) for s in (raw.get("coverage_page_types") or ())),
            coverage_transitions=coverage_transitions,
            unsafe_tokens=tuple(str(s) for s in (raw.get("unsafe_tokens") or ())),
            risky_cta_tokens=tuple(str(s) for s in (raw.get("risky_cta_tokens") or ())),
            risky_cta_allowed_pages=tuple(str(s) for s in (raw.get("risky_cta_allowed_pages") or ())),
            self_loop_input_pages=tuple(str(s) for s in (raw.get("self_loop_input_pages") or ())),
            interference_page_types=tuple(str(s) for s in (raw.get("interference_page_types") or ())),
            trap_tokens=tuple(str(s) for s in (raw.get("trap_tokens") or ())),
            default_task=str(raw.get("default_task") or ""),
            vlm_verify_transitions=vlm_verify_transitions,
        )


_DEFAULT_REGISTRY: SchemaRegistry | None = None


def get_default_registry() -> SchemaRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = SchemaRegistry()
    return _DEFAULT_REGISTRY


# Legacy constant kept as fallback so behavior never silently changes.
_LEGACY_VLM_VERIFY_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("search_result", "product_detail"),
    ("product_detail", "spec_selection"),
})


def vlm_verify_transitions_for(app_or_schema: str) -> frozenset[tuple[str, str]]:
    """Return the VLM-verify transition pairs for an app or schema name.

    Reads the ``vlm_verify_transitions`` field from the merged schema.
    Falls back to ``_LEGACY_VLM_VERIFY_TRANSITIONS`` if the schema lookup fails
    so that existing shopping behavior is never silently changed.
    """
    try:
        registry = get_default_registry()
        # Try as a schema name first; if not found try as an app's schema.
        schema_name = app_or_schema
        try:
            schema = registry.merged(schema_name)
        except (FileNotFoundError, Exception):
            # Try resolving as an app id → schema name.
            from phone_agent.spatial.app_registry import get_default_app_registry
            schema_name = get_default_app_registry().schema_for(app_or_schema)
            if not schema_name:
                return _LEGACY_VLM_VERIFY_TRANSITIONS
            schema = registry.merged(schema_name)

        pairs = schema.exploration.vlm_verify_transitions
        if pairs:
            return frozenset(pairs)
        # Empty in schema → fall back to legacy constant.
        return _LEGACY_VLM_VERIFY_TRANSITIONS
    except Exception:
        return _LEGACY_VLM_VERIFY_TRANSITIONS
