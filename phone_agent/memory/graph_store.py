import os
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

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

        if HAS_NEO4J:
            try:
                self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
                # Test connection
                self.driver.verify_connectivity()
            except Exception as e:
                print(f"Warning: Could not connect to Neo4j. Graph memory disabled. Error: {e}")
                self.driver = None

    def close(self):
        if self.driver:
            self.driver.close()

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
        query = "MATCH (s:UIState) WHERE s.semantic_layout = $layout RETURN s LIMIT $limit"
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
        Find semantically similar completed tasks using token-based OR matching.

        Neo4j stores Chinese descriptions as space-separated chars ('去 京 东 帮 我 点 个 外 卖').
        This method extracts character n-grams from the query and uses OR clauses so
        ANY matching n-gram returns the task, then re-ranks by total matched token count.
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

    def get_task_trajectory(self, task_id: str) -> Dict[str, Any]:
        """
        Get the full action sequence for a completed task.
        Returns {states: [...], actions: [...], description: str}
        """
        if not self.driver:
            return {}

        query = """
        MATCH (t:TaskTarget {target_id: $task_id})
        OPTIONAL MATCH (t)-[:STARTS_AT]->(start:UIState)
        OPTIONAL MATCH (t)-[:ENDS_AT]->(end:UIState)
        MATCH path = (start)-[:NEXT_ACTION*0..]->(:UIState)
        WITH t, start, end, path
        UNWIND nodes(path) AS ns
        WITH t, start, end, collect(DISTINCT ns) AS all_states
        RETURN t.description AS description, t.app AS app,
               start.state_id AS start_state_id, end.state_id AS end_state_id,
               [s IN all_states | s.state_id] AS state_ids
        LIMIT 1
        """
        with self.driver.session(database=self.database) as session:
            result = session.run(query, task_id=task_id).single()
            if not result:
                return {}
            return dict(result)

    def add_state_transition(self, source_state_hash: str, target_state_hash: str, action_data: Dict[str, Any], task_id: str = None):
        """Record a new transition during online exploration."""
        if not self.driver:
            return

        action_id = f"act_{source_state_hash}_{target_state_hash}"
        action_type = action_data.get("action_type", "unknown")

        query = """
        MATCH (s1:UIState {state_id: $s1_id}), (s2:UIState {state_id: $s2_id})
        MERGE (a:Action {action_id: $a_id})
        SET a.type = $type, a.target_desc = $target
        MERGE (s1)-[r1:NEXT_ACTION]->(a)
        ON CREATE SET r1.confidence = 1.0, r1.frequency = 1
        ON MATCH SET r1.frequency = r1.frequency + 1
        MERGE (a)-[r2:PRODUCES]->(s2)
        ON CREATE SET r2.success_rate = 1.0
        """
        with self.driver.session(database=self.database) as session:
            session.run(
                query,
                s1_id=f"state_{source_state_hash}",
                s2_id=f"state_{target_state_hash}",
                a_id=action_id,
                type=action_type,
                target=str(action_data)
            )