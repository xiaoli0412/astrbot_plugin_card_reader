"""parse_card 单元测试（纯标准库 unittest，容器内可直接跑）。

运行：
    cd astrbot_plugin_card_reader && python -m unittest tests.test_parse -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from cardreader.parse import parse_card  # noqa: E402


class TestParseCard(unittest.TestCase):
    def test_bilibili_detail1_with_qqdocurl(self):
        raw = {
            "app": "com.tencent.miniapp_01",
            "ver": "1.0.0.19",
            "prompt": "[QQ小程序]你们知道，佛其实也会杀人吗？",
            "meta": {
                "detail_1": {
                    "appid": "1109937557",
                    "title": "哔哩哔哩",
                    "desc": "你们知道，佛其实也会杀人吗？",
                    "preview": "https://i0.hdslb.com/bfs/archive/demo.jpg",
                    "qqdocurl": "https://www.bilibili.com/video/BV18q4j6KE6L",
                    "url": "m.q.qq.com/a/s/demo",
                }
            },
        }
        card = parse_card(raw)
        self.assertIsNotNone(card)
        self.assertEqual(card["kind"], "bilibili")
        self.assertEqual(card["title"], "你们知道，佛其实也会杀人吗？")
        self.assertEqual(card["url"], "https://www.bilibili.com/video/BV18q4j6KE6L")
        self.assertEqual(card["source"], "哔哩哔哩")
        self.assertEqual(card["matched_path"], "meta.detail_1.qqdocurl")

    def test_bilibili_napcat_real_ark_without_qqdocurl(self):
        """实测 NapCat v4.18.37 get_mini_app_ark 返回的真实结构（没有 qqdocurl）。"""
        raw = {
            "ver": "1.0.0.19",
            "prompt": "[QQ小程序]测试卡片标题",
            "config": {"type": "normal", "width": 0, "height": 0},
            "app": "com.tencent.miniapp_01",
            "view": "view_8C8E89B49BE609866298ADDFF2DBABA4",
            "meta": {
                "detail_1": {
                    "appid": "1109937557",
                    "appType": 0,
                    "title": "哔哩哔哩",
                    "desc": "测试卡片标题",
                    "icon": "http://miniapp.gtimg.cn/public/appicon/x.jpg",
                    "preview": "https://qq.ugcimg.cn/v1/demo",
                    "url": "m.q.qq.com/a/s/1189be39ee0542bd9a2374fe5b048b05",
                    "scene": 0,
                }
            },
            "miniappShareOrigin": 3,
        }
        card = parse_card(raw)
        self.assertIsNotNone(card)
        self.assertEqual(card["kind"], "bilibili")
        self.assertEqual(card["title"], "测试卡片标题")
        self.assertEqual(card["url"], "m.q.qq.com/a/s/1189be39ee0542bd9a2374fe5b048b05")
        self.assertEqual(card["matched_path"], "meta.detail_1.url")

    def test_xiaohongshu(self):
        raw = {
            "app": "com.tencent.miniapp_01",
            "meta": {
                "detail_1": {
                    "title": "小红书",
                    "desc": "春日氛围感穿搭｜三套直接抄",
                    "qqdocurl": "https://xhslink.com/a/AbCdEf",
                    "url": "m.q.qq.com/a/s/xhs",
                }
            },
        }
        card = parse_card(raw)
        self.assertIsNotNone(card)
        self.assertEqual(card["kind"], "xiaohongshu")
        self.assertEqual(card["title"], "春日氛围感穿搭｜三套直接抄")
        self.assertEqual(card["url"], "https://xhslink.com/a/AbCdEf")
        self.assertEqual(card["source"], "小红书")

    def test_music(self):
        raw = {
            "app": "com.tencent.music",
            "meta": {
                "music": {
                    "title": "反乌托邦",
                    "desc": "洛天依",
                    "jumpUrl": "https://y.qq.com/n/ryqq/songDetail/xxx",
                    "preview": "https://y.gtimg.cn/demo.jpg",
                }
            },
        }
        card = parse_card(raw)
        self.assertIsNotNone(card)
        self.assertEqual(card["kind"], "music")
        self.assertEqual(card["title"], "反乌托邦")
        self.assertEqual(card["desc"], "洛天依")
        self.assertEqual(card["url"], "https://y.qq.com/n/ryqq/songDetail/xxx")
        self.assertEqual(card["source"], "音乐")

    def test_news_bilibili(self):
        raw = {
            "app": "com.tencent.news",
            "meta": {
                "news": {
                    "title": "某视频标题",
                    "desc": "某UP主",
                    "jumpUrl": "https://b23.tv/AbCdEf",
                    "preview": "https://i0.hdslb.com/demo.jpg",
                }
            },
        }
        card = parse_card(raw)
        self.assertIsNotNone(card)
        self.assertEqual(card["kind"], "bilibili")
        self.assertEqual(card["title"], "某视频标题")
        self.assertEqual(card["url"], "https://b23.tv/AbCdEf")
        self.assertEqual(card["matched_path"], "meta.news.jumpUrl")

    def test_unknown_structure_returns_none(self):
        self.assertIsNone(parse_card({"app": "com.tencent.other", "meta": {"foo": {"bar": 1}}}))
        self.assertIsNone(parse_card({"foo": "bar"}))

    def test_empty_inputs_return_none(self):
        for value in (None, "", "   ", {}, [], "not a json"):
            with self.subTest(value=value):
                self.assertIsNone(parse_card(value))

    def test_prompt_fallback(self):
        card = parse_card({"app": "com.tencent.miniapp_01", "prompt": "[QQ小程序]一个没有 meta 的卡片"})
        self.assertIsNotNone(card)
        self.assertEqual(card["title"], "一个没有 meta 的卡片")
        self.assertEqual(card["matched_path"], "prompt")

    def test_wrapped_and_string_input(self):
        inner = {
            "app": "com.tencent.miniapp_01",
            "meta": {"detail_1": {"title": "哔哩哔哩", "desc": "包装测试", "qqdocurl": "https://www.bilibili.com/video/BV1xx411c7mD"}},
        }
        for raw in (
            {"data": inner},
            {"data": json.dumps(inner, ensure_ascii=False)},
            json.dumps(inner, ensure_ascii=False),
        ):
            with self.subTest(raw_type=type(raw).__name__):
                card = parse_card(raw)
                self.assertIsNotNone(card)
                self.assertEqual(card["title"], "包装测试")
                self.assertEqual(card["kind"], "bilibili")

    def test_top_level_url_only(self):
        card = parse_card({"url": "https://www.bilibili.com/video/BV1xx411c7mD"})
        self.assertIsNotNone(card)
        self.assertEqual(card["kind"], "bilibili")
        self.assertEqual(card["title"], "")

    def test_result_keys_are_stable(self):
        card = parse_card(
            {"meta": {"detail_1": {"title": "哔哩哔哩", "desc": "标题", "qqdocurl": "https://b23.tv/x"}}}
        )
        self.assertEqual(
            set(card.keys()),
            {"kind", "title", "url", "desc", "source", "matched_path"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
