"""卡片解析：把 OneBot `json` 消息段里的分享卡片解析成统一结构。

设计原则：
- 纯函数，不依赖 AstrBot，可单独单测；
- 兼容多种历届结构（detail_1 / news / music / video / 顶层 url / prompt）；
- 解析不出来返回 None，绝不抛异常。
"""

from __future__ import annotations

import json
import re
from typing import Any

# 常见“卡片标题其实是应用名”的值，遇到时优先把 desc 当标题
APP_NAME_HINTS = {
    "哔哩哔哩",
    "bilibili",
    "小红书",
    "QQ音乐",
    "qq音乐",
    "QQ看点",
    "QQ小程序",
    "微信",
    "微视",
    "抖音",
    "网易云音乐",
    "酷狗音乐",
}

SOURCE_LABELS = {
    "bilibili": "哔哩哔哩",
    "xiaohongshu": "小红书",
    "music": "音乐",
    "miniapp": "QQ小程序",
    "unknown": "分享卡片",
}

_PROMPT_PREFIX_RE = re.compile(r"^\s*\[[^\]]{0,20}\]\s*")


def _as_dict(raw: Any, depth: int = 0) -> dict[str, Any] | None:
    """把各种包装形态统一成 dict。"""
    if depth > 4:
        return None
    if isinstance(raw, dict):
        # 兼容 {"data": {...}} / {"data": "{...json...}"} 这类二次包装
        if "data" in raw and not any(
            k in raw for k in ("app", "meta", "ver", "prompt", "url", "jumpUrl")
        ):
            return _as_dict(raw.get("data"), depth + 1)
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return None
        return _as_dict(parsed, depth + 1)
    if isinstance(raw, (list, tuple)):
        for item in raw:
            got = _as_dict(item, depth + 1)
            if got:
                return got
    return None


def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _first(*values: Any) -> str:
    for v in values:
        s = _text(v)
        if s:
            return s
    return ""


def _detect_kind(
    url: str,
    app: str,
    block: str,
    appid: str = "",
    title: str = "",
) -> str:
    u = (url or "").lower()
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    if "xiaohongshu.com" in u or "xhslink.com" in u:
        return "xiaohongshu"
    if appid == "1109937557" or (title or "").strip().lower() in {"哔哩哔哩", "bilibili"}:
        return "bilibili"
    if block == "music":
        return "music"
    if "miniapp" in (app or "").lower():
        return "miniapp"
    return "unknown"


def _clean_title(title: str, desc: str) -> str:
    """title 是应用名时用 desc 顶替。"""
    t = (title or "").strip()
    d = (desc or "").strip()
    if t and t in APP_NAME_HINTS:
        return d or t
    if not t and d:
        return d
    return t


def _parse_block(
    node: Any,
    base_path: str,
    block_name: str,
    app: str,
    appid: str = "",
) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None

    title_raw = _first(node.get("title"), node.get("name"))
    desc_raw = _first(node.get("desc"), node.get("description"), node.get("summary"))
    url = ""
    url_path = ""
    for key in ("qqdocurl", "jumpUrl", "jump_url", "url", "musicUrl", "music_url"):
        val = _text(node.get(key))
        if val:
            url = val
            url_path = f"{base_path}.{key}"
            break
    preview = _first(node.get("preview"), node.get("picUrl"), node.get("icon"))
    title = _clean_title(title_raw, desc_raw)

    if not (title or desc_raw or url):
        return None

    kind = _detect_kind(url, app, block_name, appid, title_raw)
    return {
        "kind": kind,
        "title": title,
        "url": url,
        "desc": desc_raw if desc_raw != title else "",
        "source": SOURCE_LABELS[kind],
        "matched_path": url_path or (f"{base_path}.title" if title else f"{base_path}.desc"),
        "_preview": preview,
    }


def parse_card(raw: Any) -> dict[str, Any] | None:
    """解析 OneBot json 卡片。

    返回 {"kind", "title", "url", "desc", "source", "matched_path"}，失败返回 None。
    """
    obj = _as_dict(raw)
    if not obj:
        return None

    app = _first(obj.get("app"), obj.get("appid"), obj.get("app_name"))
    top_appid = _first(obj.get("appid"))

    # 1) meta 下的几种标准结构，按常见程度排序
    for block_name in ("detail_1", "news", "music", "video", "detail"):
        node = _dig(obj, f"meta.{block_name}")
        block_appid = _first(
            node.get("appid") if isinstance(node, dict) else None, top_appid
        )
        parsed = _parse_block(node, f"meta.{block_name}", block_name, app, block_appid)
        if parsed:
            return _finish(parsed)

    # 2) prompt 兜底标题（去掉 [QQ小程序] 之类前缀）
    prompt = _text(obj.get("prompt"))
    if prompt:
        prompt = _PROMPT_PREFIX_RE.sub("", prompt).strip()
    top_url = _first(
        obj.get("qqdocurl"),
        obj.get("jumpUrl"),
        obj.get("jump_url"),
        obj.get("url"),
    )
    if prompt or top_url:
        kind = _detect_kind(top_url, app, "", top_appid, "")
        if not prompt and top_url and "miniapp" in (app or "").lower():
            kind = "miniapp"
        return _finish(
            {
                "kind": kind,
                "title": prompt,
                "url": top_url,
                "desc": "",
                "source": SOURCE_LABELS[kind],
                "matched_path": "prompt" if prompt else "url",
                "_preview": "",
            }
        )

    return None


def _finish(card: dict[str, Any]) -> dict[str, Any]:
    card.pop("_preview", None)
    if not card.get("title") and not card.get("url"):
        return None
    return card


def is_valid_card(raw: Any) -> bool:
    return parse_card(raw) is not None
