"""Shopping scenario configuration — externalized with JSON + defaults."""

from dataclasses import dataclass, field
from pathlib import Path
import json


@dataclass
class ShoppingConfig:
    """Shopping scenario configuration with JSON-override support."""

    apps: set[str] = field(default_factory=set)
    platforms: set[str] = field(default_factory=set)
    spec_keywords: set[str] = field(default_factory=set)
    purchase_keywords: set[str] = field(default_factory=set)

    @classmethod
    def default(cls) -> "ShoppingConfig":
        """Built-in defaults (works without config file)."""
        return cls(
            apps={
                "淘宝", "京东", "天猫", "拼多多", "美团", "饿了么",
                "瑞幸", "星巴克", "叮咚买菜", "盒马",
                "抖音商城", "小红书", "唯品会", "得物",
            },
            platforms={
                "京东", "京东商城", "jd.com", "jd",
                "淘宝", "taobao", "天猫", "tmall",
                "拼多多", "pinduoduo",
                "小红书", "xiaohongshu",
                "唯品会", "vipshop",
                "苏宁易购", "suning",
                "抖音商城", "douyin mall",
                "得物", "dewu",
                "美团", "meituan",
                "饿了么", "eleme",
            },
            spec_keywords={
                "规格", "颜色", "容量", "尺码", "口味", "温度", "糖度",
                "浓度", "选配", "机身颜色", "存储容量", "版本", "型号", "尺寸",
            },
            purchase_keywords={
                "领券购买", "立即购买", "加入购物车", "提交订单",
                "立刻抢", "马上抢", "确认下单", "结算", "支付",
            },
        )

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "ShoppingConfig":
        """Load config from JSON, falling back to defaults on any failure."""
        default = cls.default()

        if config_path is None:
            config_path = Path("config/shopping.json")

        if not Path(config_path).exists():
            print(f"⚠️ 配置文件不存在: {config_path}，使用默认值")
            return default

        try:
            with open(config_path, encoding="utf-8") as f:
                data = json.load(f)

            return cls(
                apps=set(data.get("apps", default.apps)),
                platforms=set(data.get("platforms", default.platforms)),
                spec_keywords=set(data.get("spec_keywords", default.spec_keywords)),
                purchase_keywords=set(data.get("purchase_keywords", default.purchase_keywords)),
            )
        except Exception as e:
            print(f"⚠️ 加载配置失败: {e}，使用默认值")
            return default
