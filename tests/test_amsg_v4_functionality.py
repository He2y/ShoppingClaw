import json

from phone_agent.spatial.coverage_metrics import compute_functionality_coverage
from phone_agent.spatial.exploration_queue import ExplorationQueueBuilder
from phone_agent.spatial.functionality import FunctionalityExtractor
from phone_agent.spatial.functionality_cluster import FunctionalityCluster, FunctionalityClusterer
from phone_agent.spatial.reporting import build_amsg_v4_functionality_report, format_amsg_v4_markdown
from phone_agent.spatial import task_synthesis


def test_functionality_extractor_marks_verified_transition_postcondition():
    transition = {
        "from": "product_detail:product detail",
        "to": "cart:cart list",
        "action": {"action": "Tap", "element": [850, 70]},
    }

    item = FunctionalityExtractor().from_transition(transition, app="Taobao")

    assert item.type == "functionality"
    assert item.is_verified
    assert item.observed_postcondition == "cart"
    assert item.expected_effect == "cart"
    assert item.region == "top_right"
    assert item.canonical_role == "open_cart_from_header"
    assert item.label == "open_cart_from_header"


def test_functionality_clusterer_merges_similar_verified_edges_without_presets():
    extractor = FunctionalityExtractor()
    open_cart_a = extractor.from_transition(
        {
            "from": "product_detail:product detail A",
            "to": "cart:cart list",
            "action": {"action": "Tap", "element": [850, 70]},
        },
        app="Taobao",
    )
    open_cart_b = extractor.from_transition(
        {
            "from": "product_detail:product detail B",
            "to": "cart:cart list",
            "action": {"action": "Tap", "element": [860, 72]},
        },
        app="Taobao",
    )
    open_spec = extractor.from_transition(
        {
            "from": "product_detail:product detail A",
            "to": "spec_selection:spec dialog",
            "action": {"action": "Tap", "element": [520, 930]},
        },
        app="Taobao",
    )

    _, clusters = FunctionalityClusterer().cluster([open_cart_a, open_cart_b, open_spec])

    assert len(clusters) == 2
    assert sorted(cluster.success_count for cluster in clusters) == [1, 2]
    assert any(cluster.canonical_name == "open_cart_from_header" for cluster in clusters)
    assert any(cluster.canonical_name == "open_spec_selector" for cluster in clusters)


def test_functionality_clusterer_separates_same_target_from_different_sources():
    extractor = FunctionalityExtractor()
    open_detail = extractor.from_transition(
        {
            "from": "search_result:result list",
            "to": "product_detail:detail",
            "action": {"action": "Tap", "element": [500, 420]},
        },
        app="Taobao",
    )
    close_spec = extractor.from_transition(
        {
            "from": "spec_selection:spec dialog",
            "to": "product_detail:detail",
            "action": {"action": "Back"},
        },
        app="Taobao",
    )

    _, clusters = FunctionalityClusterer().cluster([open_detail, close_spec])

    assert len(clusters) == 2
    assert sorted(cluster.page_types[0] for cluster in clusters) == ["search_result", "spec_selection"]


def test_functionality_coverage_is_discovery_based_not_bucket_based():
    extractor = FunctionalityExtractor()
    verified = extractor.from_transition(
        {
            "from": "search_result:result list",
            "to": "product_detail:detail",
            "action": {"action": "Tap", "element": [500, 400]},
        },
        app="Taobao",
    )
    unverified = extractor.from_page(
        {
            "app": "Taobao",
            "page_type": "search_result",
            "summary": "result list",
            "elements": {"filter_button": "Open filters"},
        }
    )[0]
    items, clusters = FunctionalityClusterer().cluster([verified, unverified])

    metrics = compute_functionality_coverage(
        items=items,
        clusters=clusters,
        screenshot_count=2,
        screen_cluster_count=1,
    ).to_dict()

    assert metrics["discovered_functionality_clusters"] == len(clusters)
    assert metrics["verified_functionality_clusters"] == 1
    assert metrics["verified_functionality_ratio"] == round(1 / len(clusters), 4)
    assert "target_buckets" not in metrics


def test_visible_fallback_is_not_promoted_functionality():
    items = FunctionalityExtractor().from_page(
        {
            "app": "Taobao",
            "page_type": "search_result",
            "summary": "result list with no parsed elements",
            "elements": {},
        }
    )

    assert not [item for item in items if item.label.endswith("visible functions")]
    _, clusters = FunctionalityClusterer().cluster(items)
    assert clusters == []


def test_page_extractor_separates_controls_and_data_items():
    items = FunctionalityExtractor().from_page(
        {
            "app": "Taobao",
            "page_type": "product_detail",
            "summary": "detail",
            "elements": {
                "top_cart_icon": "Open cart from header",
                "price": "¥63",
                "product_title": "phone case",
            },
        }
    )

    assert any(item.type == "functionality" and item.canonical_role == "open_cart_from_header" for item in items)
    assert any(item.type == "data" and item.canonical_role == "price" for item in items)
    assert any(item.type == "data" and item.canonical_role == "product_title" for item in items)


def test_search_result_query_is_data_not_submit_functionality():
    items = FunctionalityExtractor().from_page(
        {
            "app": "Taobao",
            "page_type": "search_result",
            "summary": "result list",
            "elements": {
                "query": "headphones",
                "filter_button": "Open filters",
            },
        }
    )

    query_item = next(item for item in items if item.label == "query")
    assert query_item.type == "data"
    assert query_item.is_promotable is False
    assert query_item.canonical_role == "page_data"
    assert any(item.type == "functionality" and item.canonical_role == "open_filter_panel" for item in items)


