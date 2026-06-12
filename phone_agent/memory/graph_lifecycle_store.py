"""Neo4j persistence layer for AMSG edge lifecycle metadata.

Separated from graph_store.py to keep lifecycle-specific Cypher
isolated. Reads/writes lifecycle_stage, outcome_distribution, and
temporal fields on Action nodes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class GraphLifecycleStore:
    """Lifecycle-specific Neo4j operations for AMSG edge verification."""

    def __init__(self, driver: Any, database: str) -> None:
        self.driver = driver
        self.database = database

    def persist_lifecycle_batch(self, batch: list[dict[str, Any]]) -> int:
        """Bulk-update lifecycle fields on existing Action nodes.

        Matches Action nodes by their page-type-level semantic identity
        (source page type, action intent/type, semantic target, target page
        type) — the fields the in-memory lifecycle record and the persisted
        Action node reliably share. (The previous action_id match re-derived
        the id with a different hash than the write path and matched 0 nodes,
        silently dropping all cumulative verification statistics.) All Action
        instances of the same semantic transition share one lifecycle stage,
        so updating every match is correct. Non-existent transitions are
        silently skipped (MATCH, not MERGE).
        """
        if not self.driver or not batch:
            return 0

        query = """
        UNWIND $batch AS row
        MATCH (s:UIState)-[:NEXT_ACTION]->(a:Action)-[:PRODUCES]->(t:UIState)
        WHERE s.page_type = row.source_page_type
          AND coalesce(a.type, '') = row.intent
          AND coalesce(a.semantic_target, '') = row.action_target
          AND t.page_type = row.target_page_type
        SET a.lifecycle_stage = row.lifecycle_stage,
            a.verification_count = row.verification_count,
            a.dominance_ratio = row.dominance_ratio,
            a.outcome_distribution_json = row.outcome_distribution_json,
            a.outcome_entropy = row.outcome_entropy,
            a.created_at = coalesce(a.created_at, row.created_at),
            a.last_traversed = row.last_traversed
        RETURN count(a) AS updated
        """
        try:
            with self.driver.session(database=self.database) as session:
                result = session.run(query, batch=batch)
                record = result.single()
                count = record["updated"] if record else 0
                logger.info("lifecycle persist: %d Action nodes updated", count)
                return count
        except Exception as exc:
            logger.warning("lifecycle persist failed: %s", exc)
            return 0

    def load_lifecycle_records(self, app: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Load lifecycle data from Neo4j into EdgeLifecycleManager-compatible dicts.

        Returns (records, outcomes) where each is a list of dicts
        matching ``EdgeLifecycleRecord.from_dict`` / ``OutcomeDistribution.from_dict``.
        """
        if not self.driver:
            return [], []

        query = """
        MATCH (s:UIState)-[r:NEXT_ACTION]->(a:Action)-[p:PRODUCES]->(t:UIState)
        WHERE a.lifecycle_stage IS NOT NULL
        """ + ("  AND s.app IN $app_aliases" if app else "") + """
        RETURN a.action_id AS action_id,
               a.lifecycle_stage AS lifecycle_stage,
               a.source_page_type AS source_page_type,
               a.target_page_type AS target_page_type,
               a.intent AS intent,
               a.semantic_target AS action_target,
               a.verification_count AS verification_count,
               a.dominance_ratio AS dominance_ratio,
               a.outcome_distribution_json AS outcome_distribution_json,
               a.outcome_entropy AS outcome_entropy,
               a.risk_level AS risk_level,
               a.created_at AS created_at,
               a.last_traversed AS last_traversed,
               r.frequency AS frequency
        """
        from phone_agent.spatial.app_registry import get_default_app_registry

        try:
            with self.driver.session(database=self.database) as session:
                result = session.run(
                    query,
                    app_aliases=list(get_default_app_registry().storage_aliases(app)),
                )
                records: list[dict[str, Any]] = []
                outcomes: list[dict[str, Any]] = []
                seen_outcome_keys: set[str] = set()

                for row in result:
                    src = row["source_page_type"] or ""
                    tgt = row["target_page_type"] or ""
                    intent = row["intent"] or ""
                    action_target = row["action_target"] or ""

                    edge_key = "|".join([src, intent, action_target, tgt])

                    outcome_dist_raw = row["outcome_distribution_json"]
                    outcome_counts: dict[str, int] = {}
                    if outcome_dist_raw:
                        try:
                            parsed = json.loads(outcome_dist_raw)
                            outcome_counts = parsed.get("outcomes", {}) if isinstance(parsed, dict) else {}
                        except (json.JSONDecodeError, TypeError):
                            pass

                    records.append({
                        "edge_key": edge_key,
                        "stage": row["lifecycle_stage"] or "hypothesis",
                        "source_page_type": src,
                        "target_page_type": tgt,
                        "intent": intent,
                        "action_target": action_target,
                        "outcome_counts": outcome_counts,
                        "total_attempts": sum(outcome_counts.values()) if outcome_counts else int(row["frequency"] or 0),
                        "verification_count": int(row["verification_count"] or 0),
                        "dominant_outcome": max(outcome_counts, key=lambda k: outcome_counts[k]) if outcome_counts else tgt,
                        "dominance_ratio": float(row["dominance_ratio"] or 0.0),
                        "risk_level": row["risk_level"] or "normal",
                        "last_verified_step": int(row["last_traversed"] or 0),
                        "created_step": int(row["created_at"] or 0),
                    })

                    action_key = f"{intent}:{action_target}"
                    okey = f"{src}|{action_key}"
                    if okey not in seen_outcome_keys and outcome_counts:
                        seen_outcome_keys.add(okey)
                        outcomes.append({
                            "source_page_type": src,
                            "action_key": action_key,
                            "outcomes": outcome_counts,
                        })

                logger.info("lifecycle load: %d records, %d outcomes from Neo4j", len(records), len(outcomes))
                return records, outcomes

        except Exception as exc:
            logger.warning("lifecycle load failed: %s", exc)
            return [], []

    def demote_stale_edges(self, threshold: float = 0.4) -> int:
        """Demote promoted edges in Neo4j whose dominance dropped below threshold.

        This is a graph-side cleanup that mirrors EdgeLifecycleManager.check_demotion().
        """
        if not self.driver:
            return 0

        query = """
        MATCH (a:Action)
        WHERE a.lifecycle_stage = 'promoted'
          AND a.dominance_ratio IS NOT NULL
          AND a.dominance_ratio < $threshold
          AND a.verification_count > 1
        SET a.lifecycle_stage = 'demoted'
        RETURN count(a) AS demoted
        """
        try:
            with self.driver.session(database=self.database) as session:
                result = session.run(query, threshold=threshold)
                record = result.single()
                count = record["demoted"] if record else 0
                if count > 0:
                    logger.info("demoted %d stale edges in Neo4j", count)
                return count
        except Exception as exc:
            logger.warning("demotion failed: %s", exc)
            return 0
