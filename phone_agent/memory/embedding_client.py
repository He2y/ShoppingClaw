import os
import requests
from dotenv import load_dotenv

load_dotenv()

class EmbeddingClient:
    """BigModel Embedding-3 API 封装"""

    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        self.api_key = api_key or os.getenv("EMBEDDING_API_KEY", "")
        self.base_url = base_url or os.getenv("EMBEDDING_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
        self.model = model or os.getenv("EMBEDDING_MODEL", "embedding-3")
        self._dimension = 2048  # Embedding-3 维度 (最新实测为2048)

    def encode(self, texts: list[str]) -> list[list[float]]:
        """调用BigModel Embedding-3 API"""
        if not self.api_key:
            # 如果没有配置API Key，返回模拟向量或抛出异常，这里为了不崩溃返回全0向量
            print("⚠️ 未配置 EMBEDDING_API_KEY，使用全0模拟向量")
            return [[0.0] * self.dimension for _ in texts]

        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "input": texts,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return [item["embedding"] for item in data["data"]]
        except Exception as e:
            print(f"⚠️ Embedding API 调用失败: {e}")
            return [[0.0] * self.dimension for _ in texts]

    @property
    def dimension(self) -> int:
        return self._dimension

    @dimension.setter
    def dimension(self, value: int):
        self._dimension = value
