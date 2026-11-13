"""cardreader —— 卡片解析与注入文本组装（纯函数，无 AstrBot 依赖）。"""

from cardreader.parse import parse_card
from cardreader.render import render_block

__all__ = ["parse_card", "render_block"]
