"""AppRegistry: canonical app identity resolution."""

from phone_agent.spatial.app_registry import AppRegistry, AppRecord, get_default_app_registry


def test_resolves_display_name_package_and_legacy_alias():
    registry = get_default_app_registry()

    assert registry.canonical_id("淘宝") == "taobao"
    assert registry.canonical_id("天猫") == "taobao"
    assert registry.canonical_id("com.taobao.taobao") == "taobao"
    assert registry.canonical_id("Taobao") == "taobao"
    assert registry.canonical_id("京东") == "jd"
    assert registry.canonical_id("com.jingdong.app.mall") == "jd"


def test_unknown_app_falls_back_to_slug():
    registry = get_default_app_registry()

    assert registry.canonical_id("SomeNewApp") == "somenewapp"
    assert registry.domain_of("SomeNewApp") == "unknown"
    assert registry.schema_for("SomeNewApp") == "common_mobile"


def test_domain_and_schema_mapping():
    registry = get_default_app_registry()

    assert registry.domain_of("淘宝") == "shopping"
    assert registry.schema_for("拼多多") == "shopping"


def test_storage_aliases_include_raw_and_known_variants():
    registry = get_default_app_registry()

    aliases = registry.storage_aliases("淘宝")
    assert "淘宝" in aliases
    assert "taobao" in aliases
    assert "com.taobao.taobao" in aliases

    # Unregistered apps still match their own nodes.
    assert registry.storage_aliases("NewApp") == ("NewApp",)


def test_infer_from_text_returns_raw_token():
    registry = get_default_app_registry()

    assert registry.infer_from_text("在淘宝搜索耳机") == "淘宝"
    assert registry.infer_from_text("no app mentioned here") == ""
    assert registry.infer_domain_from_text("打开京东首页") == "shopping"


def test_register_and_resolve_in_memory():
    registry = AppRegistry()
    record = AppRecord(
        app_id="xiaohongshu",
        domain="social",
        schema="common_mobile",
        package_names=("com.xingin.xhs",),
        display_names=("小红书",),
    )
    registry.register(record)

    assert registry.canonical_id("小红书") == "xiaohongshu"
    assert registry.canonical_id("com.xingin.xhs") == "xiaohongshu"
    assert "小红书" in registry.mention_tokens()