def test_safe_filter_recovery_cluster_not_marked_high_risk_by_address_text():
    item = FunctionalityExtractor().from_transition(
        {
            "from": "filter_panel:Filter panel with address data",
            "to": "search_result:Product results",
            "action": {"action": "Back", "semantic_target": "rollback_to_search_result"},
        },
        app="Taobao",
    )

    _, clusters = FunctionalityClusterer().cluster([item])

    assert clusters[0].canonical_name == "apply_or_close_filter"
    assert clusters[0].risk_level == "normal"


def test_unverified_functionality_does_not_cluster_across_page_types():
    extractor = FunctionalityExtractor()
    home_item = extractor.from_page(
        {
            "app": "Taobao",
            "page_type": "home",
            "summary": "home",
            "elements": {"search_bar": "search entry"},
        }
    )[0]
    search_item = extractor.from_page(
        {
            "app": "Taobao",
            "page_type": "search_input",
            "summary": "input",
            "elements": {"search_bar": "submit search"},
        }
    )[0]

    _, clusters = FunctionalityClusterer().cluster([home_item, search_item])

    assert len(clusters) == 2
    assert sorted(cluster.page_types[0] for cluster in clusters) == ["home", "search_input"]


def test_exploration_queue_targets_unverified_safe_clusters_only():
    safe_cluster = FunctionalityCluster(
        cluster_id="safe",
        canonical_name="open filter",
        canonical_description="Open a filter panel",
        member_functionality_ids=("fn1",),
        regions=("top_right",),
        risk_level="normal",
    )
    high_risk_cluster = FunctionalityCluster(
        cluster_id="risk",
        canonical_name="pay order",
        canonical_description="Submit payment",
        member_functionality_ids=("fn2",),
        regions=("bottom_right",),
        risk_level="high",
    )

    jobs = ExplorationQueueBuilder().build_jobs([safe_cluster, high_risk_cluster])

    assert len(jobs) == 1
    assert jobs[0].functionality_cluster_id == "safe"
    assert "payment" in jobs[0].forbidden_actions


def test_amsg_v4_report_discovers_verified_clusters_from_artifacts(tmp_path, monkeypatch):
    _disable_real_env(monkeypatch)
    pages_path = tmp_path / "taobao_explore_1.json"
    pages_path.write_text(
        json.dumps(
            {
                "app": "Taobao",
                "pages": [
                    {
                        "app": "Taobao",
                        "page_type": "product_detail",
                        "summary": "product detail with top cart icon and bottom CTA",
                        "elements": {
                            "top_cart_icon": "Open cart from the detail header",
                            "bottom_cta": "Open spec selection before adding to cart",
                            "price": "$9.99",
                        },
                        "screenshot_hash": "detail-1",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    transitions_path = tmp_path / "taobao_transitions_1.json"
    transitions_path.write_text(
        json.dumps(
            {
                "app": "Taobao",
                "transitions": [
                    {
                        "from": "product_detail:product detail",
                        "to": "cart:cart list",
                        "action": {"action": "Tap", "element": [850, 70]},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_amsg_v4_functionality_report(exploration_root=tmp_path, app_filter=None)
    markdown = format_amsg_v4_markdown(report)

    assert report["functionality_coverage"]["discovered_functionality_clusters"] >= 1
    assert report["functionality_coverage"]["verified_functionality_clusters"] >= 1
    assert report["functionality_coverage"]["functionality_semantics_quality"] == "limited"
    assert report["functionality_type_counts"]["data"] >= 1
    assert any(cluster["verified_edges"] for cluster in report["functionality_clusters"])
    assert not any("visible functions" in cluster["canonical_name"] for cluster in report["functionality_clusters"])
    assert not [
        cluster
        for cluster in report["functionality_clusters"]
        if cluster["success_count"] == 0 and len(cluster["page_types"]) >= 3
    ]
    assert report["functionality_items"]
    assert all("app" in item and "page_type" in item for item in report["functionality_items"])
    assert "AMSG v4 Self-Discovered Functionality Report" in markdown


def test_strong_vlm_config_prefers_amsg_env_and_masks_key(monkeypatch):
    _disable_real_env(monkeypatch)
    monkeypatch.setenv("AMSG_STRONG_VLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AMSG_STRONG_VLM_MODEL", "strong-vlm")
    monkeypatch.setenv("AMSG_STRONG_VLM_API_KEY", "secret")
    task_synthesis._ENV_LOADED = False

    config = task_synthesis.resolve_strong_vlm_config()
    data = config.to_dict()

    assert config.source == "amsg_strong_vlm"
    assert config.configured
    assert data["api_key"] == "***"
    assert data["configured"] is True


def _disable_real_env(monkeypatch):
    for key in (
        "AMSG_STRONG_VLM_BASE_URL",
        "AMSG_STRONG_VLM_MODEL",
        "AMSG_STRONG_VLM_API_KEY",
        "OFFLINE_VLM_BASE_URL",
        "OFFLINE_VLM_MODEL",
        "OFFLINE_VLM_API_KEY",
        "PHONE_AGENT_BASE_URL",
        "PHONE_AGENT_MODEL",
        "PHONE_AGENT_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(task_synthesis, "load_dotenv", lambda: None)
    task_synthesis._ENV_LOADED = False
