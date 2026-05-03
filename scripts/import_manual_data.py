import os
import json
import hashlib
import glob
from pathlib import Path
from typing import List, Dict, Any
import base64
import sys

# Ensure phone_agent is in the python path before importing its modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from phone_agent.model.client import ModelClient, ModelConfig, MessageBuilder
from phone_agent.memory.memory_store import MemoryStore, MemoryType, GraphMetadata

try:
    from neo4j import GraphDatabase
    HAS_NEO4J = True
except ImportError:
    HAS_NEO4J = False

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

def generate_semantic_layout(image_path: str, model_client: ModelClient = None) -> str:
    """
    Generate actual semantic layout from image using VLM.
    """
    txt_path = Path(image_path).with_suffix('.txt')
    
    # Return cached if valid and not a mock
    if txt_path.exists():
        with open(txt_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if content and not content.startswith("Semantic layout for UI state"):
                return content

    if not model_client:
        return f"Semantic layout for UI state represented by {Path(image_path).name}"

    print(f"      [VLM] Generating true semantic layout for {Path(image_path).name}...")
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode('utf-8')
            
        system_prompt = "你是一个UI分析专家。请用一句简短的中文描述这张手机屏幕截图属于什么APP的什么页面，以及页面的核心功能区有哪些（例如：'京东APP首页，包含顶部搜索栏、秒杀区和下方商品信息流'）。不要输出任何多余的解释。"
        messages = [
            MessageBuilder.create_system_message(system_prompt),
            MessageBuilder.create_user_message(text="请描述当前UI页面", image_base64=img_b64)
        ]
        
        response = model_client.request(messages)
        layout_desc = response.raw_content.strip()
        
        # Cache it
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(layout_desc)
            
        return layout_desc
    except Exception as e:
        print(f"      [VLM] Error generating layout: {e}")
        return f"Semantic layout for UI state represented by {Path(image_path).name}"


def process_task_directory(task_dir: Path, neo4j_session=None, memory_store=None, model_client=None):
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
    raw_task_desc = actions_data.get("task_description", [])

    # Handle both string and list formats for task_description
    if isinstance(raw_task_desc, list):
        task_descs = [d.strip() for d in raw_task_desc if d.strip()]
    elif isinstance(raw_task_desc, str):
        task_descs = [raw_task_desc.strip()] if raw_task_desc.strip() else []
    else:
        task_descs = []

    # For single description, use simple ID; for multiple, suffix with variant index
    task_ids = [
        f"{app_name}_{task_type}_{task_dir.name}" if len(task_descs) <= 1
        else f"{app_name}_{task_type}_{task_dir.name}_{i+1}"
        for i in range(len(task_descs))
    ]

    # Process nodes and relationships if Neo4j is available
    if neo4j_session and HAS_NEO4J:
        for task_id, task_desc in zip(task_ids, task_descs):
            # Create TaskTarget node — one per description variant
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
            semantic_layout = generate_semantic_layout(str(img_path), model_client)

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

            # Link START/END edges for ALL variant IDs
            # (all variants share the same states/actions)
            # 2. Edges and Actions
            if i == 0:
                for t_id in task_ids:
                    neo4j_session.run(
                        "MATCH (t:TaskTarget {target_id: $task_id}), (s:UIState {state_id: $state_id}) "
                        "MERGE (t)-[:STARTS_AT]->(s)",
                        task_id=t_id, state_id=state_id
                    )

            if prev_state_id and i - 1 < len(actions_data.get('actions', [])):
                # Connect previous state to action, and action to this state
                action_raw = actions_data['actions'][i-1]
                react_raw = react_data[i-1] if i-1 < len(react_data) else {}
                
                action_id = f"act_{prev_state_id}_{i}"
                
                # Try to get logical intent from react.json first
                action_type = react_raw.get('function', {}).get('name') or action_raw.get('action_type', action_raw.get('type', 'unknown'))
                semantic_target = react_raw.get('function', {}).get('parameters', {}).get('target_element', '')
                reasoning = react_raw.get('reasoning', '')
                
                # Action Node: Merge semantic intent with coordinate fallback
                neo4j_session.run(
                    "MERGE (a:Action {action_id: $action_id}) "
                    "SET a.type = $type, a.semantic_target = $semantic_target, a.reasoning = $reasoning, a.raw_coords = $raw_coords",
                    action_id=action_id, 
                    type=action_type, 
                    semantic_target=semantic_target,
                    reasoning=reasoning,
                    raw_coords=json.dumps(action_raw, ensure_ascii=False)
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
                for t_id in task_ids:
                    neo4j_session.run(
                        "MATCH (t:TaskTarget {target_id: $task_id}), (s:UIState {state_id: $state_id}) "
                        "MERGE (t)-[:ENDS_AT {success: true}]->(s)",
                        task_id=t_id, state_id=state_id
                    )

            prev_state_id = state_id

    return {
        "app_name": app_name,
        "task_type": task_type,
        "descriptions": task_descs,
        "task_ids": task_ids,
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

    # Initialize ModelClient for VLM layout generation (Using separate OFFLINE_VLM env vars)
    model_client = None
    offline_api_key = os.getenv("OFFLINE_VLM_API_KEY")
    if offline_api_key and offline_api_key.strip() != "" and offline_api_key != "your_offline_vlm_api_key":
        try:
            model_config = ModelConfig(
                base_url=os.getenv("OFFLINE_VLM_BASE_URL", "https://api.openai.com/v1"),
                model_name=os.getenv("OFFLINE_VLM_MODEL", "gpt-4o-mini"),
                api_key=offline_api_key
            )
            model_client = ModelClient(model_config)
            print(f"Initialized ModelClient for offline VLM generation ({model_config.model_name}).")
        except Exception as e:
            print(f"Could not initialize ModelClient, will skip true semantic layout generation: {e}")
    else:
        print("OFFLINE_VLM_API_KEY is missing or default. Skipping VLM generation (will use fallback strings).")

    total_tasks = 0
    for app_dir in apps:
        print(f"\nProcessing app: {app_dir.name}")
        task_types = [d for d in app_dir.iterdir() if d.is_dir()]

        for task_type_dir in task_types:
            print(f"  Task type: {task_type_dir.name}")
            tasks = [d for d in task_type_dir.iterdir() if d.is_dir()]

            for task_dir in tasks:
                print(f"    Processing task {task_dir.name}...")
                task_data = process_task_directory(task_dir, session, memory_store, model_client)
                if task_data:
                    total_tasks += 1

    print(f"\nProcessing complete! Processed {total_tasks} tasks.")

    if session:
        session.close()
    if driver:
        driver.close()

if __name__ == "__main__":
    main()
