"""不依赖 QQ/网络的本地链路测试：直接调用插件 handler。

在 AstrBot 容器内运行：
    cd /AstrBot/data/plugins/astrbot_plugin_card_reader && python tests/test_handlers.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

os.environ.setdefault("ASTRBOT_ROOT", "/AstrBot")

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from astrbot.core.agent.message import TextPart  # noqa: E402
from astrbot.core.message.components import Json  # noqa: E402

import main as plugin_main  # noqa: E402


class FakeMessageObj:
    def __init__(self, comps):
        self.message = comps
        self.message_str = ""


class FakeEvent:
    def __init__(self, comps, text=""):
        self.message_obj = FakeMessageObj(comps)
        self.message_str = text
        self.unified_msg_origin = "default:FriendMessage:999"
        self._extras = {}
        self.sent = []

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    async def send(self, message):
        self.sent.append(message)


class FakeReq:
    def __init__(self):
        self.prompt = ""
        self.extra_user_content_parts = []
        self.system_prompt = ""


ARK = {
    "app": "com.tencent.miniapp_01",
    "ver": "1.0.0.19",
    "prompt": "[QQ小程序]你们知道，佛其实也会杀人吗？",
    "meta": {
        "detail_1": {
            "appid": "1109937557",
            "title": "哔哩哔哩",
            "desc": "你们知道，佛其实也会杀人吗？",
            "qqdocurl": "https://www.bilibili.com/video/BV18q4j6KE6L",
            "url": "m.q.qq.com/a/s/demo",
        }
    },
}


def build_plugin():
    plugin = plugin_main.CardReaderPlugin.__new__(plugin_main.CardReaderPlugin)
    plugin.cfg = {
        "enabled": True,
        "inject_hint": True,
        "max_cards_per_turn": 3,
        "log_raw_structure": False,
        "unknown_structure_log_path": "/tmp/card_reader_test_unknown.log",
        "sources": {
            "bilibili": {"enable": True},
            "xiaohongshu": {"enable": True},
            "miniapp": {"enable": True},
        },
        "suppress_legacy_echo": True,
        "empty_card_placeholder": "（对方分享了一张卡片）",
    }
    plugin._recent = __import__("collections").deque(maxlen=20)
    plugin._data_dir = "/tmp"
    plugin._state_path = "/tmp/card_reader_recent_test.json"
    return plugin


async def run():
    plugin = build_plugin()
    event = FakeEvent([Json(data=ARK)])
    req = FakeReq()

    # 1) 早抓：占位 + 捕获
    await plugin.capture_cards(event)
    assert event.message_str == "（对方分享了一张卡片）", event.message_str
    cards = event.get_extra(plugin_main.EVENT_EXTRA_KEY)
    assert cards and cards[0]["kind"] == "bilibili", cards
    assert cards[0]["url"] == "https://www.bilibili.com/video/BV18q4j6KE6L"

    # 2) 注入：temp part，不进历史
    await plugin.on_llm_request(event, req)
    assert len(req.extra_user_content_parts) == 1
    part = req.extra_user_content_parts[0]
    assert isinstance(part, TextPart)
    dumped = part.model_dump_for_context()
    assert dumped["_no_save"] is True, dumped
    assert "<shared_card>" in dumped["text"]
    assert "BV18q4j6KE6L" in dumped["text"]
    print("OK: 捕获/占位/注入 全部通过")
    print("---- 注入文本 ----")
    print(dumped["text"])

    # 3) 旧式回显拦截
    from astrbot.api.event import MessageChain

    before = len(event.sent)
    await event.send(MessageChain().message("标题: 你们知道，佛其实也会杀人吗？\n链接: https://www.bilibili.com/video/BV18q4j6KE6L"))
    assert len(event.sent) == before, "旧式回显未被拦截"
    await event.send(MessageChain().message("正常的回复"))
    assert len(event.sent) == before + 1, "正常消息被误伤"
    print("OK: 旧式回显已拦截，正常消息放行")

    # 4) 未知结构：不注入
    plugin2 = build_plugin()
    ev2 = FakeEvent([Json(data={"foo": "bar"})])
    req2 = FakeReq()
    await plugin2.capture_cards(ev2)
    assert ev2.get_extra(plugin_main.EVENT_EXTRA_KEY) is None
    await plugin2.on_llm_request(ev2, req2)
    assert req2.extra_user_content_parts == []
    print("OK: 未知结构不注入")


if __name__ == "__main__":
    asyncio.run(run())
