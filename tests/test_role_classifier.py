"""Tests for role classifier (Definition 6)."""

from phone_agent.spatial.role_classifier import (
    EmbeddingRoleClassifier,
    KeywordRoleClassifier,
)
from phone_agent.spatial.functionality import FunctionalityExtractor


def test_keyword_classifier_buy_button():
    kw = KeywordRoleClassifier()
    role, item_type = kw.classify("product_detail", "buy_button", "Buy Now button")
    assert role == "open_spec_selector"
    assert item_type == "functionality"


def test_keyword_classifier_search():
    kw = KeywordRoleClassifier()
    role, item_type = kw.classify("home", "search_bar", "Search bar")
    assert role == "open_search"
    assert item_type == "functionality"


def test_keyword_classifier_price_data():
    kw = KeywordRoleClassifier()
    role, item_type = kw.classify("product_detail", "price_text", "Price ¥99")
    assert item_type == "data"
    assert role == "price"


def test_functionality_extractor_with_keyword_classifier():
    """Injecting KeywordRoleClassifier gives same results as legacy None."""
    fe_legacy = FunctionalityExtractor()
    fe_kw = FunctionalityExtractor(role_classifier=KeywordRoleClassifier())
    page = {"app": "taobao", "page_type": "product_detail", "elements": {"buy_button": "Buy Now"}}
    items_legacy = fe_legacy.from_page(page)
    items_kw = fe_kw.from_page(page)
    assert len(items_legacy) == len(items_kw)
    for legacy, kw in zip(items_legacy, items_kw):
        assert legacy.canonical_role == kw.canonical_role
        assert legacy.type == kw.type


def test_embedding_classifier_fallback_when_no_prototypes():
    """EmbeddingRoleClassifier falls back to keyword when prototypes empty."""
    def dummy_embed(text):
        return (0.1,) * 16
    ec = EmbeddingRoleClassifier(embedding_fn=dummy_embed)
    role, item_type = ec.classify("product_detail", "buy_button", "Buy Now button")
    # Should fall back to keyword classifier
    assert role == "open_spec_selector"
    assert item_type == "functionality"


def test_embedding_classifier_with_prototypes():
    embeddings = {}

    def embed(text):
        if text not in embeddings:
            import hashlib
            h = hashlib.md5(text.encode()).hexdigest()
            embeddings[text] = tuple(int(h[i:i+2], 16) / 255.0 for i in range(0, 32, 2))
        return embeddings[text]

    ec = EmbeddingRoleClassifier(embedding_fn=embed, role_threshold=0.3)
    # Register a prototype
    proto_emb = embed("product_detail buy_button Buy Now button")
    ec.register_verified_role("open_spec_selector", proto_emb)

    # Classify with similar text
    role, _ = ec.classify("product_detail", "buy_button", "Buy Now button")
    assert role == "open_spec_selector"


def test_embedding_classifier_deduplicates_near_identical():
    def embed(text):
        return (0.5,) * 8

    ec = EmbeddingRoleClassifier(embedding_fn=embed)
    proto = (0.5,) * 8
    ec.register_verified_role("test_role", proto)
    ec.register_verified_role("test_role", proto)  # duplicate
    assert ec.prototype_count()["test_role"] == 1  # deduplicated
