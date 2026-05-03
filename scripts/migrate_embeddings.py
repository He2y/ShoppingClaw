#!/usr/bin/env python3
"""
Data migration script for MemoryStore embedding upgrade.

Migrates existing memories from 128d SimpleEmbedder to 2048d embedding-3.
"""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from phone_agent.memory.memory_store import MemoryStore, SimpleEmbedder
from phone_agent.memory.embedding_client import EmbeddingClient


def migrate_memory_store(storage_dir: str = "memory_db/default", backup: bool = True):
    """Migrate MemoryStore from 128d to 2048d embeddings.

    Args:
        storage_dir: Path to memory storage directory
        backup: Whether to create backup before migration
    """
    print(f"🔄 Starting migration for {storage_dir}")

    # Backup existing data
    if backup:
        import shutil
        backup_dir = f"{storage_dir}_backup_{int(os.path.getmtime(storage_dir))}"
        print(f"📦 Creating backup: {backup_dir}")
        shutil.copytree(storage_dir, backup_dir)

    # Load existing store with old embedder
    print("�� Loading existing memories...")
    old_store = MemoryStore(storage_dir=storage_dir, embedding_dim=128)
    memory_count = len(old_store.memories)
    print(f"   Found {memory_count} memories")

    if memory_count == 0:
        print("✅ No memories to migrate")
        return

    # Initialize new embedding client
    print("🔧 Initializing embedding-3 client...")
    embedding_client = EmbeddingClient()

    # Re-embed all memories
    print("��� Re-embedding memories with embedding-3...")
    migrated = 0
    failed = 0

    for memory_id, memory in old_store.memories.items():
        try:
            # Generate new embedding
            new_embedding = embedding_client.encode([memory.content])[0]

            # Check if we got a valid embedding
            if sum(abs(x) for x in new_embedding) > 0:
                memory.embedding = new_embedding
                migrated += 1
            else:
                print(f"⚠️  Got zero embedding for memory {memory_id}, using fallback")
                # Use SimpleEmbedder fallback
                simple_emb = SimpleEmbedder(dim=128).encode([memory.content])[0]
                memory.embedding = simple_emb + [0.0] * (2048 - 128)
                migrated += 1
        except Exception as e:
            print(f"❌ Failed to re-embed memory {memory_id}: {e}")
            failed += 1

    # Update embedding_dim and rebuild index
    print("🔨 Rebuilding FAISS index...")
    old_store.embedding_dim = 2048
    old_store._rebuild_index()

    # Save migrated data
    print("💾 Saving migrated memories...")
    old_store._save_memories()

    # Summary
    print("\n" + "="*50)
    print(f"✅ Migration complete!")
    print(f"   Total memories: {memory_count}")
    print(f"   Migrated: {migrated}")
    print(f"   Failed: {failed}")
    if backup:
        print(f"   Backup: {backup_dir}")
    print("="*50)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Migrate MemoryStore embeddings to 2048d")
    parser.add_argument(
        "--storage-dir",
        default="memory_db/default",
        help="Path to memory storage directory"
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip backup creation"
    )

    args = parser.parse_args()

    migrate_memory_store(
        storage_dir=args.storage_dir,
        backup=not args.no_backup
    )
