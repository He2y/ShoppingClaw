import os
import json
import numpy as np
import faiss
from pathlib import Path
from .embedding_client import EmbeddingClient

class TaskIndex:
    """任务描述的语义向量索引"""
    def __init__(self, storage_dir: str = "memory_db/default"):
        self._client = EmbeddingClient()
        self.dimension = self._client.dimension  # 1536
        self.index = faiss.IndexFlatIP(self.dimension)
        self.task_ids: list[str] = []

        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.storage_dir / "task_index.npy"
        self.ids_path = self.storage_dir / "task_ids.json"

    def add_task(self, task_id: str, description: str) -> None:
        """向索引添加一个新任务"""
        emb = self._client.encode([description])[0]
        emb = np.array(emb, dtype=np.float32)
        # L2归一化，使用IndexFlatIP即为余弦相似度
        faiss.normalize_L2(emb.reshape(1, -1))
        self.index.add(emb.reshape(1, -1))
        self.task_ids.append(task_id)
        self.save()

    def search(self, query: str, top_k: int = 3) -> list[tuple[str, float]]:
        """语义搜索：返回 [(task_id, similarity_score), ...]"""
        if self.index.ntotal == 0:
            return []

        q_emb = np.array(self._client.encode([query])[0], dtype=np.float32)
        faiss.normalize_L2(q_emb.reshape(1, -1))

        k = min(top_k, self.index.ntotal)
        scores, indices = self.index.search(q_emb.reshape(1, -1), k)

        results = []
        for j, i in enumerate(indices[0]):
            if i < len(self.task_ids) and i >= 0:
                results.append((self.task_ids[i], float(scores[0][j])))
        return results

    def rebuild_from_neo4j(self, neo4j_descriptions: list[tuple[str, str]]) -> None:
        """从Neo4j中的TaskTarget列表重建索引
        neo4j_descriptions: [(task_id, description), ...]
        """
        self.index = faiss.IndexFlatIP(self.dimension)
        self.task_ids = []

        if not neo4j_descriptions:
            self.save()
            return

        task_ids = [item[0] for item in neo4j_descriptions]
        descriptions = [item[1] for item in neo4j_descriptions]

        # 批量编码
        embeddings = self._client.encode(descriptions)
        embeddings = np.array(embeddings, dtype=np.float32)
        faiss.normalize_L2(embeddings)

        self.index.add(embeddings)
        self.task_ids = task_ids
        self.save()

    def save(self) -> None:
        """持久化到 numpy array + json"""
        try:
            if self.index.ntotal > 0:
                # 提取FAISS中的向量数据
                vectors = faiss.rev_swig_ptr(self.index.get_xb(), self.index.ntotal * self.dimension)
                vectors = vectors.reshape(self.index.ntotal, self.dimension)
                np.save(str(self.index_path), vectors)
            else:
                if self.index_path.exists():
                    self.index_path.unlink()

            with open(self.ids_path, 'w', encoding='utf-8') as f:
                json.dump(self.task_ids, f, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ 保存 TaskIndex 失败: {e}")

    def load(self) -> bool:
        """从文件加载索引"""
        try:
            if not self.ids_path.exists():
                return False

            with open(self.ids_path, 'r', encoding='utf-8') as f:
                self.task_ids = json.load(f)

            if self.index_path.exists():
                vectors = np.load(str(self.index_path))
                if len(vectors) > 0:
                    self.index = faiss.IndexFlatIP(self.dimension)
                    self.index.add(vectors)

            return True
        except Exception as e:
            print(f"⚠️ 加载 TaskIndex 失败: {e}")
            self.index = faiss.IndexFlatIP(self.dimension)
            self.task_ids = []
            return False
