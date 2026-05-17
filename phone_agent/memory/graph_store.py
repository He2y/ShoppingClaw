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
    def __init__(self, uri: str = None, user: str = None, password: str = None, database: str = None):
        # Ensure .env is loaded to get the correct Neo4j credentials
        load_dotenv()
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "password")
        self.database = database or os.getenv("NEO4J_DATABASE", "shopping")
        self.driver = None
        self.task_index = None

        if HAS_NEO4J:
            try:
                self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
                # Test connection
                self.driver.verify_connectivity()

                # Initialize FAISS TaskIndex
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
        Returns {description, app, steps: [{state_id, action_type, action_target}...], state_ids: [...]}
        """
        if not self.driver:
            return {}

        # Follow the path: UIState -NEXT_ACTION-> Action -PRODUCES-> UIState -...
        # Fetch up to 20 steps to cover all practical trajectories
        steps_query = """
        MATCH (t:TaskTarget {target_id: $task_id})
        OPTIONAL MATCH (t)-[:STARTS_AT]->(s0:UIState)
        OPTIONAL MATCH (t)-[:ENDS_AT]->(en:UIState)
        OPTIONAL MATCH (s0)-[:NEXT_ACTION]->(a1:Action)-[:PRODUCES]->(s1:UIState)
        OPTIONAL MATCH (s1)-[:NEXT_ACTION]->(a2:Action)-[:PRODUCES]->(s2:UIState)
        OPTIONAL MATCH (s2)-[:NEXT_ACTION]->(a3:Action)-[:PRODUCES]->(s3:UIState)
        OPTIONAL MATCH (s3)-[:NEXT_ACTION]->(a4:Action)-[:PRODUCES]->(s4:UIState)
        OPTIONAL MATCH (s4)-[:NEXT_ACTION]->(a5:Action)-[:PRODUCES]->(s5:UIState)
        OPTIONAL MATCH (s5)-[:NEXT_ACTION]->(a6:Action)-[:PRODUCES]->(s6:UIState)
        OPTIONAL MATCH (s6)-[:NEXT_ACTION]->(a7:Action)-[:PRODUCES]->(s7:UIState)
        OPTIONAL MATCH (s7)-[:NEXT_ACTION]->(a8:Action)-[:PRODUCES]->(s8:UIState)
        OPTIONAL MATCH (s8)-[:NEXT_ACTION]->(a9:Action)-[:PRODUCES]->(s9:UIState)
        OPTIONAL MATCH (s9)-[:NEXT_ACTION]->(a10:Action)-[:PRODUCES]->(s10:UIState)
        OPTIONAL MATCH (s10)-[:NEXT_ACTION]->(a11:Action)-[:PRODUCES]->(s11:UIState)
        OPTIONAL MATCH (s11)-[:NEXT_ACTION]->(a12:Action)-[:PRODUCES]->(s12:UIState)
        OPTIONAL MATCH (s12)-[:NEXT_ACTION]->(a13:Action)-[:PRODUCES]->(s13:UIState)
        OPTIONAL MATCH (s13)-[:NEXT_ACTION]->(a14:Action)-[:PRODUCES]->(s14:UIState)
        OPTIONAL MATCH (s14)-[:NEXT_ACTION]->(a15:Action)-[:PRODUCES]->(s15:UIState)
        RETURN t.description AS description, t.app AS app,
               s0.state_id AS state0,
               a1.type AS a1_type, a1.semantic_target AS a1_target, a1.reasoning AS a1_reasoning,
               s1.state_id AS state1,
               a2.type AS a2_type, a2.semantic_target AS a2_target, a2.reasoning AS a2_reasoning,
               s2.state_id AS state2,
               a3.type AS a3_type, a3.semantic_target AS a3_target, a3.reasoning AS a3_reasoning,
               s3.state_id AS state3,
               a4.type AS a4_type, a4.semantic_target AS a4_target, a4.reasoning AS a4_reasoning,
               s4.state_id AS state4,
               a5.type AS a5_type, a5.semantic_target AS a5_target, a5.reasoning AS a5_reasoning,
               s5.state_id AS state5,
               a6.type AS a6_type, a6.semantic_target AS a6_target, a6.reasoning AS a6_reasoning,
               s6.state_id AS state6,
               a7.type AS a7_type, a7.semantic_target AS a7_target, a7.reasoning AS a7_reasoning,
               s7.state_id AS state7,
               a8.type AS a8_type, a8.semantic_target AS a8_target, a8.reasoning AS a8_reasoning,
               s8.state_id AS state8,
               a9.type AS a9_type, a9.semantic_target AS a9_target, a9.reasoning AS a9_reasoning,
               s9.state_id AS state9,
               a10.type AS a10_type, a10.semantic_target AS a10_target, a10.reasoning AS a10_reasoning,
               s10.state_id AS state10,
               a11.type AS a11_type, a11.semantic_target AS a11_target, a11.reasoning AS a11_reasoning,
               s11.state_id AS state11,
               a12.type AS a12_type, a12.semantic_target AS a12_target, a12.reasoning AS a12_reasoning,
               s12.state_id AS state12,
               a13.type AS a13_type, a13.semantic_target AS a13_target, a13.reasoning AS a13_reasoning,
               s13.state_id AS state13,
               a14.type AS a14_type, a14.semantic_target AS a14_target, a14.reasoning AS a14_reasoning,
               s14.state_id AS state14,
               a15.type AS a15_type, a15.semantic_target AS a15_target, a15.reasoning AS a15_reasoning,
               s15.state_id AS state15,
               en.state_id AS end_state
        LIMIT 1
        """
        with self.driver.session(database=self.database) as session:
            result = session.run(steps_query, task_id=task_id).single()
            if not result:
                return {}

        d = dict(result)
        description = d.get("description", "")
        app = d.get("app", "")
        end_state = d.get("end_state")

        # Build steps list
        steps = []
        state_ids = []
        prev_state = d.get("state0")
        if prev_state:
            state_ids.append(prev_state)

        for i in range(1, 16):
            action_type = d.get(f"a{i}_type")
            action_target = d.get(f"a{i}_target", "")
            action_reasoning = d.get(f"a{i}_reasoning", "")
            next_state = d.get(f"state{i}")
            if action_type:
                steps.append({
                    "step": i,
                    "action_type": action_type or "unknown",
                    "action_target": action_target or "",
                    "reasoning": action_reasoning or "",
                })
            if next_state:
                state_ids.append(next_state)

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
        action_hash = hashlib.md5(str(action_data).encode("utf-8")).hexdigest()[:8]
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
            a.target_desc = $target,
            a.semantic_target = $semantic_target,
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
                target=str(action_data),
                semantic_target=str(action_data.get("semantic_target") or action_data.get("target") or action_data.get("element") or action_data.get("text") or ""),
                reasoning=str(action_data.get("reasoning") or ""),
                source_type=action_data.get("source_type"),
                source_path=action_data.get("source_path"),
                confidence=confidence,
                success_delta=success_delta,
                fail_delta=fail_delta,
                task_id=task_id,
            )

    def get_outgoing_transitions(self, state_id: str, limit: int = 20):
        """Return outgoing graph edges as TransitionEdge objects."""
        if not self.driver:
            return []

        from .spatial_graph_memory import TransitionEdge

        normalized_state_id = self._normalize_state_id(state_id)
        query = """
        MATCH (s:UIState {state_id: $state_id})-[r:NEXT_ACTION]->(a:Action)-[p:PRODUCES]->(t:UIState)
        RETURN s.state_id AS source_id,
               t.state_id AS target_id,
               t.page_type AS target_page_type,
               t.risk_level AS target_risk,
               a.type AS action_type,
               a.semantic_target AS action_target,
               a.target_desc AS action_params,
               r.confidence AS confidence,
               r.frequency AS success_count,
               coalesce(r.fail_count, 0) AS fail_count,
               coalesce(p.success_rate, 1.0) AS success_rate
        ORDER BY confidence DESC, success_count DESC
        LIMIT $limit
        """
        edges = []
        with self.driver.session(database=self.database) as session:
            for record in session.run(query, state_id=normalized_state_id, limit=limit):
                target_page_type = record["target_page_type"] or ""
                target_risk = record["target_risk"] or "normal"
                edges.append(
                    TransitionEdge(
                        source_id=record["source_id"],
                        target_id=record["target_id"],
                        action_type=record["action_type"] or "unknown",
                        action_target=record["action_target"] or "",
                        action_params={"raw": record["action_params"] or ""},
                        postcondition=target_page_type,
                        success_count=record["success_count"] or 0,
                        fail_count=record["fail_count"] or 0,
                        risk=target_risk,
                        confidence=record["confidence"] or record["success_rate"] or 0.0,
                    )
                )
        return edges
