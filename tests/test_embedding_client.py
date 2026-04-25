import pytest
import os
from phone_agent.memory.embedding_client import EmbeddingClient

def test_embedding_client_initialization():
    client = EmbeddingClient(api_key="test_key", base_url="http://test.com", model="test-model")
    assert client.api_key == "test_key"
    assert client.base_url == "http://test.com"
    assert client.model == "test-model"
    assert client.dimension == 2048

def test_embedding_client_missing_key(monkeypatch):
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    client = EmbeddingClient(api_key="")
    client.api_key = "" # Ensure it's empty
    # It should return a zero vector instead of crashing
    texts = ["hello"]
    embeddings = client.encode(texts)
    assert len(embeddings) == 1
    assert len(embeddings[0]) == 2048
    assert all(x == 0.0 for x in embeddings[0])
