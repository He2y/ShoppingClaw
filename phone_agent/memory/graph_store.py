import ast
import hashlib
import json
import os
import re
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from .task_index import TaskIndex

try:
    from neo4j import GraphDatabase
    HAS_NEO4J = True
except ImportError:
    HAS_NEO4J = False

class GraphStore:
    """
    Spatial Memory Store using Neo4j Graph Database.
    Handles UI state graphs, transitions, and shortcut retrieval.
    """
    def __init__(
        self,
        uri: str = None,
        user: str = None,
        password: str = None,
        database: str = None,
        enable_task_index: bool = True,
    ):
        # Ensure .env is loaded to get the correct Neo4j credentials
        load_dotenv()
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "password")
        self.database = database or os.getenv("NEO4J_DATABASE", "shopping")
        self.driver = None
        self.task_index = None
        self.enable_task_index = enable_task_index

        if HAS_NEO4J:
            try:
                self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
                # Test connection
                self.driver.verify_connectivity()

                # Legacy task replay index is optional. AMSG v4 runtime graphs
                # intentionally do not require TaskTarget/STARTS_AT.
                if self.enable_task_index:
                    self.task_index = TaskIndex()
                    if not self.task_index.load():
                        print("FAISS cache empty, triggering rebuild from Neo4j...")
                        self._rebuild_task_index()
            except Exception as e:
                print(f"⚠️ Neo4j 不可用（{e}），空间记忆图功能已降级")
                self.driver = None
                self.task_index = None


    def _rebuild_task_index(self):
        """Fetch all TaskTargets from Neo4j and rebuild the FAISS index."""
        if not self.driver:
            return
        
        query = "MATCH (t:TaskTarget) RETURN t.target_id AS id, t.description AS desc"
        descriptions = []
        with self.driver.session(database=self.database) as session:
            for record in session.run(query):
                descriptions.append((record["id"], record["desc"]))
                
        if descriptions:
            self.task_index.rebuild_from_neo4j(descriptions)
            print(f"✅ Rebuilt TaskIndex with {len(descriptions)} tasks")

    def close(self):
        if self.driver:
            self.driver.close()

    def ensure_database(self, database: str | None = None) -> None:
        """Create the target Neo4j database when the server supports multi-database."""
        if not self.driver:
            raise RuntimeError("Neo4j driver is unavailable")
        name = database or self.database
        if not re.match(r"^[A-Za-z0-9.-]+$", name):
            raise ValueError(f"Invalid Neo4j database name: {name}. Use letters, numbers, dots, or dashes.")
        with self.driver.session(database="system") as session:
            session.run(f"CREATE DATABASE `{name}` IF NOT EXISTS WAIT").consume()

    def get_current_state(self, state_hash: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific UI state by its hash."""
        if not self.driver:
            return None

        query = "MATCH (s:UIState {state_id: $state_id}) RETURN s"
        with self.driver.session(database=self.database) as session:
            result = session.run(query, state_id=f"state_{state_hash}")
            record = result.single()
            return dict(record["s"]) if record else None

    def get_state_by_semantic(self, semantic_layout: str, limit: int = 1) -> Optional[Dict[str, Any]]:
        """Fallback: Retrieve a specific UI state by its semantic layout if exact hash fails."""
        if not self.driver or not semantic_layout:
            return None

        # In a real implementation this should use vector search,
        # but for now we try a simple string similarity or exact match on layout
        query = """
        MATCH (s:UIState)
        WHERE s.semantic_signature = $layout OR s.semantic_layout = $layout
        RETURN s
        LIMIT $limit
        """
        with self.driver.session(database=self.database) as session:
            result = session.run(query, layout=semantic_layout, limit=limit)
            record = result.single()
            return dict(record["s"]) if record else None

    def find_page_state_candidates(self, app: str = "", page_type: str = "", limit: int = 10) -> List[Dict[str, Any]]:
        """Return page-level candidates for approximate runtime localization."""
        if not self.driver or not page_type:
            return []

        query = """
        MATCH (s:UIState)
        WHERE s.page_type = $page_type
          AND ($app = "" OR s.app = $app)
        OPTIONAL MATCH (s)-[rel]-()
        WITH s, count(rel) AS degree
        RETURN s
        ORDER BY degree DESC, coalesce(s.updated_at, 0) DESC
        LIMIT $limit
        """
        candidates = []
        with self.driver.session(database=self.database) as session:
            for record in session.run(query, app=app or "", page_type=page_type, limit=limit):
                candidates.append(dict(record["s"]))
        return candidates

    def find_similar_pages(
        self,
        page_type: str,
        summary: str = "",
        app: str = "",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Find UIState nodes matching page_type with optional summary keyword overlap.

        Uses Neo4j string matching for lightweight dedup queries. For full
        embedding-based similarity, use the SpatialGraphMemory layer.
        """
        if not self.driver or not page_type:
            return []

        # If no summary, fall back to page_type-only query
        if not summary.strip():
            return self.find_page_state_candidates(app=app, page_type=page_type, limit=limit)

        query = """
        MATCH (s:UIState)
        WHERE s.page_type = $page_type
          AND ($app = "" OR s.app = $app)
          AND (
            s.summary CONTAINS $summary
            OR s.semantic_signature CONTAINS $summary
            OR $summary CONTAINS coalesce(s.summary, "")
          )
        OPTIONAL MATCH (s)-[rel]-()
        WITH s, count(rel) AS degree
        RETURN s
        ORDER BY degree DESC, coalesce(s.updated_at, 0) DESC
        LIMIT $limit
        """
        results: List[Dict[str, Any]] = []
        with self.driver.session(database=self.database) as session:
            for record in session.run(
                query, page_type=page_type, summary=summary.strip(), app=app or "", limit=limit
            ):
                results.append(dict(record["s"]))
        return results

    def find_v4_page_candidates(
        self,
        app: str = "",
        page_type: str = "",
        semantic_signature: str = "",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Find UIState candidates using only the AMSG v4 runtime schema."""
        if not self.driver:
            return []
        if not page_type and not semantic_signature:
            return []

        query = """
        MATCH (s:UIState)
        WHERE ($page_type = "" OR s.page_type = $page_type)
          AND (
            size($app_aliases) = 0
            OR s.app IN $app_aliases
            OR any(alias IN $app_aliases
                   WHERE toLower(coalesce(s.app, "")) CONTAINS toLower(alias)
                      OR toLower(alias) CONTAINS toLower(coalesce(s.app, "")))
          )
          AND (
            $semantic_signature = ""
            OR s.semantic_signature = $semantic_signature
            OR s.semantic_layout = $semantic_signature
          )
        OPTIONAL MATCH (s)-[:NEXT_ACTION]->(:Action)
        WITH s, count(*) AS outgoing_degree
        OPTIONAL MATCH (:Action)-[:PRODUCES]->(s)
        WITH s, outgoing_degree, count(*) AS incoming_degree
        RETURN s
        ORDER BY
          CASE
            WHEN $semantic_signature <> "" AND s.semantic_signature = $semantic_signature THEN 0
            WHEN $semantic_signature <> "" AND s.semantic_layout = $semantic_signature THEN 1
            ELSE 2
          END,
          (outgoing_degree + incoming_degree) DESC,
          coalesce(s.updated_at, 0) DESC
        LIMIT $limit
        """
        candidates: List[Dict[str, Any]] = []
        with self.driver.session(database=self.database) as session:
            for record in session.run(
                query,
                app_aliases=self._runtime_app_aliases(app),
                page_type=page_type or "",
                semantic_signature=semantic_signature or "",
                limit=limit,
            ):
                candidates.append(dict(record["s"]))
        return candidates

    def get_v4_outgoing_edges(self, state_id: str, app: str = "", limit: int = 20):
        """Return outgoing AMSG v4 edges from UIState->Action->UIState."""
        return self.get_outgoing_transitions(state_id=state_id, limit=limit, app=app)

    def get_v4_functionality_context(
        self,
        page_type: str,
        app: str = "",
        state_id: str | None = None,
    ) -> Dict[str, Any]:
        """Read FunctionalityItem/Cluster context without legacy TaskTarget fallback."""
        result: Dict[str, Any] = {
            "available_roles": [],
            "data_items": [],
            "verified_clusters": [],
            "implemented_actions": [],
            "semantic_hint": "",
        }
        if not self.driver or not page_type:
            return result

        with self.driver.session(database=self.database) as session:
            roles_query = """
            MATCH (s:UIState)
            WHERE ($state_id = "" OR s.state_id = $state_id)
              AND s.page_type = $page_type
              AND (
                size($app_aliases) = 0
                OR s.app IN $app_aliases
                OR any(alias IN $app_aliases
                       WHERE toLower(coalesce(s.app, "")) CONTAINS toLower(alias)
                          OR toLower(alias) CONTAINS toLower(coalesce(s.app, "")))
              )
            MATCH (s)-[:EXPOSES_FUNCTION]->(i:FunctionalityItem)
            WHERE i.type = 'functionality'
              AND coalesce(i.is_promotable, true) = true
            RETURN DISTINCT i.canonical_role AS role, count(*) AS cnt
            ORDER BY cnt DESC
            """
            result["available_roles"] = [
                dict(record)
                for record in session.run(
                    roles_query,
                    state_id=state_id or "",
                    page_type=page_type,
                    app_aliases=self._runtime_app_aliases(app),
                )
            ]

            data_query = """
            MATCH (s:UIState)
            WHERE ($state_id = "" OR s.state_id = $state_id)
              AND s.page_type = $page_type
              AND (
                size($app_aliases) = 0
                OR s.app IN $app_aliases
                OR any(alias IN $app_aliases
                       WHERE toLower(coalesce(s.app, "")) CONTAINS toLower(alias)
                          OR toLower(alias) CONTAINS toLower(coalesce(s.app, "")))
              )
            MATCH (s)-[:EXPOSES_FUNCTION]->(i:FunctionalityItem)
            WHERE i.type = 'data'
              AND coalesce(i.canonical_role, '') <> ''
            RETURN DISTINCT i.canonical_role AS role,
                   i.description AS sample,
                   i.confidence AS conf
            ORDER BY conf DESC
            LIMIT 12
            """
            result["data_items"] = [
                dict(record)
                for record in session.run(
                    data_query,
                    state_id=state_id or "",
                    page_type=page_type,
                    app_aliases=self._runtime_app_aliases(app),
                )
            ]

            cluster_query = """
            MATCH (c:FunctionalityCluster)
            WHERE coalesce(c.success_count, 0) > 0
              AND $page_type IN coalesce(c.page_types, [])
            RETURN c.canonical_name AS name,
                   c.canonical_description AS description,
                   c.success_count AS verified,
                   size(coalesce(c.member_functionality_ids, [])) AS members,
                   c.risk_level AS risk
            ORDER BY verified DESC
            """
            result["verified_clusters"] = [
                dict(record)
                for record in session.run(cluster_query, page_type=page_type)
            ]

            impl_query = """
            MATCH (a:Action)-[:IMPLEMENTS_FUNCTION]->(i)
            WHERE (i:FunctionalityItem OR i:FunctionalityCluster)
              AND (
                coalesce(i.page_type, '') = $page_type
                OR $page_type IN coalesce(i.page_types, [])
              )
              AND (
                size($app_aliases) = 0
                OR coalesce(i.app, '') = ''
                OR i.app IN $app_aliases
                OR any(alias IN $app_aliases
                       WHERE toLower(coalesce(i.app, "")) CONTAINS toLower(alias)
                          OR toLower(alias) CONTAINS toLower(coalesce(i.app, "")))
              )
            RETURN a.type AS action_type,
                   a.semantic_target AS target,
                   coalesce(i.canonical_role, i.canonical_name) AS role,
                   a.region AS region
            LIMIT 20
            """
            result["implemented_actions"] = [
                dict(record)
                for record in session.run(
                    impl_query,
                    page_type=page_type,
                    app_aliases=self._runtime_app_aliases(app),
                )
            ]

        hint_parts: list[str] = []
        if result["available_roles"]:
            role_names = [record.get("role", "") for record in result["available_roles"][:8]]
            role_names = [role for role in role_names if role]
            if role_names:
                hint_parts.append(f"[Available actions on {page_type}]: {', '.join(role_names)}")
        if result["verified_clusters"]:
            cluster_names = [record.get("name", "") for record in result["verified_clusters"][:5]]
            cluster_names = [name for name in cluster_names if name]
            if cluster_names:
                hint_parts.append(f"[Verified navigation paths]: {', '.join(cluster_names)}")
        if result["data_items"]:
            data_roles = [record.get("role", "") for record in result["data_items"][:6]]
            data_roles = [role for role in data_roles if role]
            if data_roles:
                hint_parts.append(f"[Observable data fields]: {', '.join(data_roles)}")
        result["semantic_hint"] = " | ".join(hint_parts)
        return result

    def load_v4_runtime_subgraph(
        self,
        app: str,
        start_state_ids: List[str] | None = None,
        target_page_types: List[str] | None = None,
        limit: int = 64,
    ) -> Dict[str, Any]:
        """Load a bounded UIState/Action subgraph for V4 runtime planning."""
        return self.load_spatial_subgraph(
            app=app or "",
            goal_spec={"target_page_types": list(target_page_types or [])},
            start_candidates=list(start_state_ids or []),
            max_nodes=limit,
        )

    @staticmethod
    def _runtime_app_aliases(app: str = "") -> List[str]:
        normalized = str(app or "").strip()
        if not normalized:
            return []
        aliases = {normalized}
        lowered = normalized.lower()
        if "taobao" in lowered or "淘宝" in normalized:
            aliases.update({"taobao", "Taobao", "淘宝", "com.taobao.taobao"})
        if "tmall" in lowered or "天猫" in normalized:
            aliases.update({"tmall", "Tmall", "天猫"})
        if "jd" in lowered or "jingdong" in lowered or "京东" in normalized:
            aliases.update({"jd", "jingdong", "JD", "京东"})
        return sorted(aliases)

    def get_page_type_coverage(self, app: str = "") -> Dict[str, int]:
        """Return canonical page coverage counts grouped by page_type."""
        if not self.driver:
            return {}
        query = """
        MATCH (s:UIState)
        WHERE ($app = "" OR s.app = $app)
        RETURN coalesce(s.page_type, "unknown") AS page_type, count(s) AS count
        ORDER BY count DESC
        """
        coverage: Dict[str, int] = {}
        with self.driver.session(database=self.database) as session:
            for record in session.run(query, app=app or ""):
                coverage[str(record["page_type"])] = int(record["count"] or 0)
        return coverage

    def get_transition_coverage(self, app: str = "") -> Dict[str, int]:
        """Return transition coverage counts grouped by source/target page type."""
        if not self.driver:
            return {}
        query = """
        MATCH (s:UIState)-[:NEXT_ACTION]->(:Action)-[:PRODUCES]->(t:UIState)
        WHERE coalesce(s.app, "") = coalesce(t.app, "")
          AND ($app = "" OR s.app = $app)
        RETURN coalesce(s.page_type, "unknown") AS source_type,
               coalesce(t.page_type, "unknown") AS target_type,
               count(*) AS count
        ORDER BY count DESC
        """
        coverage: Dict[str, int] = {}
        with self.driver.session(database=self.database) as session:
            for record in session.run(query, app=app or ""):
                key = f"{record['source_type']}->{record['target_type']}"
                coverage[key] = int(record["count"] or 0)
        return coverage

    def get_missing_edges(self, app: str, target_flow: List[str]) -> List[Dict[str, str]]:
        """Return missing adjacent page-type transitions for a target flow."""
        transition_coverage = self.get_transition_coverage(app)
        missing: List[Dict[str, str]] = []
        for source, target in zip(target_flow, target_flow[1:]):
            key = f"{source}->{target}"
            if transition_coverage.get(key, 0) <= 0:
                missing.append({"source": source, "target": target, "edge": key})
        return missing

    def load_spatial_subgraph(
        self,
        app: str,
        goal_spec: Dict[str, Any],
        start_candidates: List[str] | None = None,
        max_nodes: int = 64,
    ) -> Dict[str, Any]:
        """Load a bounded task-relevant subgraph for runtime DAG compilation."""
        if not self.driver:
            return {"nodes": [], "edges": []}
        target_page_types = list(goal_spec.get("target_page_types") or [])
        start_candidates = start_candidates or []
        query = """
        MATCH (s:UIState)-[r:NEXT_ACTION]->(a:Action)-[p:PRODUCES]->(t:UIState)
        WHERE coalesce(s.app, "") = coalesce(t.app, "")
          AND ($app = "" OR s.app = $app)
          AND (
            size($starts) = 0 OR s.state_id IN $starts OR
            size($targets) = 0 OR t.page_type IN $targets OR s.page_type IN $targets
          )
        RETURN s, t,
               a.type AS action_type,
               a.semantic_target AS action_target,
               a.target_desc AS action_params,
               a.summary AS action_summary,
               a.reasoning AS action_reasoning,
               r.confidence AS confidence,
               r.frequency AS success_count,
               coalesce(r.fail_count, 0) AS fail_count,
               coalesce(p.success_rate, 1.0) AS success_rate
        ORDER BY confidence DESC, success_count DESC
        LIMIT $limit
        """
        nodes: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []
        with self.driver.session(database=self.database) as session:
            for record in session.run(
                query,
                app=app or "",
                targets=target_page_types,
                starts=start_candidates,
                limit=max_nodes,
            ):
                source = dict(record["s"])
                target = dict(record["t"])
                if source.get("state_id"):
                    nodes[source["state_id"]] = source
                if target.get("state_id"):
                    nodes[target["state_id"]] = target
                edges.append(
                    {
                        "source_id": source.get("state_id", ""),
                        "target_id": target.get("state_id", ""),
                        "action_type": record["action_type"] or "unknown",
                        "action_target": record["action_target"] or "",
                        "action_params": self._decode_action_params(record["action_params"] or ""),
                        "evidence": record["action_summary"] or record["action_reasoning"] or "",
                        "confidence": record["confidence"] or record["success_rate"] or 0.0,
                        "success_count": record["success_count"] or 0,
                        "fail_count": record["fail_count"] or 0,
                        "postcondition": target.get("page_type", ""),
                        "risk": target.get("risk_level", "normal"),
                    }
                )
        return {"nodes": list(nodes.values()), "edges": edges}

    def get_next_actions(self, state_hash: str, min_confidence: float = 0.5) -> List[Dict[str, Any]]:
        """Retrieve possible next actions from the current state."""
        if not self.driver:
            return []

        query = """
        MATCH (s:UIState {state_id: $state_id})-[r:NEXT_ACTION]->(a:Action)
        WHERE r.confidence >= $min_confidence
        RETURN a.action_id AS action_id, a.type AS type, a.target_desc AS target, r.confidence AS confidence, r.frequency AS freq
        ORDER BY r.confidence DESC, r.frequency DESC
        """
        actions = []
        with self.driver.session(database=self.database) as session:
            results = session.run(query, state_id=f"state_{state_hash}", min_confidence=min_confidence)
            for record in results:
                actions.append({
                    "action_id": record["action_id"],
                    "type": record["type"],
                    "target_desc": record["target"],
                    "confidence": record["confidence"],
                    "frequency": record["freq"]
                })
        return actions

    def _tokenize_chinese(self, text: str) -> set[str]:
        """
        Split text into tokens suitable for matching against the space-separated
        descriptions stored in Neo4j (where Chinese text is char-space-char-space...).
        Returns individual chars and common 2-char/3-char n-grams.
        """
        tokens: set[str] = set()
        # Individual characters (but skip pure ASCII/punctuation)
        for ch in text:
            if '\u4e00' <= ch <= '\u9fff':  # CJK unified ideographs
                tokens.add(ch)
        # 2-char n-grams for Chinese
        for i in range(len(text) - 1):
            if '\u4e00' <= text[i] <= '\u9fff' and '\u4e00' <= text[i+1] <= '\u9fff':
                tokens.add(text[i:i+2])
        # 3-char n-grams for Chinese
        for i in range(len(text) - 2):
            if ('\u4e00' <= text[i] <= '\u9fff' and
                '\u4e00' <= text[i+1] <= '\u9fff' and
                '\u4e00' <= text[i+2] <= '\u9fff'):
                tokens.add(text[i:i+3])
        return tokens

    def find_similar_tasks(self, task_description: str, app: str = None, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        Find semantically similar completed tasks.

        Uses the GraphRAG TaskIndex (embedding-3) for vector semantic search if available.
        Falls back to Neo4j N-gram matching if vector search fails or is empty.
        """
        if not self.driver:
            return []

        # 优先向量语义搜索
        if hasattr(self, 'task_index') and self.task_index:
            index_results = self.task_index.search(task_description, top_k=top_k)
            if index_results:
                results = []
                with self.driver.session(database=self.database) as session:
                    for task_id, similarity in index_results:
                        query = """
                        MATCH (t:TaskTarget {target_id: $task_id})
                        OPTIONAL MATCH (t)-[:STARTS_AT]->(first:UIState)
                        OPTIONAL MATCH (first)-[r:NEXT_ACTION]->(a:Action)
                        RETURN t.target_id AS task_id, t.description AS description, t.app AS app,
                               first.state_id AS start_state, a.type AS action_type,
                               a.semantic_target AS action_target,
                               r.confidence AS confidence, r.frequency AS frequency
                        ORDER BY r.frequency DESC
                        LIMIT 1
                        """
                        result = session.run(query, task_id=task_id).single()
                        if result:
                            task_data = dict(result)
                            task_data["similarity"] = similarity
                            results.append(task_data)

                if results:
                    # Sort by similarity descending
                    results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
                    return results

        # Fallback: N-gram关键词匹配（现有逻辑）
        return self._find_by_ngram(task_description, app, top_k)

    def _find_by_ngram(self, task_description: str, app: str = None, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        Original Neo4j token-based OR matching fallback.
        """
        if not self.driver:
            return []

        # Extract Chinese n-gram tokens from user query
        user_tokens = self._tokenize_chinese(task_description)
        # Build two sets:
        # - multi-char (2-3 grams): must match as-is against space-separated string
        # - single-char: also matched; they work because '外' in '外 卖' is a substring
        multi_char = sorted({t for t in user_tokens if len(t) >= 2}, key=len, reverse=True)
        single_char = sorted({t for t in user_tokens if len(t) == 1})
        # Use all tokens for the OR clause (cap at 50 to stay within Neo4j limits)
        query_tokens = (multi_char + single_char)[:50]

        if not query_tokens:
            return []

        # Build OR clauses; ANY match returns the row
        params: Dict[str, Any] = {"top_k": top_k * 10}
        clauses: List[str] = []
        for i, tok in enumerate(query_tokens):
            key = f"q{i}"
            clauses.append(f"toLower(t.description) CONTAINS ${key}")
            params[key] = tok
        if app:
            clauses.insert(0, "t.app = $app")
            params["app"] = app

        where_clause = " OR ".join(clauses)
        if app:
            where_clause = f"t.app = $app AND ({where_clause})"
        query = f"""
        MATCH (t:TaskTarget)
        WHERE {where_clause}
        MATCH (t)-[:STARTS_AT]->(first:UIState)
        OPTIONAL MATCH (first)-[r:NEXT_ACTION]->(a:Action)
        RETURN t.target_id AS task_id, t.description AS description, t.app AS app,
               first.state_id AS start_state, a.type AS action_type,
               a.semantic_target AS action_target,
               r.confidence AS confidence, r.frequency AS frequency
        ORDER BY r.frequency DESC
        LIMIT $top_k
        """
        all_results: List[Dict[str, Any]] = []
        with self.driver.session(database=self.database) as session:
            for record in session.run(query, **params):
                all_results.append(dict(record))

        # Re-rank by token overlap score
        scored = []
        for r in all_results:
            desc_tokens = self._tokenize_chinese(r.get("description", ""))
            matched_multi = {t for t in multi_char if t in desc_tokens}
            matched_single = {t for t in single_char if t in desc_tokens}
            # Score = sum of token lengths for multi-char + count for single-char
            score = sum(len(t) for t in matched_multi) + len(matched_single)
            # Bonus for key semantic phrases
            for kw in ["外卖", "KFC", "闪购", "瑞幸", "吮指", "原味", "生椰"]:
                if kw in r.get("description", "") and kw in task_description:
                    score += 15
            scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:top_k]]

    def get_task_trajectory(self, task_id: str, max_steps: int = 20) -> Dict[str, Any]:
        """
        Get the full action sequence for a completed task.

        Uses an iterative graph walk instead of hardcoded MATCH depth,
        so trajectories of any length (up to *max_steps*) are returned.

        Returns {description, app, steps: [{step, action_type, action_target,
        reasoning, page_type, summary}...], state_ids: [...], end_state}
        """
        if not self.driver:
            return {}

        # 1. Fetch task metadata + start/end states
        meta_query = """
        MATCH (t:TaskTarget {target_id: $task_id})
        OPTIONAL MATCH (t)-[:STARTS_AT]->(s0:UIState)
        OPTIONAL MATCH (t)-[:ENDS_AT]->(en:UIState)
        RETURN t.description AS description, t.app AS app,
               s0.state_id AS start_state, en.state_id AS end_state
        """
        with self.driver.session(database=self.database) as session:
            meta = session.run(meta_query, task_id=task_id).single()
            if not meta:
                return {}

            description = meta["description"] or ""
            app = meta["app"] or ""
            start_state = meta["start_state"]
            end_state = meta["end_state"]

            if not start_state:
                return {
                    "description": description,
                    "app": app,
                    "steps": [],
                    "state_ids": [],
                    "end_state": end_state,
                }

            # 2. Walk the UIState→Action→UIState chain iteratively
            step_query = """
            MATCH (s:UIState {state_id: $current})-[r:NEXT_ACTION]->(a:Action)-[:PRODUCES]->(next:UIState)
            RETURN a.type AS action_type,
                   a.semantic_target AS action_target,
                   a.reasoning AS reasoning,
                   next.state_id AS next_state,
                   next.page_type AS page_type,
                   next.summary AS summary
            ORDER BY r.confidence DESC, r.frequency DESC
            LIMIT 1
            """
            steps: List[Dict[str, Any]] = []
            state_ids = [start_state]
            current = start_state
            visited = {start_state}

            for i in range(max_steps):
                record = session.run(step_query, current=current).single()
                if not record:
                    break
                next_state = record["next_state"]
                steps.append({
                    "step": i + 1,
                    "action_type": record["action_type"] or "unknown",
                    "action_target": record["action_target"] or "",
                    "reasoning": record["reasoning"] or "",
                    "page_type": record["page_type"] or "",
                    "summary": record["summary"] or "",
                })
                if not next_state or next_state in visited:
                    break
                state_ids.append(next_state)
                visited.add(next_state)
                current = next_state

        return {
            "description": description,
            "app": app,
            "steps": steps,
            "state_ids": state_ids,
            "end_state": end_state,
        }

    def commit_task_trajectory(
        self,
        task_description: str,
        task_id: str,
        app: str,
        start_state_id: str,
        end_state_id: str,
        success: bool = True,
    ) -> bool:
        """
        Commit a completed task trajectory to Neo4j so future searches can find it.

        Creates a TaskTarget node with STARTS_AT and ENDS_AT links.
        The individual state transitions are already recorded via add_state_transition.
        """
        if not self.driver:
            print(f"Warning: Neo4j driver not available, cannot commit trajectory.")
            return False

        import hashlib
        from pathlib import Path
        task_hash = hashlib.md5(task_description.encode()).hexdigest()[:8]
        persistent_id = f"{task_id}_{task_hash}"

        # Build query dynamically based on whether state IDs are available
        # If state IDs are None (graph was not populated during execution), skip those matches
        query_parts = []
        if start_state_id:
            query_parts.append("MATCH (s_start:UIState {state_id: $start_state})")
        if end_state_id:
            query_parts.append("MATCH (s_end:UIState {state_id: $end_state})")
        query_parts.append("MERGE (t:TaskTarget {target_id: $tid})")
        query_parts.append("SET t.description = $desc, t.app = $app, t.task_type = $task_id, t.committed_at = timestamp(), t.success = $success")
        if start_state_id:
            query_parts.append("MERGE (t)-[:STARTS_AT]->(s_start)")
        if end_state_id:
            query_parts.append("MERGE (t)-[:ENDS_AT {success: $success}]->(s_end)")
        query_parts.append("RETURN t.target_id AS created_id")
        query = "\n".join(query_parts)
        try:
            with self.driver.session(database=self.database) as session:
                result = session.run(
                    query,
                    tid=persistent_id,
                    desc=task_description,
                    app=app,
                    task_id=task_id,
                    start_state=start_state_id,
                    end_state=end_state_id,
                    success=success,
                ).single()

            # Update FAISS Index via Embedding API
            if result and hasattr(self, 'task_index') and self.task_index:
                self.task_index.add_task(persistent_id, task_description)

            return result is not None
        except Exception as e:
            print(f"Warning: failed to commit task trajectory: {e}")
            return False

    @staticmethod
    def _normalize_state_id(state_id: str) -> str:
        """Return a Neo4j UIState id without adding duplicate state_ prefixes."""
        if not state_id:
            return "state_unknown"
        return state_id if state_id.startswith("state_") else f"state_{state_id}"

    @staticmethod
    def _decode_action_params(value: Any) -> Dict[str, Any]:
        """Recover persisted action dictionaries for graph shortcut replay."""
        if isinstance(value, dict):
            return dict(value)
        if not isinstance(value, str) or not value.strip():
            return {}
        for decoder in (ast.literal_eval, json.loads):
            try:
                decoded = decoder(value)
            except (SyntaxError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if isinstance(decoded, dict):
                return dict(decoded)
        return {"raw": value}

    @classmethod
    def _enrich_action_metadata(
        cls,
        action_data: Dict[str, Any],
        source_metadata: Optional[Dict[str, Any]] = None,
        target_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Attach deterministic route semantics to persisted Action nodes."""
        enriched = dict(action_data)
        summary = str(enriched.get("summary") or "").strip()
        reasoning = str(enriched.get("reasoning") or "").strip()
        if not summary:
            summary = cls._build_action_summary(enriched, source_metadata or {}, target_metadata or {})
            enriched["summary"] = summary
        if not reasoning:
            enriched["reasoning"] = cls._build_action_reasoning(
                enriched,
                source_metadata or {},
                target_metadata or {},
                summary,
            )
        return enriched

    @classmethod
    def _semantic_action_key(
        cls,
        action_data: Dict[str, Any],
        source_metadata: Optional[Dict[str, Any]] = None,
        target_metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Stable action identity that ignores tiny coordinate jitter.

        Raw model actions can differ by a few pixels while representing the
        same UI affordance. AMSG treats coordinates as evidence; the persisted
        Action identity is the source page, normalized intent, semantic target,
        region, and expected postcondition.
        """
        source_metadata = source_metadata or {}
        target_metadata = target_metadata or {}
        action_type = str(action_data.get("action_type") or action_data.get("action") or "unknown").lower()
        intent = str(action_data.get("intent") or action_type).lower()
        source_type = str(action_data.get("source_page_type") or source_metadata.get("page_type") or "")
        target_type = str(
            action_data.get("target_page_type")
            or action_data.get("expected_postcondition")
            or target_metadata.get("page_type")
            or ""
        )
        semantic_target = str(action_data.get("semantic_target") or action_data.get("target") or "")
        region = str(action_data.get("region") or cls._action_region(action_data) or "")
        return "|".join([source_type, intent, semantic_target, region, target_type])

    @classmethod
    def _action_region(cls, action_data: Dict[str, Any]) -> str:
        value = action_data.get("element") or action_data.get("coordinate") or action_data.get("point")
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
            value = value[0]
        if not isinstance(value, list) or len(value) < 2:
            return ""
        try:
            x = float(value[0])
            y = float(value[1])
        except (TypeError, ValueError):
            return ""
        vertical = "top" if y <= 250 else "bottom" if y >= 750 else "middle"
        horizontal = "left" if x <= 330 else "right" if x >= 670 else "center"
        if vertical == "middle" and horizontal == "center":
            return "center"
        return f"{vertical}_{horizontal}"

    @classmethod
    def _build_action_summary(
        cls,
        action_data: Dict[str, Any],
        source_metadata: Dict[str, Any],
        target_metadata: Dict[str, Any],
    ) -> str:
        source_type = str(source_metadata.get("page_type") or "unknown")
        target_type = str(target_metadata.get("page_type") or "unknown")
        action_type = str(action_data.get("action_type") or action_data.get("action") or "unknown")
        target_text = cls._action_target_text(action_data)
        action_phrase = f"{action_type} {target_text}".strip()
        return f"{source_type}->{target_type}: {action_phrase}"

    @classmethod
    def _build_action_reasoning(
        cls,
        action_data: Dict[str, Any],
        source_metadata: Dict[str, Any],
        target_metadata: Dict[str, Any],
        summary: str,
    ) -> str:
        source_type = str(source_metadata.get("page_type") or "unknown")
        target_type = str(target_metadata.get("page_type") or "unknown")
        source_summary = str(source_metadata.get("summary") or source_type)
        target_summary = str(target_metadata.get("summary") or target_type)
        action_type = str(action_data.get("action_type") or action_data.get("action") or "unknown")
        target_text = cls._action_target_text(action_data)
        action_phrase = f"{action_type} {target_text}".strip()
        return (
            f"{summary}. On {source_type} ({source_summary}), perform {action_phrase} "
            f"to reach {target_type} ({target_summary})."
        )

    @classmethod
    def _action_target_text(cls, action_data: Dict[str, Any]) -> str:
        value = (
            action_data.get("semantic_target")
            or action_data.get("target")
            or action_data.get("element")
            or action_data.get("text")
            or ""
        )
        if value:
            return cls._stringify_action_value(value)
        actions = action_data.get("actions")
        if isinstance(actions, list):
            labels = [cls._action_target_text(item) for item in actions if isinstance(item, dict)]
            labels = [label for label in labels if label]
            return " -> ".join(labels)
        return ""

    @staticmethod
    def _stringify_action_value(value: Any) -> str:
        if isinstance(value, (list, tuple)):
            return "[" + ", ".join(GraphStore._stringify_action_value(item) for item in value) + "]"
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)

    def upsert_page_state(self, state_metadata: Dict[str, Any]) -> None:
        """Create or update a UIState node from a SpatialGraphMemory PageState."""
        if not self.driver:
            return

        state_id = self._normalize_state_id(str(state_metadata.get("state_id") or ""))
        query = """
        MERGE (s:UIState {state_id: $state_id})
        SET s.app = coalesce($app, s.app),
            s.page_type = coalesce($page_type, s.page_type),
            s.summary = coalesce($summary, s.summary),
            s.semantic_layout = coalesce($semantic_signature, s.semantic_layout),
            s.semantic_signature = coalesce($semantic_signature, s.semantic_signature),
            s.landmarks = $landmarks,
            s.affordances = $affordances,
            s.slots = $slots,
            s.risk_level = coalesce($risk_level, s.risk_level),
            s.screenshot_hash = coalesce($screenshot_hash, s.screenshot_hash),
            s.updated_at = timestamp()
        """
        with self.driver.session(database=self.database) as session:
            session.run(
                query,
                state_id=state_id,
                app=state_metadata.get("app"),
                page_type=state_metadata.get("page_type"),
                summary=state_metadata.get("summary"),
                semantic_signature=state_metadata.get("semantic_signature"),
                landmarks=list(state_metadata.get("landmarks") or []),
                affordances=list(state_metadata.get("affordances") or []),
                slots=json.dumps(state_metadata.get("slots") or {}, ensure_ascii=False),
                risk_level=state_metadata.get("risk_level"),
                screenshot_hash=state_metadata.get("screenshot_hash"),
            )

    def reset_spatial_graph(self) -> None:
        """Delete all graph-memory nodes in the current database."""
        if not self.driver:
            return
        query = "MATCH (n) DETACH DELETE n"
        with self.driver.session(database=self.database) as session:
            session.run(query)

    def upsert_task_target(
        self,
        *,
        target_id: str,
        app: str,
        task_type: str,
        descriptions: list[str],
        start_state_id: str | None,
        end_state_id: str | None,
        goal_spec: Optional[Dict[str, Any]] = None,
        source_path: str = "",
        source_type: str = "manual",
    ) -> bool:
        """Create/update a TaskTarget linked to its start/end PageState nodes."""
        if not self.driver:
            return False

        start_state = self._normalize_state_id(start_state_id or "") if start_state_id else None
        end_state = self._normalize_state_id(end_state_id or "") if end_state_id else None
        description = descriptions[0] if descriptions else ""
        query_parts = [
            "MERGE (t:TaskTarget {target_id: $target_id})",
            """
            SET t.app = $app,
                t.task_type = $task_type,
                t.description = $description,
                t.descriptions = $descriptions,
                t.goal_spec = $goal_spec,
                t.source_path = $source_path,
                t.source_type = $source_type,
                t.committed_at = timestamp(),
                t.success = true
            """,
        ]
        if start_state:
            query_parts.append("MERGE (s_start:UIState {state_id: $start_state})")
            query_parts.append("MERGE (t)-[:STARTS_AT]->(s_start)")
        if end_state:
            query_parts.append("MERGE (s_end:UIState {state_id: $end_state})")
            query_parts.append("MERGE (t)-[:ENDS_AT {success: true}]->(s_end)")

        with self.driver.session(database=self.database) as session:
            session.run(
                "\n".join(query_parts),
                target_id=target_id,
                app=app,
                task_type=task_type,
                description=description,
                descriptions=descriptions,
                goal_spec=json.dumps(goal_spec or {}, ensure_ascii=False),
                source_path=source_path,
                source_type=source_type,
                start_state=start_state,
                end_state=end_state,
            )
        return True

    def create_exploration_session(
        self,
        session_id: str,
        app: str,
        session_type: str = "offline_exploration",
        pages_discovered: int = 0,
        transitions_promoted: int = 0,
        task_description: str = "",
        source_path: str = "",
    ) -> bool:
        """Create an ExplorationSession node to distinguish offline exploration from online execution.

        ExplorationSession is also labelled TaskTarget so existing task-search
        queries can find it, but the extra label and session_type field allow
        filtering by provenance.
        """
        if not self.driver:
            return False

        query = """
        MERGE (es:ExplorationSession:TaskTarget {target_id: $session_id})
        SET es.session_type = $session_type,
            es.app = $app,
            es.description = $description,
            es.pages_discovered = $pages_discovered,
            es.transitions_promoted = $transitions_promoted,
            es.source_path = $source_path,
            es.explored_at = timestamp(),
            es.success = true
        """
        try:
            with self.driver.session(database=self.database) as session:
                session.run(
                    query,
                    session_id=session_id,
                    session_type=session_type,
                    app=app,
                    description=task_description,
                    pages_discovered=pages_discovered,
                    transitions_promoted=transitions_promoted,
                    source_path=source_path,
                )
            if self.task_index and task_description:
                self.task_index.add_task(session_id, task_description)
            return True
        except Exception as e:
            print(f"Warning: failed to create ExplorationSession: {e}")
            return False

    def find_exploration_sessions(
        self, app: str = "", session_type: str = ""
    ) -> List[Dict[str, Any]]:
        """List ExplorationSession nodes, optionally filtered by app or session_type."""
        if not self.driver:
            return []

        query = """
        MATCH (es:ExplorationSession)
        WHERE ($app = "" OR es.app = $app)
          AND ($session_type = "" OR es.session_type = $session_type)
        RETURN es
        ORDER BY es.explored_at DESC
        """
        results: List[Dict[str, Any]] = []
        with self.driver.session(database=self.database) as session:
            for record in session.run(query, app=app or "", session_type=session_type or ""):
                results.append(dict(record["es"]))
        return results

    def add_state_transition(
        self,
        source_state_hash: str,
        target_state_hash: str,
        action_data: Dict[str, Any],
        task_id: str = None,
        outcome: str = "success",
        source_metadata: Optional[Dict[str, Any]] = None,
        target_metadata: Optional[Dict[str, Any]] = None,
    ):
        """Record a transition and create missing UIState nodes if needed."""
        if not self.driver:
            return

        source_state_id = self._normalize_state_id(source_state_hash)
        target_state_id = self._normalize_state_id(target_state_hash)
        action_data = self._enrich_action_metadata(action_data, source_metadata, target_metadata)
        semantic_edge_key = action_data.get("_semantic_edge_key") or self._semantic_action_key(
            action_data,
            source_metadata,
            target_metadata,
        )
        action_hash = hashlib.md5(semantic_edge_key.encode("utf-8")).hexdigest()[:8]
        action_id = f"act_{source_state_id}_{target_state_id}_{action_hash}"
        action_type = action_data.get("action_type") or action_data.get("action") or "unknown"
        success_delta = 0 if outcome == "failure" else 1
        fail_delta = 1 if outcome == "failure" else 0
        confidence_value = action_data.get("confidence")
        confidence = float(confidence_value) if confidence_value is not None else (0.4 if outcome == "failure" else 1.0)
        source_metadata = source_metadata or {}
        target_metadata = target_metadata or {}

        query = """
        MERGE (s1:UIState {state_id: $s1_id})
        SET s1.semantic_layout = coalesce($s1_semantic_layout, s1.semantic_layout),
            s1.semantic_signature = coalesce($s1_semantic_layout, s1.semantic_signature),
            s1.app = coalesce($s1_app, s1.app),
            s1.page_type = coalesce($s1_page_type, s1.page_type),
            s1.summary = coalesce($s1_summary, s1.summary),
            s1.landmarks = coalesce($s1_landmarks, s1.landmarks),
            s1.affordances = coalesce($s1_affordances, s1.affordances),
            s1.slots = coalesce($s1_slots, s1.slots),
            s1.risk_level = coalesce($s1_risk_level, s1.risk_level),
            s1.updated_at = timestamp()
        MERGE (s2:UIState {state_id: $s2_id})
        SET s2.semantic_layout = coalesce($s2_semantic_layout, s2.semantic_layout),
            s2.semantic_signature = coalesce($s2_semantic_layout, s2.semantic_signature),
            s2.app = coalesce($s2_app, s2.app),
            s2.page_type = coalesce($s2_page_type, s2.page_type),
            s2.summary = coalesce($s2_summary, s2.summary),
            s2.landmarks = coalesce($s2_landmarks, s2.landmarks),
            s2.affordances = coalesce($s2_affordances, s2.affordances),
            s2.slots = coalesce($s2_slots, s2.slots),
            s2.risk_level = coalesce($s2_risk_level, s2.risk_level),
            s2.updated_at = timestamp()
        MERGE (a:Action {action_id: $a_id})
        SET a.type = $type,
            a.intent = $intent,
            a.target_desc = $target,
            a.semantic_target = $semantic_target,
            a.semantic_edge_key = $semantic_edge_key,
            a.expected_postcondition = $expected_postcondition,
            a.source_page_type = $source_page_type,
            a.target_page_type = $target_page_type,
            a.region = $region,
            a.risk_level = $action_risk_level,
            a.target_locator = $target_locator,
            a.summary = $summary,
            a.reasoning = $reasoning,
            a.source_type = coalesce($source_type, a.source_type),
            a.source_path = coalesce($source_path, a.source_path),
            a.updated_at = timestamp()
        MERGE (s1)-[r1:NEXT_ACTION]->(a)
        ON CREATE SET r1.confidence = $confidence,
                      r1.frequency = 0,
                      r1.fail_count = 0,
                      r1.task_id = $task_id
        SET r1.frequency = r1.frequency + $success_delta,
            r1.fail_count = coalesce(r1.fail_count, 0) + $fail_delta,
            r1.confidence = CASE
                WHEN (r1.frequency + coalesce(r1.fail_count, 0)) = 0 THEN $confidence
                ELSE toFloat(r1.frequency) / toFloat(r1.frequency + coalesce(r1.fail_count, 0))
            END
        MERGE (a)-[r2:PRODUCES]->(s2)
        ON CREATE SET r2.success_count = 0, r2.fail_count = 0
        SET r2.success_count = r2.success_count + $success_delta,
            r2.fail_count = r2.fail_count + $fail_delta,
            r2.success_rate = CASE
                WHEN (r2.success_count + r2.fail_count) = 0 THEN $confidence
                ELSE toFloat(r2.success_count) / toFloat(r2.success_count + r2.fail_count)
            END
        """
        with self.driver.session(database=self.database) as session:
            session.run(
                query,
                s1_id=source_state_id,
                s2_id=target_state_id,
                s1_semantic_layout=source_metadata.get("semantic_signature") or source_metadata.get("semantic_layout"),
                s1_app=source_metadata.get("app"),
                s1_page_type=source_metadata.get("page_type"),
                s1_summary=source_metadata.get("summary"),
                s1_landmarks=list(source_metadata["landmarks"]) if "landmarks" in source_metadata else None,
                s1_affordances=list(source_metadata["affordances"]) if "affordances" in source_metadata else None,
                s1_slots=json.dumps(source_metadata.get("slots") or {}, ensure_ascii=False) if "slots" in source_metadata else None,
                s1_risk_level=source_metadata.get("risk_level"),
                s2_semantic_layout=target_metadata.get("semantic_signature") or target_metadata.get("semantic_layout"),
                s2_app=target_metadata.get("app"),
                s2_page_type=target_metadata.get("page_type"),
                s2_summary=target_metadata.get("summary"),
                s2_landmarks=list(target_metadata["landmarks"]) if "landmarks" in target_metadata else None,
                s2_affordances=list(target_metadata["affordances"]) if "affordances" in target_metadata else None,
                s2_slots=json.dumps(target_metadata.get("slots") or {}, ensure_ascii=False) if "slots" in target_metadata else None,
                s2_risk_level=target_metadata.get("risk_level"),
                a_id=action_id,
                type=action_type,
                intent=str(action_data.get("intent") or action_type).lower(),
                target=str(action_data),
                semantic_target=str(action_data.get("semantic_target") or action_data.get("target") or action_data.get("element") or action_data.get("text") or ""),
                semantic_edge_key=semantic_edge_key,
                expected_postcondition=str(action_data.get("expected_postcondition") or target_metadata.get("page_type") or ""),
                source_page_type=str(action_data.get("source_page_type") or source_metadata.get("page_type") or ""),
                target_page_type=str(action_data.get("target_page_type") or target_metadata.get("page_type") or ""),
                region=str(action_data.get("region") or ""),
                action_risk_level=str(action_data.get("risk_level") or target_metadata.get("risk_level") or "normal"),
                target_locator=json.dumps(action_data.get("target_locator") or {}, ensure_ascii=False, sort_keys=True),
                summary=str(action_data.get("summary") or ""),
                reasoning=str(action_data.get("reasoning") or ""),
                source_type=action_data.get("source_type"),
                source_path=action_data.get("source_path"),
                confidence=confidence,
                success_delta=success_delta,
                fail_delta=fail_delta,
                task_id=task_id,
            )

    def upsert_functionality_graph(self, report: Dict[str, Any]) -> Dict[str, int]:
        """Persist AMSG v4 self-discovered functionality into Neo4j.

        This makes functionality coverage a first-class graph surface instead
        of a report-only artifact. Functionality items link to the UIState pages
        that expose them; verified items/clusters link to the Action nodes that
        implement them and to the observed postcondition pages.
        """
        if not self.driver:
            return {"clusters": 0, "items": 0, "ui_links": 0, "action_links": 0, "postcondition_links": 0}

        clusters = [item for item in (report.get("functionality_clusters") or []) if isinstance(item, dict)]
        # Only persist promotable (actionable) items — data items (price,
        # product_title, shop_name, etc.) are ephemeral and pollute the graph.
        items = [
            item for item in (report.get("functionality_items") or [])
            if isinstance(item, dict) and item.get("is_promotable", True)
        ]
        app_filter = str(report.get("app_filter") or "")
        counts = {"clusters": 0, "items": 0, "ui_links": 0, "action_links": 0, "postcondition_links": 0}

        with self.driver.session(database=self.database) as session:
            for cluster in clusters:
                cluster_id = str(cluster.get("cluster_id") or "")
                if not cluster_id:
                    continue
                session.run(
                    """
                    MERGE (c:FunctionalityCluster {cluster_id: $cluster_id})
                    SET c.canonical_name = $canonical_name,
                        c.canonical_description = $canonical_description,
                        c.member_functionality_ids = $member_functionality_ids,
                        c.apps = $apps,
                        c.page_types = $page_types,
                        c.regions = $regions,
                        c.verified_edges = $verified_edges,
                        c.success_count = $success_count,
                        c.fail_count = $fail_count,
                        c.novelty_score = $novelty_score,
                        c.risk_level = $risk_level,
                        c.updated_at = timestamp()
                    """,
                    cluster_id=cluster_id,
                    canonical_name=str(cluster.get("canonical_name") or ""),
                    canonical_description=str(cluster.get("canonical_description") or ""),
                    member_functionality_ids=list(cluster.get("member_functionality_ids") or []),
                    apps=list(cluster.get("apps") or []),
                    page_types=list(cluster.get("page_types") or []),
                    regions=list(cluster.get("regions") or []),
                    verified_edges=list(cluster.get("verified_edges") or []),
                    success_count=int(cluster.get("success_count") or 0),
                    fail_count=int(cluster.get("fail_count") or 0),
                    novelty_score=float(cluster.get("novelty_score") or 0.0),
                    risk_level=str(cluster.get("risk_level") or "normal"),
                )
                counts["clusters"] += 1

            for item in items:
                item_id = str(item.get("functionality_id") or "")
                if not item_id:
                    continue
                source_action = item.get("source_action") if isinstance(item.get("source_action"), dict) else {}
                bbox = item.get("bbox") if isinstance(item.get("bbox"), list) else []
                app = str(item.get("app") or app_filter or "")
                page_type = str(item.get("page_type") or "")
                item_type = str(item.get("type") or "functionality")
                is_promotable = bool(item.get("is_promotable", True))
                cluster_id = str(item.get("cluster_id") or "")
                observed_postcondition = str(item.get("observed_postcondition") or "")
                region = str(item.get("region") or "")
                if region == "unknown":
                    region = ""
                session.run(
                    """
                    MERGE (i:FunctionalityItem {functionality_id: $functionality_id})
                    SET i.page_node_id = $page_node_id,
                        i.app = $app,
                        i.page_type = $page_type,
                        i.type = $type,
                        i.source_kind = $source_kind,
                        i.canonical_role = $canonical_role,
                        i.is_promotable = $is_promotable,
                        i.screen_cluster_id = $screen_cluster_id,
                        i.label = $label,
                        i.description = $description,
                        i.bbox = $bbox,
                        i.region = $region,
                        i.visual_evidence = $visual_evidence,
                        i.text_evidence = $text_evidence,
                        i.source_action = $source_action,
                        i.expected_effect = $expected_effect,
                        i.observed_postcondition = $observed_postcondition,
                        i.confidence = $confidence,
                        i.embedding = $embedding,
                        i.cluster_id = $cluster_id,
                        i.updated_at = timestamp()
                    """,
                    functionality_id=item_id,
                    page_node_id=str(item.get("page_node_id") or ""),
                    app=app,
                    page_type=page_type,
                    type=item_type,
                    source_kind=str(item.get("source_kind") or ""),
                    canonical_role=str(item.get("canonical_role") or ""),
                    is_promotable=is_promotable,
                    screen_cluster_id=str(item.get("screen_cluster_id") or ""),
                    label=str(item.get("label") or ""),
                    description=str(item.get("description") or ""),
                    bbox=bbox,
                    region=region,
                    visual_evidence=str(item.get("visual_evidence") or ""),
                    text_evidence=str(item.get("text_evidence") or ""),
                    source_action=json.dumps(source_action, ensure_ascii=False, sort_keys=True),
                    expected_effect=str(item.get("expected_effect") or ""),
                    observed_postcondition=observed_postcondition,
                    confidence=float(item.get("confidence") or 0.0),
                    embedding=list(item.get("embedding") or []),
                    cluster_id=cluster_id,
                )
                counts["items"] += 1

                if cluster_id and item_type == "functionality" and is_promotable:
                    session.run(
                        """
                        MATCH (i:FunctionalityItem {functionality_id: $functionality_id})
                        MATCH (c:FunctionalityCluster {cluster_id: $cluster_id})
                        MERGE (i)-[:MEMBER_OF]->(c)
                        """,
                        functionality_id=item_id,
                        cluster_id=cluster_id,
                    )

                if page_type:
                    result = session.run(
                        """
                        MATCH (s:UIState)
                        WHERE s.page_type = $page_type
                          AND ($app = "" OR s.app = $app)
                        MATCH (i:FunctionalityItem {functionality_id: $functionality_id})
                        MERGE (s)-[:EXPOSES_FUNCTION]->(i)
                        RETURN count(s) AS linked
                        """,
                        page_type=page_type,
                        app=app,
                        functionality_id=item_id,
                    ).single()
                    counts["ui_links"] += int(result["linked"] or 0) if result else 0

                if observed_postcondition and item_type == "functionality" and is_promotable:
                    result = session.run(
                        """
                        MATCH (a:Action)
                        WHERE ($page_type = "" OR a.source_page_type = $page_type)
                          AND (a.expected_postcondition = $postcondition OR a.target_page_type = $postcondition)
                          AND (
                            $region = ""
                            OR a.region = $region
                            OR a.semantic_target = $canonical_role
                            OR a.semantic_target = $expected_effect
                          )
                        MATCH (i:FunctionalityItem {functionality_id: $functionality_id})
                        MERGE (a)-[:IMPLEMENTS_FUNCTION]->(i)
                        WITH a, i
                        OPTIONAL MATCH (c:FunctionalityCluster {cluster_id: $cluster_id})
                        FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END |
                            MERGE (a)-[:IMPLEMENTS_FUNCTION]->(c)
                        )
                        RETURN count(a) AS linked
                        """,
                        page_type=page_type,
                        postcondition=observed_postcondition,
                        region=region,
                        canonical_role=str(item.get("canonical_role") or ""),
                        expected_effect=str(item.get("expected_effect") or ""),
                        functionality_id=item_id,
                        cluster_id=cluster_id,
                    ).single()
                    counts["action_links"] += int(result["linked"] or 0) if result else 0

                    if cluster_id:
                        result = session.run(
                            """
                            MATCH (c:FunctionalityCluster {cluster_id: $cluster_id})
                            MATCH (t:UIState)
                            WHERE t.page_type = $postcondition
                              AND ($app = "" OR t.app = $app)
                            MERGE (c)-[:LEADS_TO]->(t)
                            RETURN count(t) AS linked
                            """,
                            cluster_id=cluster_id,
                            postcondition=observed_postcondition,
                            app=app,
                        ).single()
                        counts["postcondition_links"] += int(result["linked"] or 0) if result else 0

        return counts

    def get_outgoing_transitions(self, state_id: str, limit: int = 20, app: str = ""):
        """Return outgoing graph edges as TransitionEdge objects."""
        if not self.driver:
            return []

        from .spatial_graph_memory import TransitionEdge

        normalized_state_id = self._normalize_state_id(state_id)
        query = """
        MATCH (s:UIState {state_id: $state_id})-[r:NEXT_ACTION]->(a:Action)-[p:PRODUCES]->(t:UIState)
        WHERE coalesce(t.app, "") = coalesce(s.app, "")
          AND ($app = "" OR s.app = $app)
        RETURN s.state_id AS source_id,
               t.state_id AS target_id,
               t.page_type AS target_page_type,
               t.risk_level AS target_risk,
               a.type AS action_type,
               a.semantic_target AS action_target,
               a.target_desc AS action_params,
               a.summary AS action_summary,
               a.reasoning AS action_reasoning,
               r.confidence AS confidence,
               r.frequency AS success_count,
               coalesce(r.fail_count, 0) AS fail_count,
               coalesce(p.success_rate, 1.0) AS success_rate
        ORDER BY confidence DESC, success_count DESC
        LIMIT $limit
        """
        edges = []
        with self.driver.session(database=self.database) as session:
            for record in session.run(query, state_id=normalized_state_id, limit=limit, app=app or ""):
                target_page_type = record["target_page_type"] or ""
                target_risk = record["target_risk"] or "normal"
                edges.append(
                    TransitionEdge(
                        source_id=record["source_id"],
                        target_id=record["target_id"],
                        action_type=record["action_type"] or "unknown",
                        action_target=record["action_target"] or "",
                        action_params=self._decode_action_params(record["action_params"] or ""),
                        postcondition=target_page_type,
                        success_count=record["success_count"] or 0,
                        fail_count=record["fail_count"] or 0,
                        risk=target_risk,
                        confidence=record["confidence"] or record["success_rate"] or 0.0,
                        evidence=record["action_summary"] or record["action_reasoning"] or "",
                    )
                )
        return edges
