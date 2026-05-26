# AMSG v3 Dry-Run Report

## Summary

- Domain: `shopping`
- App filter: `淘宝`
- Artifacts: `17`
- Screenshots/pages: `54`
- Candidate edge hypotheses: `90`
- Promoted edges: `12` / transitions seen `12`
- Valid edge ratio: `1.0`
- Safe schema coverage: `0.9231`

## Schema Coverage

- Observed: cart, checkout, dialog, filter_panel, home, payment, product_detail, search_input, search_result, spec_selection
- Missing safe page types: settings

## Active Frontier Samples

### search_input: 搜索输入页
- `search_input` -> `search_result` via `submit_search` / `submit_search`, score `2.1`, risk `normal`

### cart: 购物车列表
- `cart` -> `checkout` via `checkout` / `checkout`, score `0.9`, risk `high`

### filter_panel: 商品筛选面板
- `filter_panel` -> `search_result` via `apply_filter` / `apply_filter`, score `2.1`, risk `normal`
- `filter_panel` -> `detail` via `close_dialog` / `close_dialog`, score `2.1`, risk `normal`

### search_input: 搜索输入页
- `search_input` -> `search_result` via `submit_search` / `submit_search`, score `2.1`, risk `normal`

### search_result: 商品搜索结果
- `search_result` -> `product_detail` via `open_product` / `open_product`, score `2.1`, risk `normal`
- `search_result` -> `filter_panel` via `open_filter` / `open_filter`, score `2.1`, risk `normal`

### filter_panel: 商品筛选面板
- `filter_panel` -> `search_result` via `apply_filter` / `apply_filter`, score `2.1`, risk `normal`
- `filter_panel` -> `detail` via `close_dialog` / `close_dialog`, score `2.1`, risk `normal`

### spec_selection: 商品规格选择
- `spec_selection` -> `product_detail` via `confirm_add_to_cart` / `confirm_spec`, score `2.1`, risk `normal`
- `spec_selection` -> `cart` via `confirm_spec` / `confirm_spec`, score `2.1`, risk `normal`
- `spec_selection` -> `detail` via `close_dialog` / `close_dialog`, score `2.1`, risk `normal`

### search_input: 搜索输入页
- `search_input` -> `search_result` via `submit_search` / `submit_search`, score `2.1`, risk `normal`

### filter_panel: 商品筛选面板
- `filter_panel` -> `search_result` via `apply_filter` / `apply_filter`, score `2.1`, risk `normal`
- `filter_panel` -> `detail` via `close_dialog` / `close_dialog`, score `2.1`, risk `normal`

### cart: 购物车列表
- `cart` -> `checkout` via `checkout` / `checkout`, score `0.9`, risk `high`

### search_result: 商品搜索结果
- `search_result` -> `product_detail` via `open_product` / `open_product`, score `2.1`, risk `normal`
- `search_result` -> `filter_panel` via `open_filter` / `open_filter`, score `2.1`, risk `normal`

### cart: 购物车列表
- `cart` -> `checkout` via `checkout` / `checkout`, score `0.9`, risk `high`
