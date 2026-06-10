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


@dataclass(frozen=True)
class TransitionSpec:
    source: str
    target: str
    intent: str
    affordance: str = ""
    target_locator: dict[str, Any] = field(default_factory=dict)
    risk: str = "normal"
    rollback_action: str = "Back"


@dataclass(frozen=True)
class MobileSchema:
    name: str
    extends: str = ""
    page_types: dict[str, PageTypeSpec] = field(default_factory=dict)
    transitions: tuple[TransitionSpec, ...] = ()
    app_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    intents: dict[str, dict[str, Any]] = field(default_factory=dict)

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
        return MobileSchema(
            name=schema.name,
            extends=schema.extends,
            page_types=page_types,
            transitions=transitions,
            app_aliases=app_aliases,
            intents=intents,
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
            )
            for item in (raw.get("transitions") or ())
        )
        aliases = {
            str(key): tuple(str(alias) for alias in value)
            for key, value in (raw.get("app_aliases") or {}).items()
        }
        return MobileSchema(
            name=str(raw.get("name") or "unknown"),
            extends=str(raw.get("extends") or ""),
            page_types=page_types,
            transitions=transitions,
            app_aliases=aliases,
            intents=dict(raw.get("intents") or {}),
        )


_DEFAULT_REGISTRY: SchemaRegistry | None = None


def get_default_registry() -> SchemaRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = SchemaRegistry()
    return _DEFAULT_REGISTRY
