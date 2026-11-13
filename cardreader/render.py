"""把解析后的卡片组装成注入给 LLM 的自然语言块。"""

from __future__ import annotations

from typing import Any

KIND_NOUN = {
    "bilibili": "哔哩哔哩视频",
    "xiaohongshu": "小红书笔记",
    "music": "音乐",
    "miniapp": "小程序卡片",
    "unknown": "分享卡片",
}

DEFAULT_HINT = (
    "对方只是分享，没有直接提问。你可以：\n"
    "1. 就这个内容自然接一句（看到标题有什么反应）；\n"
    "2. 用搜索工具查一下再回复；\n"
    "3. 不要复述标题和链接，不要输出「标题：… 链接：…」这种格式。"
)


def _card_lines(card: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    title = str(card.get("title") or "").strip()
    url = str(card.get("url") or "").strip()
    desc = str(card.get("desc") or "").strip()
    if title:
        lines.append(f"- 标题：{title}")
    if url:
        lines.append(f"- 链接：{url}")
    if desc and desc != title:
        lines.append(f"- 描述：{desc}")
    return lines


def render_block(cards: list[dict[str, Any]], hint: bool = True) -> str:
    """组装注入文本。多张卡片时按序号排列。"""
    if not cards:
        return ""

    lines: list[str] = ["<shared_card>"]
    if len(cards) == 1:
        card = cards[0]
        noun = KIND_NOUN.get(str(card.get("kind")), "分享卡片")
        lines.append(f"对方分享了一个{noun}：")
        lines.extend(_card_lines(card))
    else:
        lines.append(f"对方分享了 {len(cards)} 张卡片：")
        for idx, card in enumerate(cards, 1):
            noun = KIND_NOUN.get(str(card.get("kind")), "分享卡片")
            lines.append(f"{idx}. {noun}")
            for line in _card_lines(card):
                lines.append(f"   {line}")

    lines.append("")
    if hint:
        lines.append(DEFAULT_HINT)
    else:
        lines.append("对方只是分享，没有直接提问。")
    lines.append("</shared_card>")
    return "\n".join(lines)
