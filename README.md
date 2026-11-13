# astrbot_plugin_card_reader

读取 QQ 里的分享卡片（B站 / 小红书 / 音乐 / 小程序），把标题和链接**注入给 LLM**，而不是把「标题：… 链接：…」复述给用户。

## 它解决什么问题

QQ 分享卡片在 AstrBot 里是 `[ComponentType.Json]` 消息段，`message_str` 是空字符串：

- 主 Agent 拿不到任何可读内容，要么不回复，要么把卡片文字照念一遍；
- `astrbot_plugin_bilibili` 的 `parse_miniapp` 会直接把 `标题: xxx\n链接: xxx` 发给用户，多出一条裸链接。

本插件在消息进入 LLM 前解析卡片，用 `TextPart(text=...).mark_as_temp()` 追加到 `req.extra_user_content_parts`：

- 只给模型看，**不写入对话历史**；
- 模型能拿链接去搜索、自然接话；
- 同时拦掉旧式「标题/链接」直发消息（可关）。

## 安装

把整个目录放到 `AstrBot/data/plugins/astrbot_plugin_card_reader/`，重启 AstrBot。

无新增第三方依赖。

## 配置

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | true | 总开关 |
| `inject_priority` | 210 | `on_llm_request` 优先级，高于 persona_conductor(200) |
| `inject_hint` | true | 是否附带「不要复述标题链接」等行为提示 |
| `max_cards_per_turn` | 3 | 单条消息最多处理几张卡片 |
| `log_raw_structure` | true | 完整记录收到的 Json 原始结构 |
| `unknown_structure_log_path` | card_unknown.log | 未识别结构日志（相对插件数据目录） |
| `sources.bilibili.enable` | true | B站卡片开关 |
| `sources.xiaohongshu.enable` | true | 小红书卡片开关 |
| `sources.miniapp.enable` | true | 其它小程序卡片开关 |
| `suppress_legacy_echo` | true | 拦截 `astrbot_plugin_bilibili` 的「标题/链接」直发 |
| `empty_card_placeholder` | （对方分享了一张卡片） | 纯卡片消息的占位文本，用于让主 Agent 进入 LLM 流程 |

## 指令

- `/卡片` —— 查看配置与最近解析记录
- `/卡片 测试 <json原文或URL>` —— 手工测试解析器，不用真发卡片

示例：

```
/卡片 测试 https://www.bilibili.com/video/BV18q4j6KE6L
/卡片 测试 {"app":"com.tencent.miniapp_01","prompt":"[QQ小程序]标题","meta":{"detail_1":{"title":"哔哩哔哩","desc":"视频标题","qqdocurl":"https://www.bilibili.com/video/BVxxx"}}}
```

## 解析覆盖的字段路径

按优先级尝试：

1. `meta.detail_1.title` / `.desc` / `.qqdocurl` / `.url` / `.preview`
2. `meta.news.title` / `.desc` / `.jumpUrl` / `.preview`
3. `meta.music.title` / `.desc` / `.jumpUrl`
4. `meta.video.*`
5. `prompt`（兜底标题，去掉 `[QQ小程序]` 前缀）
6. 顶层 `url` / `jumpUrl`

来源识别：URL 含 `bilibili.com` / `b23.tv` → `bilibili`；含 `xiaohongshu.com` / `xhslink.com` → `xiaohongshu`；`app` 含 `miniapp` → `miniapp`；音乐结构 → `music`；否则 `unknown`。

未识别结构会完整写入 `card_unknown.log`，方便后续补路径。

## 目录结构

```
astrbot_plugin_card_reader/
├── main.py
├── _conf_schema.json
├── metadata.yaml
├── README.md
├── LICENSE
└── cardreader/
    ├── __init__.py
    ├── parse.py     # 纯函数解析器，可单测
    └── render.py    # 注入文本组装
```

## License

MIT
