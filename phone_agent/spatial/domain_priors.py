"""Domain structural priors for cold-start graph navigation.

Provides TEXT-ONLY hints about typical page-type transitions within a domain,
derived from:
  1. Schema-level transitions (schema_registry → merged schema → outgoing transitions)
  2. Cross-app graph aggregation (same domain, non-own apps, promoted edges)

Hard invariant: priors are NEVER converted to executable next_actions or
coordinates.  They are appended to ``graph_hint`` as plain Chinese text
so the VLM can orient itself on unfamiliar apps.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

_DOMAIN_PRIORS_ENABLED = os.getenv("AMSG_DOMAIN_PRIORS", "1") not in ("0", "false", "False")


@dataclass(frozen=True)
class StructuralPrior:
    """A single structural-prior transition hint."""

    source_page_type: str
    intent: str
    target_page_type: str
    origin: str  # "schema" | "cross_app"
    support: int = 0  # number of observations (cross_app only)
    apps: tuple[str, ...] = ()


@dataclass
class _CoverageCache:
    total: int
    expires_at: float


class DomainPriorProvider:
    """Aggregates structural priors for an app from schema and cross-app data.

    Parameters
    ----------
    graph_store:
        Optional live GraphStore.  When ``driver`` is None the cross-app
        query is silently skipped.
    schema_registry:
        Optional SchemaRegistry override; defaults to the global singleton.
    app_registry:
        Optional AppRegistry override; defaults to the global singleton.
    cache_ttl_s:
        How long to cache coverage counts before re-querying (seconds).
    """

    def __init__(
        self,
        graph_store: Any = None,
        schema_registry: Any = None,
        app_registry: Any = None,
        cache_ttl_s: float = 300.0,
    ) -> None:
        self._graph_store = graph_store
        self._schema_registry = schema_registry
        self._app_registry = app_registry
        self._cache_ttl_s = cache_ttl_s
        self._coverage_cache: dict[str, _CoverageCache] = {}

    # ------------------------------------------------------------------
    # Lazy singletons
    # ------------------------------------------------------------------

    def _get_schema_registry(self) -> Any:
        if self._schema_registry is not None:
            return self._schema_registry
        from phone_agent.spatial.schema_registry import get_default_registry
        return get_default_registry()

    def _get_app_registry(self) -> Any:
        if self._app_registry is not None:
            return self._app_registry
        from phone_agent.spatial.app_registry import get_default_app_registry
        return get_default_app_registry()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def priors_for(
        self,
        app: str,
        page_type: str,
        goal_page_types: tuple[str, ...] = (),
    ) -> tuple[StructuralPrior, ...]:
        """Return structural priors for *app* at *page_type*.

        Schema priors are always included when the schema has outgoing
        transitions from *page_type*.  Cross-app priors are added when the
        graph store is connected.

        Goal-directed priors (transitions toward *goal_page_types*) are
        included in addition to direct outgoing transitions.
        """
        if not _DOMAIN_PRIORS_ENABLED:
            return ()
        try:
            schema_priors = self._schema_priors(app, page_type, goal_page_types)
            cross_app_priors = self._cross_app_priors(app, page_type)
            # Deduplicate: schema priors take precedence
            seen: set[tuple[str, str, str]] = set()
            merged: list[StructuralPrior] = []
            for p in (*schema_priors, *cross_app_priors):
                key = (p.source_page_type, p.intent, p.target_page_type)
                if key not in seen:
                    seen.add(key)
                    merged.append(p)
            return tuple(merged)
        except Exception:
            return ()

    def format_hint(
        self,
        priors: tuple[StructuralPrior, ...],
        app: str,
        page_type: str,
    ) -> str:
        """Format priors as a Chinese text block for ``graph_hint``.

        Returns "" when *priors* is empty.
        """
        if not priors:
            return ""
        schema_parts: list[str] = []
        cross_parts: list[str] = []
        for p in priors:
            if p.origin == "schema":
                schema_parts.append(f"  {p.intent}→{p.target_page_type} (领域模式)")
            else:
                apps_note = f"({p.support}个应用验证过)" if p.support else ""
                cross_parts.append(f"  {p.intent}→{p.target_page_type} {apps_note}".rstrip())

        lines: list[str] = [
            f"[同域结构先验] 当前页面类型 {page_type} 在同域应用中常见的转移:"
        ]
        if cross_parts:
            lines.extend(cross_parts)
        if schema_parts:
            lines.extend(schema_parts)
        lines.append("这些仅是方向提示, 具体元素请从当前截图判断。")
        return "\n".join(lines)

    def coverage_is_cold(self, app: str, threshold: int = 3) -> bool:
        """Return True when the app subgraph node count is below *threshold*.

        Result is cached per app for ``cache_ttl_s`` seconds.
        """
        if not _DOMAIN_PRIORS_ENABLED:
            return False
        now = time.monotonic()
        entry = self._coverage_cache.get(app)
        if entry is None or now >= entry.expires_at:
            total = self._get_coverage_total(app)
            self._coverage_cache[app] = _CoverageCache(
                total=total, expires_at=now + self._cache_ttl_s
            )
            entry = self._coverage_cache[app]
        return entry.total < threshold

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _schema_priors(
        self,
        app: str,
        page_type: str,
        goal_page_types: tuple[str, ...],
    ) -> list[StructuralPrior]:
        """Derive priors from the app's merged schema."""
        priors: list[StructuralPrior] = []
        try:
            registry = self._get_schema_registry()
            app_registry = self._get_app_registry()
            schema_name = app_registry.schema_for(app)
            if not schema_name:
                schema_name = "common_mobile"
            schema = registry.merged(schema_name)
            # Direct outgoing transitions from current page_type
            for spec in schema.outgoing(page_type):
                priors.append(
                    StructuralPrior(
                        source_page_type=spec.source,
                        intent=spec.intent,
                        target_page_type=spec.target,
                        origin="schema",
                    )
                )
            # Goal-directed: transitions toward any goal page_type
            if goal_page_types:
                goal_set = set(goal_page_types)
                for spec in schema.transitions:
                    if spec.target in goal_set and spec.source != page_type:
                        priors.append(
                            StructuralPrior(
                                source_page_type=spec.source,
                                intent=spec.intent,
                                target_page_type=spec.target,
                                origin="schema",
                            )
                        )
        except Exception:
            pass
        return priors

    def _cross_app_priors(self, app: str, page_type: str) -> list[StructuralPrior]:
        """Query promoted edges from other apps in the same domain."""
        gs = self._graph_store
        if gs is None or not getattr(gs, "driver", None):
            return []
        return self._query_cross_app(app, page_type)

    def _query_cross_app(self, app: str, page_type: str) -> list[StructuralPrior]:
        """Execute the cross-app aggregation Cypher query."""
        try:
            app_registry = self._get_app_registry()
            domain = app_registry.domain_of(app)
            if not domain or domain == "unknown":
                return []
            own_aliases = list(app_registry.storage_aliases(app))
            gs = self._graph_store
            database = getattr(gs, "database", None) or "neo4j"
            with gs.driver.session(database=database) as session:
                result = session.run(
                    """
                    MATCH (s:UIState)-[:NEXT_ACTION]->(a:Action)-[:PRODUCES]->(t:UIState)
                    WHERE s.domain = $domain
                      AND NOT s.app IN $own_aliases
                      AND s.page_type = $page_type
                      AND coalesce(a.lifecycle_stage,'') = 'promoted'
                      AND coalesce(a.dirty_legacy, false) <> true
                    RETURN s.page_type AS src, a.intent AS intent, t.page_type AS tgt,
                           count(*) AS support,
                           collect(DISTINCT s.app)[..5] AS apps
                    ORDER BY support DESC
                    LIMIT 8
                    """,
                    domain=domain,
                    own_aliases=own_aliases,
                    page_type=page_type,
                )
                priors: list[StructuralPrior] = []
                for rec in result:
                    r = dict(rec.items()) if hasattr(rec, "items") else dict(rec)
                    src = str(r.get("src") or page_type)
                    intent = str(r.get("intent") or "")
                    tgt = str(r.get("tgt") or "")
                    support = int(r.get("support") or 0)
                    raw_apps = r.get("apps") or []
                    apps = tuple(str(a) for a in raw_apps)
                    if intent and tgt:
                        priors.append(
                            StructuralPrior(
                                source_page_type=src,
                                intent=intent,
                                target_page_type=tgt,
                                origin="cross_app",
                                support=support,
                                apps=apps,
                            )
                        )
                return priors
        except Exception:
            return []

    def _get_coverage_total(self, app: str) -> int:
        """Get total node count for the app subgraph."""
        gs = self._graph_store
        if gs is None or not getattr(gs, "driver", None):
            return 0
        try:
            coverage = gs.get_page_type_coverage(app)
            return sum(coverage.values())
        except Exception:
            return 0
