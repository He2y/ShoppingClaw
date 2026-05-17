#!/usr/bin/env python
"""Test qwen3.6-plus for page classification."""

import os
import sys
import base64
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI
from PIL import Image
from io import BytesIO


def test_qwen36_plus():
    """Test if qwen3.6-plus follows the prompt format correctly."""
    print("=" * 60)
    print("Qwen3.6-Plus Page Classification Test")
    print("=" * 60)

    api_key = os.environ.get("OFFLINE_VLM_API_KEY")
    base_url = os.environ.get("OFFLINE_VLM_BASE_URL")
    model = os.environ.get("OFFLINE_VLM_MODEL")

    print(f"\nConfiguration:")
    print(f"  Model: {model}")
    print(f"  Base URL: {base_url}")

    # Load test image
    image_path = "tmp/taobao_home.png"
    if not Path(image_path).exists():
        print(f"\n❌ Test image not found: {image_path}")
        return False

    print(f"\n📸 Testing with image: {image_path}")
    with open(image_path, "rb") as f:
        screenshot_b64 = base64.b64encode(f.read()).decode()

    # Crop image (same as PageClassifier)
    img_data = base64.b64decode(screenshot_b64)
    img = Image.open(BytesIO(img_data))
    width, height = img.size

    crop_top = int(height * 0.08)
    crop_bottom = int(height * 0.88)

    cropped = img.crop((0, crop_top, width, crop_bottom))
    buf = BytesIO()
    cropped.save(buf, format="PNG")
    cropped_b64 = base64.b64encode(buf.getvalue()).decode()

    print(f"  Original: {width}x{height}, Cropped: {width}x{crop_bottom-crop_top}")

    # Test with exact PageClassifier prompt
    system_prompt = (
        "你是一个移动应用页面分类器。识别购物App当前显示的页面类型，并列出页面中的关键交互元素。\n\n"
        "**重要**: 截图已经裁剪掉了顶部状态栏和底部导航栏（首页/购物车/我的等Tab）。\n"
        "你只能看到页面的主内容区域。请仅根据主内容区域判断页面类型，不要猜测被裁剪掉的部分。\n\n"
        "=== 页面类型定义 ===\n"
        "- home: 首页 — Banner轮播图、推荐商品网格、搜索框入口、活动入口图标\n"
        "- search_input: 搜索输入页 — 搜索框已激活(有光标)、键盘已弹出、显示搜索历史或热门搜索词\n"
        "- search_result: 搜索结果页 — 商品卡片列表、顶部有搜索框(未激活)、筛选/排序按钮(价格/销量/综合)\n"
        "- product_detail: 商品详情页 — 单个商品大图、价格(¥符号)、商品名称、加入购物车/立即购买按钮\n"
        "- spec_selection: 规格选择 — 弹窗或半屏面板、颜色/尺寸/容量等选项按钮、数量选择器、显示价格\n"
        "- cart: 购物车 — 商品列表每项带圆形复选框、有全选按钮、有结算/去结算按钮、有编辑/管理按钮\n"
        "- checkout: 结算/订单确认 — 收货地址、支付方式选择、商品清单、提交订单按钮\n"
        "- category: 分类页 — 左侧一级分类列表+右侧子分类网格、或分类图标网格布局\n"
        "- my_account: 个人中心 — 用户头像区域、订单入口(待付款/待发货/待收货)、优惠券/收藏/足迹等入口\n"
        "- store: 店铺主页 — 店铺Logo和名称、店铺评分、店铺内商品列表、关注按钮\n"
        "- login: 登录页 — 手机号输入框、密码输入框、登录按钮、验证码、第三方登录图标\n"
        "- unknown: 以上都不匹配或无法判断\n\n"
        "=== elements 字段说明 ===\n"
        "列出页面中可见的关键交互元素，用简短中文命名。只列功能性组件（按钮、输入框、列表、选择器等），\n"
        "不要列纯展示内容（文字、图片）。命名要通用化，不要包含具体商品名或价格。\n"
        "示例: {\"search_bar\": \"顶部搜索栏\", \"product_cards\": \"商品卡片列表\", \"filter_buttons\": \"筛选排序按钮\"}\n\n"
        "=== summary 字段说明 ===\n"
        "用不超过15个字概括页面功能，不要包含具体商品名/品牌名/价格。\n"
        "示例: \"商品搜索结果列表\" 而非 \"OPPO Find X9手机搜索结果页\"\n\n"
        "=== 输出格式 ===\n"
        '严格输出JSON，不要加任何额外文字：\n'
        '{"page_type": "<类型>", "summary": "<≤15字功能概括>", "elements": {"元素名": "简短描述"}}'
    )

    print(f"\n📡 Calling API with PageClassifier prompt...")
    try:
        client = OpenAI(base_url=base_url, api_key=api_key)

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{cropped_b64}"},
                        },
                        {"type": "text", "text": "请分类这个页面"},
                    ],
                }
            ],
            max_tokens=500,
            temperature=0.0,
        )

        raw = response.choices[0].message.content or ""
        print(f"\n  ✅ API responded")
        print(f"\n  Raw response ({len(raw)} chars):")
        print(f"  {'-' * 50}")
        print(f"  {raw}")
        print(f"  {'-' * 50}")

        # Try to parse
        print(f"\n  Parsing response...")
        cleaned = raw.strip()

        # Remove markdown if present
        if cleaned.startswith("```"):
            first_newline = cleaned.find("\n")
            if first_newline != -1:
                cleaned = cleaned[first_newline + 1:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        try:
            result = json.loads(cleaned)
            print(f"\n  ✅ JSON parsed successfully!")
            print(f"     Page type: {result.get('page_type', 'N/A')}")
            print(f"     Summary: {result.get('summary', 'N/A')}")
            print(f"     Elements: {len(result.get('elements', {}))} items")

            if result.get('elements'):
                print(f"\n  Elements:")
                for name, desc in list(result['elements'].items())[:5]:
                    print(f"    - {name}: {desc}")

            # Validate format
            required_keys = ['page_type', 'summary', 'elements']
            if all(k in result for k in required_keys):
                print(f"\n  ✅ Response format is correct!")
                return True
            else:
                print(f"\n  ⚠️ Missing keys: {set(required_keys) - set(result.keys())}")
                return False

        except json.JSONDecodeError as e:
            print(f"\n  ❌ JSON parse failed: {e}")
            return False

    except Exception as e:
        print(f"\n❌ API call failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_qwen36_plus()
    sys.exit(0 if success else 1)
