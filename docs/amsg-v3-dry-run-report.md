# AMSG v3 Dry-Run Report

## Summary

- Domain: `shopping`
- App filter: `Taobao`
- Artifacts: `11`
- Screenshots/pages: `43`
- Candidate edge hypotheses: `63`
- Promoted edges: `20` / transitions seen `46`
- Valid edge ratio: `0.4348`
- Safe schema coverage: `0.9231`

## Schema Coverage

- Observed: cart, checkout, filter_panel, home, product_detail, search_input, search_result, spec_selection, unknown
- Missing safe page types: settings

## Active Frontier Samples

### home: 电商首页推荐流
- `home` -> `search` via `open_search` / `open_search`, score `2.1`, risk `normal`
- `home` -> `search_input` via `open_search` / `open_search`, score `2.1`, risk `normal`

### search_input: 搜索输入及推荐页
- `search_input` -> `search_result` via `submit_search` / `submit_search`, score `2.1`, risk `normal`

### search_result: 肠炎宁片搜索结果列表
- `search_result` -> `product_detail` via `open_product` / `open_product`, score `2.1`, risk `normal`
- `search_result` -> `filter_panel` via `open_filter` / `open_filter`, score `2.1`, risk `normal`
- `search_result` -> `detail` via `open_detail` / `open_detail`, score `2.1`, risk `normal`

### product_detail: 药品商品详情页
- `product_detail` -> `spec_selection` via `open_spec` / `open_spec`, score `2.1`, risk `normal`

### product_detail: 药品商品详情展示页
- `product_detail` -> `spec_selection` via `open_spec` / `open_spec`, score `2.1`, risk `normal`

### spec_selection: 商品规格与数量选择
- `spec_selection` -> `cart` via `confirm_spec` / `confirm_spec`, score `2.1`, risk `normal`
- `spec_selection` -> `detail` via `close_dialog` / `close_dialog`, score `2.1`, risk `normal`

### cart: 购物车商品列表及优惠
- `cart` -> `detail` via `open_detail` / `open_detail`, score `2.1`, risk `normal`
- `cart` -> `checkout` via `checkout` / `checkout`, score `0.9`, risk `high`

### search_result: 商品搜索结果
- `search_result` -> `product_detail` via `open_product` / `open_product`, score `2.1`, risk `normal`
- `search_result` -> `filter_panel` via `open_filter` / `open_filter`, score `2.1`, risk `normal`

### spec_selection: 商品规格选择
- `spec_selection` -> `cart` via `confirm_spec` / `confirm_spec`, score `2.1`, risk `normal`
- `spec_selection` -> `detail` via `close_dialog` / `close_dialog`, score `2.1`, risk `normal`

### search_input: 搜索输入页
- `search_input` -> `search_result` via `submit_search` / `submit_search`, score `2.1`, risk `normal`

### filter_panel: 商品筛选面板
- `filter_panel` -> `search_result` via `apply_filter` / `apply_filter`, score `2.1`, risk `normal`
- `filter_panel` -> `detail` via `close_dialog` / `close_dialog`, score `2.1`, risk `normal`

### product_detail: 商品详情页
- `product_detail` -> `spec_selection` via `open_spec` / `open_spec`, score `2.1`, risk `normal`
