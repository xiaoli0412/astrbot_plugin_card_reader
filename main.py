"""astrbot_plugin_card_reader —— 读 QQ 分享卡片，注入给 LLM，而不是复述给用户。

核心链路：
1. `@filter.event_message_type(ALL)` 早早抓到 `json` 消息段，解析成结构化卡片；
2. 卡片消息的 `message_str` 是空的，会导致主 Agent 直接跳过 LLM，
   因此这里补一个中性占位文本让它进入正常流程；
3. `@filter.on_llm_request(priority=210)` 把卡片内容用 `TextPart.mark_as_temp()`
   追加到 `req.extra_user_content_parts`，只给模型看、不落历史；
4. 顺带拦掉 `astrbot_plugin_bilibili` 那种「标题: … 链接: …」的旧式直发回显，
   避免用户收到一串裸链接。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
from collections import deque
from typing import Any

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from astrbot.api import AstrBotConfig, logger  # noqa: E402
from astrbot.api.event import AstrMessageEvent, filter  # noqa: E402
from astrbot.api.star import Context, Star, register  # noqa: E402
from astrbot.core.agent.message import TextPart  # noqa: E402
from astrbot.core.message.components import Json, Reply  # noqa: E402
from astrbot.core.star.filter.command import GreedyStr  # noqa: E402
from astrbot.core.star.star_handler import EventType, star_handlers_registry  # noqa: E402

from cardreader.parse import parse_card  # noqa: E402
from cardreader.render import render_block  # noqa: E402

PLUGIN_NAME = "astrbot_plugin_card_reader"
EVENT_EXTRA_KEY = "_card_reader_cards"
DEFAULT_PLACEHOLDER = "（对方分享了一张卡片）"

# 旧式回显：astrbot_plugin_bilibili 的 parse_miniapp 会直接发这两行
_ECHO_RE = re.compile(
    r"^\s*标题\s*[:：][\s\S]{0,300}?链接\s*[:：]\s*https?://\S+\s*$"
)


def _now() -> str:
    return datetime.datetime.now().strftime("%m-%d %H:%M:%S")


def _iter_components(components: Any, depth: int = 0):
    """遍历消息组件，兼容引用消息里的嵌套链。"""
    if depth > 3 or not components:
        return
    for comp in components:
        yield comp
        if isinstance(comp, Reply):
            chain = getattr(comp, "chain", None)
            if chain:
                yield from _iter_components(chain, depth + 1)


def extract_cards_from_event(event: AstrMessageEvent) -> list[dict[str, Any]]:
    """从事件里提取所有可解析的卡片。"""
    cards: list[dict[str, Any]] = []
    try:
        components = event.message_obj.message
    except Exception:
        return cards
    for comp in _iter_components(components):
        if isinstance(comp, Json):
            parsed = parse_card(getattr(comp, "data", None))
            if parsed:
                cards.append(parsed)
    return cards


def _message_plain_text(message: Any) -> str:
    """把 MessageChain / str 粗略转成纯文本，用于判定旧式回显。"""
    if isinstance(message, str):
        return message
    parts: list[str] = []
    chain = getattr(message, "chain", None) or []
    for comp in chain:
        text = getattr(comp, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


@register(
    PLUGIN_NAME,
    "xiaoli0412",
    "读取 QQ 分享卡片（B站/小红书/音乐/小程序），把标题和链接注入给 LLM，而不是复述给用户",
    "0.1.0",
    "",
)
class CardReaderPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | dict | None = None):
        super().__init__(context)
        self.cfg: dict[str, Any] = dict(config or {})
        self._recent: deque[dict[str, Any]] = deque(maxlen=20)
        self._data_dir = self._resolve_data_dir()
        self._state_path = os.path.join(self._data_dir, "recent.json")
        self._load_recent()
        self._apply_priority_from_config()

    # ---------------- 基础工具 ----------------

    def _resolve_data_dir(self) -> str:
        try:
            from astrbot.core.star.star_tools import StarTools

            return str(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:
            fallback = os.path.join(PLUGIN_DIR, ".data")
            os.makedirs(fallback, exist_ok=True)
            return fallback

    def _apply_priority_from_config(self) -> None:
        """让 inject_priority 配置项真正生效（默认装饰器值是 210）。"""
        try:
            priority = int(self._cfg("inject_priority", 210))
        except (TypeError, ValueError):
            return
        if priority == 210:
            return
        try:
            for handler in star_handlers_registry.get_handlers_by_event_type(
                EventType.OnLLMRequestEvent,
                only_activated=False,
            ):
                if (
                    handler.handler_name == "on_llm_request"
                    and handler.handler_module_path == __name__
                ):
                    handler.extras_configs["priority"] = priority
            star_handlers_registry._handlers.sort(
                key=lambda h: -h.extras_configs.get("priority", 0)
            )
            logger.info(f"[card_reader] 注入优先级已设为 {priority}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[card_reader] 设置注入优先级失败，沿用默认 210: {e}")

    def _cfg(self, key: str, default: Any = None) -> Any:
        value = self.cfg.get(key, default)
        return default if value is None else value

    def _source_enabled(self, kind: str) -> bool:
        sources = self._cfg("sources", {}) or {}
        if not isinstance(sources, dict):
            return True
        node = sources.get(kind)
        if isinstance(node, dict):
            return bool(node.get("enable", True))
        if isinstance(node, bool):
            return node
        return True

    def _log_path(self) -> str:
        name = str(self._cfg("unknown_structure_log_path", "card_unknown.log") or "")
        if not name:
            name = "card_unknown.log"
        if os.path.isabs(name):
            return name
        return os.path.join(self._data_dir, name)

    def _append_log(self, tag: str, payload: Any) -> None:
        try:
            if isinstance(payload, str):
                text = payload
            else:
                text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        except Exception:
            text = repr(payload)
        block = f"\n===== [{datetime.datetime.now().isoformat(timespec='seconds')}] {tag} =====\n{text}\n"
        path = self._log_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(block)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[card_reader] 写日志失败 {path}: {e}")

    def _load_recent(self) -> None:
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._recent.extend([x for x in data if isinstance(x, dict)][-20:])
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[card_reader] 读取最近记录失败: {e}")

    def _save_recent(self) -> None:
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(list(self._recent), f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[card_reader] 保存最近记录失败: {e}")

    def _remember(self, card: dict[str, Any], unknown: bool = False) -> None:
        self._recent.append(
            {
                "ts": _now(),
                "kind": card.get("kind"),
                "title": card.get("title"),
                "url": card.get("url"),
                "matched_path": card.get("matched_path"),
                "unknown": unknown,
            }
        )
        self._save_recent()

    # ---------------- 早抓：解析 + 放行 + 拦截旧回显 ----------------

    @filter.event_message_type(filter.EventMessageType.ALL, priority=300)
    async def capture_cards(self, event: AstrMessageEvent):
        if not bool(self._cfg("enabled", True)):
            return

        raw_components: list[Any] = []
        try:
            raw_components = list(event.message_obj.message)
        except Exception:
            return

        json_components = [c for c in _iter_components(raw_components) if isinstance(c, Json)]
        if not json_components:
            return

        max_cards = int(self._cfg("max_cards_per_turn", 3) or 3)
        cards: list[dict[str, Any]] = []
        for comp in json_components:
            raw = getattr(comp, "data", None)
            if bool(self._cfg("log_raw_structure", True)):
                self._append_log("RAW", raw)
            parsed = parse_card(raw)
            if not parsed:
                self._append_log("UNKNOWN_STRUCTURE", raw)
                logger.warning(f"[card_reader] 未识别的卡片结构: {str(raw)[:300]}")
                continue
            if not self._source_enabled(str(parsed.get("kind"))):
                logger.info(f"[card_reader] 来源已禁用，跳过: {parsed.get('kind')}")
                continue
            cards.append(parsed)
            self._remember(parsed)

        if not cards:
            return

        cards = cards[: max(1, max_cards)]
        event.set_extra(EVENT_EXTRA_KEY, cards)
        logger.info(
            f"[card_reader] 捕获 {len(cards)} 张卡片: "
            + "; ".join(f"{c['kind']}:{c['title'][:20]}" for c in cards)
        )

        # 纯卡片消息 message_str 为空，主 Agent 会直接跳过 LLM —— 补中性占位
        placeholder = str(
            self._cfg("empty_card_placeholder", DEFAULT_PLACEHOLDER)
            or DEFAULT_PLACEHOLDER
        )
        try:
            if not (event.message_str or "").strip():
                event.message_str = placeholder
                if getattr(event, "message_obj", None) is not None:
                    event.message_obj.message_str = placeholder
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[card_reader] 设置占位文本失败: {e}")

        # 拦截“标题: … 链接: …”旧式直发，既不打扰用户，也不阻断主 LLM
        if bool(self._cfg("suppress_legacy_echo", True)):
            self._install_send_guard(event)

    # ------------------------------------------------ WebUI（PATCHED local）
    async def initialize(self):
        """注册 WebUI 卡片解析面板 API。"""
        try:
            reg = getattr(self.context, "register_web_api", None)
            if reg:
                reg("astrbot_plugin_card_reader/overview", self.api_overview,
                    ["GET"], "卡片读取：解析记录")
                logger.info("[card_reader] WebUI API 已注册 1 条")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[card_reader] WebUI API 注册失败：%s", exc)

    async def api_overview(self):
        """卡片解析面板数据。"""
        out = []
        for r in list(self._recent)[::-1]:
            out.append({
                "ts": r.get("ts") or "",
                "kind": r.get("kind") or "?",
                "title": str(r.get("title") or "")[:80],
                "url": str(r.get("url") or "")[:180],
                "matched": str(r.get("matched_path") or r.get("matched") or ""),
                "injected": bool(r.get("injected")),
            })
        return {
            "ok": True,
            "data": {
                "enabled": bool(self._cfg("enabled", True)),
                "suppress_legacy_echo": bool(self._cfg("suppress_legacy_echo", True)),
                "inject_hint": bool(self._cfg("inject_hint", True)),
                "max_cards_per_turn": int(self._cfg("max_cards_per_turn", 3)),
                "records": out,
                "total": len(self._recent),
            },
        }

    def _install_send_guard(self, event: AstrMessageEvent) -> None:
        if getattr(event, "_card_reader_send_guard", False):
            return
        original_send = getattr(event, "send", None)
        if original_send is None:
            return

        plugin = self

        async def _guarded_send(message, *args, **kwargs):
            try:
                text = _message_plain_text(message)
            except Exception:
                text = ""
            if text and _ECHO_RE.match(text):
                plugin._append_log("SUPPRESSED_LEGACY_ECHO", text)
                logger.info(f"[card_reader] 已拦截旧式卡片回显: {text[:80]!r}")
                return None
            return await original_send(message, *args, **kwargs)

        try:
            event.send = _guarded_send  # type: ignore[method-assign]
            event._card_reader_send_guard = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[card_reader] 安装发送守卫失败: {e}")

    # ---------------- 注入给 LLM（临时内容，不落历史） ----------------

    @filter.on_llm_request(priority=210)
    async def on_llm_request(self, event: AstrMessageEvent, req: Any):
        if not bool(self._cfg("enabled", True)):
            return

        cards = event.get_extra(EVENT_EXTRA_KEY)
        if not cards:
            cards = extract_cards_from_event(event)
            if cards:
                cards = [
                    c
                    for c in cards
                    if self._source_enabled(str(c.get("kind")))
                ][: int(self._cfg("max_cards_per_turn", 3) or 3)]
        if not cards:
            return

        try:
            block = render_block(cards, hint=bool(self._cfg("inject_hint", True)))
            if not block:
                return
            part = TextPart(text=block)
            part.mark_as_temp()
            req.extra_user_content_parts.append(part)
            logger.info(
                f"[card_reader] 已注入 {len(cards)} 张卡片（temp），会话 {event.unified_msg_origin}"
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"[card_reader] 注入卡片失败: {e}", exc_info=True)

    # ---------------- 指令 ----------------

    @filter.command("卡片")
    async def cmd_card(self, event: AstrMessageEvent, arg: GreedyStr):
        """查看配置与最近解析记录；`/卡片 测试 <json或URL>` 手工验证解析器。"""
        text = str(arg or "").strip()
        if text.startswith("测试"):
            payload = text[len("测试") :].strip()
            async for result in self._cmd_test(event, payload):
                yield result
            return

        sources = self._cfg("sources", {}) or {}
        src_bits = []
        for kind, label in (
            ("bilibili", "B站"),
            ("xiaohongshu", "小红书"),
            ("miniapp", "小程序"),
        ):
            node = sources.get(kind) if isinstance(sources, dict) else None
            enabled = True
            if isinstance(node, dict):
                enabled = bool(node.get("enable", True))
            elif isinstance(node, bool):
                enabled = node
            src_bits.append(f"{label}={'开' if enabled else '关'}")

        lines = [
            "【卡片阅读器】",
            f"启用：{'是' if self._cfg('enabled', True) else '否'} | "
            f"注入优先级：{self._cfg('inject_priority', 210)} | "
            f"每轮上限：{self._cfg('max_cards_per_turn', 3)}",
            f"来源：{' '.join(src_bits)}",
            f"提示词：{'开' if self._cfg('inject_hint', True) else '关'} | "
            f"原始结构日志：{'开' if self._cfg('log_raw_structure', True) else '关'} | "
            f"拦截旧回显：{'开' if self._cfg('suppress_legacy_echo', True) else '关'}",
            f"未知结构日志：{self._log_path()}",
        ]
        if self._recent:
            lines.append("")
            lines.append(f"最近解析（{len(self._recent)} 条）：")
            for idx, rec in enumerate(list(self._recent)[-10:], 1):
                lines.append(
                    f"{idx}. [{rec.get('kind')}] {rec.get('title') or '(无标题)'} "
                    f"-> {rec.get('url') or '(无链接)'} ({rec.get('matched_path')})"
                )
        else:
            lines.append("")
            lines.append("最近还没有解析到卡片。")
        yield event.plain_result("\n".join(lines))

    async def _cmd_test(self, event: AstrMessageEvent, payload: str):
        if not payload:
            yield event.plain_result("用法：/卡片 测试 <json原文或URL>")
            return

        raw: Any
        if payload.startswith("http://") or payload.startswith("https://"):
            raw = {"meta": {"detail_1": {"qqdocurl": payload}}}
        else:
            try:
                raw = json.loads(payload)
            except Exception as e:  # noqa: BLE001
                yield event.plain_result(f"JSON 解析失败：{type(e).__name__}: {e}")
                return

        result = parse_card(raw)
        if not result:
            yield event.plain_result(
                "解析失败：parse_card 返回 None（结构未识别，已按设置写入未知结构日志）"
            )
            return
        pretty = json.dumps(result, ensure_ascii=False, indent=2)
        yield event.plain_result(f"解析成功：\n{pretty}\n\n注入预览：\n{render_block([result])}")
