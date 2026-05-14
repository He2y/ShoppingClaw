"""
Product data model for unified session state.

Merges ProductObservation (KnowledgeBase) + ProductInfo (SessionMemory)
into a single Product dataclass.
"""

from dataclasses import dataclass, field
from enum import Enum


class ProductStatus(str, Enum):
    VIEWED = "viewed"
    COMPARED = "compared"
    ADDED_TO_CART = "added_to_cart"
    PURCHASED = "purchased"


@dataclass
class Product:
    """A product observed during the shopping session."""

    name: str
    price: float | None = None
    currency: str = "¥"
    specs: dict[str, str] = field(default_factory=dict)
    source_page: str = ""  # "search_result" / "product_detail" / "cart" / "checkout"
    status: ProductStatus = ProductStatus.VIEWED
    first_seen_step: int = 0
    screenshot_desc: str = ""

    def update_from(self, other: "Product") -> None:
        """Merge newer observation into this product (mutates in-place)."""
        if other.price is not None:
            self.price = other.price
        if other.specs:
            self.specs.update(other.specs)
        if other.source_page:
            self.source_page = other.source_page
        if other.status != ProductStatus.VIEWED:
            self.status = other.status

    @property
    def price_display(self) -> str:
        if self.price is not None:
            return f"{self.currency}{self.price}"
        return "价格未知"

    @property
    def specs_display(self) -> str:
        if not self.specs:
            return ""
        return ", ".join(f"{k}={v}" for k, v in self.specs.items())

    @property
    def status_cn(self) -> str:
        _map = {
            ProductStatus.VIEWED: "已看",
            ProductStatus.COMPARED: "对比中",
            ProductStatus.ADDED_TO_CART: "已加购",
            ProductStatus.PURCHASED: "已购买",
        }
        return _map.get(self.status, self.status.value)


def product_name_match(a: str, b: str) -> bool:
    """Fuzzy product name match — case-insensitive, whitespace-normalized."""
    a_norm = "".join(a.lower().split())
    b_norm = "".join(b.lower().split())
    if a_norm == b_norm:
        return True
    if len(a_norm) > 4 and len(b_norm) > 4:
        if a_norm in b_norm or b_norm in a_norm:
            return True
    return False
