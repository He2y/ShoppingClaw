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

    def find_similar_tasks(self, task_description: str, app: str = None, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        Find semantically similar completed tasks from the graph.
        Falls back to app-name prefix matching when no semantic similarity is available.

        Returns list of task objects with id, description, app, and first few actions.
        """
        if not self.driver:
            return []

        # Primary: semantic text match on task description (case-insensitive substring)
        match_clause = ""
        params: Dict[str, Any] = {"desc": task_description, "top_k": top_k}
        if app:
            match_clause = "WHERE t.app = $app AND toLower(t.description) CONTAINS toLower($desc)"
            params["app"] = app
        else:
            match_clause = "WHERE toLower(t.description) CONTAINS toLower($desc)"

        # Also find tasks with overlapping keywords
        keywords = [w for w in task_description if len(w) >= 2]
        keyword_matches: List[Dict[str, Any]] = []

        if keywords:
            keyword_params: Dict[str, Any] = {"top_k": top_k}
            if app:
                keyword_clause = "WHERE t.app = $app AND " + " OR ".join(
                    f"toLower(t.description) CONTAINS toLower($kw{i})" for i in range(len(keywords))
                )
                keyword_params["app"] = app
            else:
                keyword_clause = "WHERE " + " OR ".join(
                    f"toLower(t.description) CONTAINS toLower($kw{i})" for i in range(len(keywords))
                )
            for i, kw in enumerate(keywords):
                keyword_params[f"kw{i}"] = kw

            keyword_query = f"""
            MATCH (t:TaskTarget)
            {keyword_clause}
            MATCH (t)-[:STARTS_AT]->(first:UIState)
            OPTIONAL MATCH (first)-[r:NEXT_ACTION]->(a:Action)
            RETURN t.target_id AS task_id, t.description AS description, t.app AS app,
                   first.state_id AS start_state, a.type AS action_type,
                   a.semantic_target AS action_target, r.confidence AS confidence,
                   r.frequency AS frequency
            ORDER BY r.frequency DESC
            LIMIT $top_k
            """
            with self.driver.session(database=self.database) as session:
                for record in session.run(keyword_query, keyword_params):
                    keyword_matches.append(dict(record))

        # Substring match query
        query = f"""
        MATCH (t:TaskTarget)
        {match_clause}
        MATCH (t)-[:STARTS_AT]->(first:UIState)
        OPTIONAL MATCH (first)-[r:NEXT_ACTION]->(a:Action)
        RETURN t.target_id AS task_id, t.description AS description, t.app AS app,
               first.state_id AS start_state, a.type AS action_type,
               a.semantic_target AS action_target, r.confidence AS confidence,
               r.frequency AS frequency
        ORDER BY r.frequency DESC
        LIMIT $top_k
        """
        results: List[Dict[str, Any]] = []
        seen_tasks: set = set()

        with self.driver.session(database=self.database) as session:
            for record in session.run(query, **params):
                d = dict(record)
                tid = d.get("task_id", "")
                if tid and tid not in seen_tasks:
                    seen_tasks.add(tid)
                    results.append(d)

        # Merge keyword matches that aren't already in results
        for km in keyword_matches:
            if km.get("task_id") not in seen_tasks:
                seen_tasks.add(km["task_id"])
                results.append(km)

        # De-duplicate by task_id and return top_k
        unique: Dict[str, Dict[str, Any]] = {}
        for r in results:
            tid = r.get("task_id", "")
            if tid:
                if tid not in unique or (r.get("frequency", 0) > unique[tid].get("frequency", 0)):
                    unique[tid] = r
        return list(unique.values())[:top_k]

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