import os
import json
import hashlib
import glob
from pathlib import Path
from typing import List, Dict, Any

try:
    from neo4j import GraphDatabase
    HAS_NEO4J = True
except ImportError:
    HAS_NEO4J = False

# Import MemoryStore and MemoryType
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from phone_agent.memory.memory_store import MemoryStore, MemoryType, GraphMetadata

def get_image_hash(image_path: str) -> str:
    """Hash an image file as a surrogate for view hierarchy hash."""
    hasher = hashlib.md5()
    try:
        with open(image_path, 'rb') as f:
            buf = f.read()
            hasher.update(buf)
        return hasher.hexdigest()
    except Exception as e:
        print(f"Error hashing image {image_path}: {e}")
        return ""

def generate_semantic_layout(image_path: str) -> str:
    """
    Generate semantic layout from image.
    Currently a mock that returns a basic description, but in reality
    would call a VLM or use a cached layout.
    """
    # Simple caching logic: check if .txt exists with same name
    txt_path = Path(image_path).with_suffix('.txt')
    if txt_path.exists():
        with open(txt_path, 'r', encoding='utf-8') as f:
            return f.read().strip()

    # Mock VLM generation
    layout_desc = f"Semantic layout for UI state represented by {Path(image_path).name}"

    # Cache it
    try:
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(layout_desc)
    except Exception:
        pass

    return layout_desc

def process_task_directory(task_dir: Path, neo4j_session=None, memory_store=None):
    """Process a single task directory like MobiAgent/collect/manual/data/淘宝/基础加购商品/1"""
    actions_file = task_dir / "actions.json"
    react_file = task_dir / "react.json"

    if not actions_file.exists() or not react_file.exists():
        print(f"Missing actions.json or react.json in {task_dir}")
        return None

    with open(actions_file, 'r', encoding='utf-8') as f:
        actions_data = json.load(f)

    with open(react_file, 'r', encoding='utf-8') as f:
        react_data = json.load(f)

    # Get all images for states
    images = sorted(list(task_dir.glob("*.jpg")))
    state_images = [img for img in images if "_" not in img.name]
    state_images.sort(key=lambda x: int(x.stem))

    print(f"Found {len(state_images)} states and {len(actions_data.get('actions', []))} actions in {task_dir}")

    app_name = actions_data.get("app_name", "UnknownApp")
    task_type = actions_data.get("task_type", "UnknownTask")
    task_desc = " ".join(actions_data.get("task_description", []))
    task_id = f"{app_name}_{task_type}_{task_dir.name}"

    # Process nodes and relationships if Neo4j is available
    if neo4j_session and HAS_NEO4J:
        # Create TaskTarget
        neo4j_session.run(
            "MERGE (t:TaskTarget {target_id: $task_id}) "
            "SET t.app = $app, t.task_type = $type, t.description = $desc",
            task_id=task_id, app=app_name, type=task_type, desc=task_desc
        )

        # Process states and actions
        prev_state_id = None
        for i, img_path in enumerate(state_images):
            # 1. State Node
            img_hash = get_image_hash(str(img_path))
            state_id = f"state_{img_hash}"
            semantic_layout = generate_semantic_layout(str(img_path))

            neo4j_session.run(
                "MERGE (s:UIState {state_id: $state_id}) "
                "SET s.app = $app, s.semantic_layout = $layout, s.view_hierarchy_hash = $hash, s.is_popup = false",
                state_id=state_id, app=app_name, layout=semantic_layout, hash=img_hash
            )

            # Store in Semantic Memory for initial alignment
            if memory_store:
                meta: GraphMetadata = {
                    "app_name": app_name,
                    "state_id": state_id
                }
                memory_store.add(
                    content=semantic_layout,
                    memory_type=MemoryType.UI_STATE,
                    metadata=meta
                )

            # 2. Edges and Actions
            if i == 0:
                neo4j_session.run(
                    "MATCH (t:TaskTarget {target_id: $task_id}), (s:UIState {state_id: $state_id}) "
                    "MERGE (t)-[:STARTS_AT]->(s)",
                    task_id=task_id, state_id=state_id
                )

            if prev_state_id and i - 1 < len(actions_data.get('actions', [])):
                # Connect previous state to action, and action to this state
                action = actions_data['actions'][i-1]
                action_id = f"act_{prev_state_id}_{i}"
                action_type = action.get('action_type', 'unknown')

                # Action Node
                neo4j_session.run(
                    "MERGE (a:Action {action_id: $action_id}) "
                    "SET a.type = $type, a.target_desc = $target",
                    action_id=action_id, type=action_type, target=str(action)
                )

                # Edges
                neo4j_session.run(
                    "MATCH (s1:UIState {state_id: $s1_id}), (a:Action {action_id: $a_id}), (s2:UIState {state_id: $s2_id}) "
                    "MERGE (s1)-[:NEXT_ACTION {confidence: 1.0, frequency: 1}]->(a) "
                    "MERGE (a)-[:PRODUCES {success_rate: 1.0}]->(s2)",
                    s1_id=prev_state_id, a_id=action_id, s2_id=state_id
                )

            if i == len(state_images) - 1:
                # End state
                neo4j_session.run(
                    "MATCH (t:TaskTarget {target_id: $task_id}), (s:UIState {state_id: $state_id}) "
                    "MERGE (t)-[:ENDS_AT {success: true}]->(s)",
                    task_id=task_id, state_id=state_id
                )

            prev_state_id = state_id

    return {
        "app_name": app_name,
        "task_type": task_type,
        "descriptions": task_desc,
        "actions": actions_data.get("actions", []),
        "reacts": react_data,
        "state_images": state_images
    }

def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        print("Warning: python-dotenv not installed. Neo4j credentials from .env won't be loaded automatically.")
        
    data_dir = Path("MobiAgent/collect/manual/data")
    if not data_dir.exists():
        print(f"Data directory {data_dir} does not exist.")
        return

    apps = [d for d in data_dir.iterdir() if d.is_dir()]

    # Initialize Neo4j and MemoryStore
    driver = None
    session = None
    if HAS_NEO4J:
        neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        neo4j_user = os.getenv("NEO4J_USER", "neo4j")
        neo4j_password = os.getenv("NEO4J_PASSWORD", "password")
        neo4j_database = os.getenv("NEO4J_DATABASE", "shopping")
        try:
            driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
            session = driver.session(database=neo4j_database)
            print("Connected to Neo4j successfully.")
        except Exception as e:
            print(f"Warning: Could not connect to Neo4j. Graph construction will be skipped. Error: {e}")
            session = None
    else:
        print("Warning: neo4j python driver not installed. Run `pip install neo4j` to enable graph construction.")

    memory_store = MemoryStore(storage_dir="memory_db_offline_import")
    print("Initialized MemoryStore for offline import.")

    total_tasks = 0
    for app_dir in apps:
        print(f"\nProcessing app: {app_dir.name}")
        task_types = [d for d in app_dir.iterdir() if d.is_dir()]

        for task_type_dir in task_types:
            print(f"  Task type: {task_type_dir.name}")
            tasks = [d for d in task_type_dir.iterdir() if d.is_dir()]

            for task_dir in tasks:
                print(f"    Processing task {task_dir.name}...")
                task_data = process_task_directory(task_dir, session, memory_store)
                if task_data:
                    total_tasks += 1

    print(f"\nProcessing complete! Processed {total_tasks} tasks.")

    if session:
        session.close()
    if driver:
        driver.close()

if __name__ == "__main__":
    main()
