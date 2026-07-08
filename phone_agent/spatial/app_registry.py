"""Canonical app identity registry for the AMSG spatial graph.

Single source of truth replacing the alias dicts that were previously
scattered across ``spatial_graph_memory`` (_SHOPPING_APPS/_APP_ALIASES),
``graph_store._runtime_app_aliases`` and ``schema_registry.normalize_app``.

Every app the system knows about has one canonical ``app_id`` (ASCII
snake_case), a ``domain`` that maps to a structural schema, and the set of
aliases under which it may appear: display names, package names, and the
mojibake legacy variants found in old exploration fixtures.

The registry file is JSON-compatible YAML (parsed with ``json.loads``),
matching the convention of the domain schemas in this directory.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parent / "schemas" / "app_registry.yaml"

_UNKNOWN_DOMAIN = "unknown"
_FALLBACK_SCHEMA = "common_mobile"


@dataclass(frozen=True)
class AppRecord:
    """Identity record for a single app."""

    app_id: str
    domain: str
    schema: str
    package_names: tuple[str, ...] = ()
    display_names: tuple[str, ...] = ()
    legacy_aliases: tuple[str, ...] = ()

    @property
    def all_aliases(self) -> tuple[str, ...]:
        """Every string this app may appear as, canonical id included."""
        return (
            self.app_id,
            *self.display_names,
            *self.package_names,
            *self.legacy_aliases,
        )


def _slugify(value: str) -> str:
    """ASCII snake_case fallback id for apps not present in the registry."""
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_part = re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")
    if ascii_part:
        return ascii_part
    digest = hashlib.md5((value or "").encode("utf-8")).hexdigest()[:8]
    return f"app_{digest}"


class AppRegistry:
    """Resolves raw app strings (display name / package / alias) to records."""

    def __init__(self, registry_path: str | Path | None = None):
        self.registry_path = Path(registry_path) if registry_path else REGISTRY_PATH
        self._records: dict[str, AppRecord] = {}
        self._alias_index: dict[str, str] = {}
        self._domain_schemas: dict[str, str] = {}
        self._mention_tokens: tuple[str, ...] = ()
        self._load()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        raw = self._read_registry_file()
        domains = raw.get("domains") or {}
        self._domain_schemas = {
            str(name): str((spec or {}).get("schema") or _FALLBACK_SCHEMA)
            for name, spec in domains.items()
        }
        for app_id, spec in (raw.get("apps") or {}).items():
            spec = spec or {}
            domain = str(spec.get("domain") or _UNKNOWN_DOMAIN)
            record = AppRecord(
                app_id=str(app_id),
                domain=domain,
                schema=str(spec.get("schema") or self._domain_schemas.get(domain, _FALLBACK_SCHEMA)),
                package_names=tuple(str(item) for item in (spec.get("package_names") or ())),
                display_names=tuple(str(item) for item in (spec.get("display_names") or ())),
                legacy_aliases=tuple(str(item) for item in (spec.get("legacy_aliases") or ())),
            )
            self._index_record(record)
        self._rebuild_mention_tokens()

    def _read_registry_file(self) -> dict:
        if not self.registry_path.exists():
            return {}
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _index_record(self, record: AppRecord) -> None:
        self._records[record.app_id] = record
        for alias in record.all_aliases:
            key = alias.strip().lower()
            if key:
                self._alias_index[key] = record.app_id

    def _rebuild_mention_tokens(self) -> None:
        tokens = {
            alias
            for record in self._records.values()
            for alias in record.all_aliases
            if alias and not alias.startswith("com.") and not alias.startswith("me.")
        }
        self._mention_tokens = tuple(sorted(tokens, key=len, reverse=True))

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, raw: str) -> AppRecord | None:
        """Resolve any alias / package / display name to its AppRecord."""
        key = (raw or "").strip().lower()
        if not key:
            return None
        app_id = self._alias_index.get(key)
        return self._records.get(app_id) if app_id else None

    def canonical_id(self, raw: str) -> str:
        """Canonical app id; slugified fallback for unregistered apps."""
        value = (raw or "").strip()
        if not value:
            return ""
        record = self.resolve(value)
        return record.app_id if record else _slugify(value)

    def display_name(self, raw: str) -> str:
        """Primary human-readable name (e.g. 京东), used for app_raw alignment.

        Graph nodes store app=canonical_id for queries and app_raw=display
        name for human browsing - mixed raw values (jd vs 京东) made Neo4j
        Browser filtering inconsistent.
        """
        record = self.resolve(raw)
        if record and record.display_names:
            return record.display_names[0]
        return (raw or "").strip()

    def domain_of(self, raw: str) -> str:
        record = self.resolve(raw)
        return record.domain if record else _UNKNOWN_DOMAIN

    def schema_for(self, raw: str) -> str:
        record = self.resolve(raw)
        if record:
            return record.schema
        return self._domain_schemas.get(_UNKNOWN_DOMAIN, _FALLBACK_SCHEMA)

    def storage_aliases(self, raw: str) -> tuple[str, ...]:
        """All values this app may be stored as in Neo4j (for IN clauses).

        Always includes the raw value itself so unregistered apps still match
        their own nodes.
        """
        value = (raw or "").strip()
        if not value:
            return ()
        aliases = {value}
        record = self.resolve(value)
        if record:
            aliases.update(record.all_aliases)
        return tuple(sorted(aliases))

    def aliases_of(self, raw: str) -> frozenset[str]:
        """Lowercased alias set used for app-consistency checks."""
        record = self.resolve(raw)
        if record:
            return frozenset(alias.lower() for alias in record.all_aliases)
        value = (raw or "").strip().lower()
        return frozenset({value}) if value else frozenset()

    # ------------------------------------------------------------------
    # Text inference (legacy `_infer_app` behavior)
    # ------------------------------------------------------------------

    def mention_tokens(self) -> tuple[str, ...]:
        """Known app tokens, longest first, suitable for substring scans."""
        return self._mention_tokens

    def infer_from_text(self, text: str) -> str:
        """Return the first known app token mentioned in ``text``.

        Returns the token as it appears (e.g. ``"淘宝"``), not the canonical
        id — page-state construction keeps the raw value; canonicalization
        happens at the storage boundary.
        """
        if not text:
            return ""
        for token in self._mention_tokens:
            if token in text:
                return token
        return ""

    def infer_domain_from_text(self, text: str) -> str:
        token = self.infer_from_text(text)
        return self.domain_of(token) if token else ""

    # ------------------------------------------------------------------
    # Registration (used by app onboarding)
    # ------------------------------------------------------------------

    def register(self, record: AppRecord) -> None:
        """Register a record in memory (does not persist to disk)."""
        self._index_record(record)
        self._rebuild_mention_tokens()

    def save(self) -> Path:
        """Persist the current records back to the registry file."""
        payload = {
            "_comment": (
                "Single source of truth for app identity. JSON-compatible YAML "
                "(parsed with json.loads) - do NOT use YAML-only syntax."
            ),
            "version": 1,
            "domains": {
                name: {"schema": schema} for name, schema in sorted(self._domain_schemas.items())
            },
            "apps": {
                record.app_id: {
                    "domain": record.domain,
                    "display_names": list(record.display_names),
                    "package_names": list(record.package_names),
                    "legacy_aliases": list(record.legacy_aliases),
                }
                for record in sorted(self._records.values(), key=lambda item: item.app_id)
            },
        }
        self.registry_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return self.registry_path


_DEFAULT_APP_REGISTRY: AppRegistry | None = None


def get_default_app_registry() -> AppRegistry:
    global _DEFAULT_APP_REGISTRY
    if _DEFAULT_APP_REGISTRY is None:
        _DEFAULT_APP_REGISTRY = AppRegistry()
    return _DEFAULT_APP_REGISTRY
