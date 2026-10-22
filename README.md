# astrbot_plugin_web_bottle

**AstrBot 漂流瓶插件 · 内置 Web 审核台**

在聊天里扔漂流瓶、捡漂流瓶、评论、点赞；用户提交的瓶子与评论先进入审核队列，
通过后才会出现在大海里。审核台直接内嵌在 AstrBot WebUI 的插件详情页中，
不需要额外开端口、不需要再登录一次。

> 本插件由 [nonebot-plugin-web-bottle](https://github.com/luosheng520qaq/nonebot-plugin-web-bottle)
> 迁移而来，按 AstrBot 插件规范重写，并新增 WebUI 可视化视图。

---

## ✨ 功能

- **聊天指令**：扔漂流瓶（文字 + 图片）、捡漂流瓶、评论漂流瓶、点赞漂流瓶、按编号查看。
- **WebUI 审核台**：在 AstrBot WebUI 里审核漂流瓶与评论，支持图片预览/放大、快捷键、实时统计。
- **审核流程**：`待审核 → 通过 / 拒绝`；拒绝时会自动清理该瓶子的图片缓存。
- **多平台**：消息统一走 AstrBot 消息链，文本 + 图片的平台都能用（OneBot v11 / QQ 官方 / Telegram / Discord 等，具体取决于平台是否支持图片）。
- **数据安全**：数据保存在 AstrBot 的 `data/plugin_data/` 下，更新或重装插件不会丢数据。

## 📦 安装

1. 打开 AstrBot WebUI → **插件** → **安装插件**，上传本目录打包的 zip（或直接把文件夹放到 `AstrBot/data/plugins/`）。
2. 在插件页点击 **重载插件**。
3. 安装时会自动检查 `requirements.txt` 中的依赖（`httpx`、`pillow`，AstrBot 通常已自带）。

加载成功后，插件卡片上会出现可视化视图入口。

## 💬 聊天指令

> 指令前缀可在插件配置里改：
> - **指令必须带前缀**（`require_prefix`）默认开启，开启时用 **插件指令前缀**（`command_prefix`，默认 `/`）触发；
> - 关闭开关后，**不需要 `/`**，直接发送「扔漂流瓶 ……」也能触发；
> - AstrBot 的全局「唤醒前缀」以及 @ 机器人始终有效。

| 指令 | 说明 |
| --- | --- |
| `扔漂流瓶 [文字]`（别名 `丢瓶子`、`扔瓶子`） | 把瓶子扔进大海，可同时附带图片，等待审核 |
| `捡漂流瓶`（别名 `捡瓶子`、`捞瓶子`） | 随机捞一个已通过审核的瓶子 |
| `评论漂流瓶 [编号] [内容]` | 给漂流瓶留言，审核通过后展示 |
| `点赞漂流瓶 [编号]` | 给喜欢的瓶子点赞（同一用户只能点一次） |
| `查看漂流瓶 [编号]` | 按编号查看漂流瓶（会提示未审核 / 已拒绝） |
| `漂流瓶`（别名 `漂流瓶菜单`、`漂流瓶帮助`） | 查看帮助 |

扔漂流瓶 / 捡漂流瓶按用户独立计算冷却，默认 6 秒。

## 🖥️ WebUI 审核台

打开 **WebUI → 插件 → 漂流瓶 · Web 审核台 → 可视化视图**，进入「漂流瓶审核台」：

- **顶部统计**：待审瓶子、待审评论、已通过瓶子、已通过评论。
- **瓶子审核**：显示编号、内容、发送者、来源群聊、时间、图片（点击可放大），
  一键「通过 / 拒绝 / 跳过」。
- **评论审核**：显示评论编号、所属瓶子、评论人、内容，一键「通过 / 拒绝 / 跳过」。
- **快捷键**：`A` 通过、`R` 拒绝、`N` / `→` 下一条（输入框聚焦时不生效）。
- 自动跟随 WebUI 的浅色 / 深色主题。

审核台使用 AstrBot Dashboard 的登录态，只有能进入 WebUI 的管理员才能访问。

> 审核台依赖 AstrBot 的「插件可视化视图」能力（`views/` 目录 + `context.register_web_api`）。
> 如果当前 AstrBot 版本较旧、不支持该能力，插件仍会正常加载，聊天指令全部可用，
> 只是插件详情页里没有审核台入口，日志中会有对应提示。

### 查看不到审核台？

按顺序检查：

1. **版本**：需要支持「插件可视化视图」的 AstrBot（`context.register_web_api` + `views/` 扫描）。
   启动日志里搜「漂流瓶审核台视图已就绪」；如果看到「不支持插件可视化视图」或
   「未找到审核台视图文件」，说明版本过旧或文件缺失。
2. **目录结构**：安装后应该是
   `data/plugins/astrbot_plugin_web_bottle/views/review/index.html`，
   不能在插件目录里再多套一层同名文件夹。
3. **重载 + 刷新**：在插件页点「重载插件」，再刷新 WebUI 页面；入口在
   插件卡片 → 插件详情页 →「可视化视图」。
4. 插件详情页还打不开的话，去 WebUI 的「日志」里搜插件名，把报错发出来即可定位。

## ⚙️ 配置

在 WebUI 的插件配置页可视化修改，配置会保存在 `data/config/astrbot_plugin_web_bottle_config.json`。

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `require_prefix` | bool | `true` | 指令是否必须带前缀；关闭后无需 `/` 即可触发 |
| `command_prefix` | string | `/` | 插件指令前缀，可改成 `!`、`#` 等 |
| `max_bottle_pic` | int | `2` | 单条漂流瓶最多图片数量 |
| `max_bottle_word` | int | `1200` | 单条漂流瓶 / 评论最大字符数 |
| `max_bottle_lines` | int | `9` | 单条漂流瓶最大文字行数（空行不计） |
| `embedded_help` | bool | `true` | 扔漂流瓶没写内容时是否附带帮助 |
| `cooling_time` | int | `6` | 扔漂流瓶 / 捡漂流瓶冷却秒数，`0` 为不限制 |
| `default_nickname` | string | `未知昵称` | 取不到昵称时的默认称呼 |
| `bottle_msg_split` | bool | `true` | 正文与评论区分两条消息发送 |
| `max_bottle_comments` | int | `3` | 捡漂流瓶时最多展示的评论条数 |
| `show_sender_name` | bool | `true` | 是否展示发送者昵称 |
| `show_group_name` | bool | `true` | 是否展示来源群聊 |

## 🗂️ 数据存储

```text
AstrBot/data/plugin_data/astrbot_plugin_web_bottle/
├─ bottle.db      # SQLite 数据库：pending / approved / comments / user_up
├─ bottle_id.txt  # 瓶子编号计数器
└─ img/
   └─ <瓶子ID>/
      └─ 0.webp   # 瓶子图片，统一压缩为 WebP
```

备份这个目录即可完整备份漂流瓶数据；删除它相当于清空所有瓶子。

## 🔁 从 NoneBot 版迁移数据

本插件沿用原版数据库结构（新增的 `username` / `groupname` 列会在启动时自动补齐），
所以可以直接复用旧数据：

1. 停止 AstrBot；
2. 找到 NoneBot 版的数据目录（`plugin-localstore` 的 data 目录下的 `nonebot_plugin_web_bottle/`）；
3. 把其中的 `bottle.db`、`bottle_id.txt`、`img/` 复制到
   `AstrBot/data/plugin_data/astrbot_plugin_web_bottle/`；
4. 启动 AstrBot，重载插件。

旧数据的发送者昵称会显示为默认称呼（首次迁移时没有记录昵称），之后的瓶子会正常记录。

## 📁 目录结构

```text
astrbot_plugin_web_bottle/
├─ main.py                     # 插件入口：指令、数据层、Web API
├─ metadata.yaml               # 插件元信息
├─ _conf_schema.json           # WebUI 可视化配置定义
├─ requirements.txt            # 依赖
├─ logo.png                    # 插件 Logo
├─ README.md
├─ LICENSE.txt
├─ views/
│  └─ review/                  # WebUI 审核台（插件可视化视图）
│     ├─ index.html
│     ├─ app.js
│     └─ style.css
└─ .astrbot-plugin/
   └─ i18n/
      ├─ zh-CN.json
      └─ en-US.json
```

## 🔧 与 NoneBot 版的差异

- **不再自带 Web 服务**：原版的 FastAPI 站点、登录页、Cookie 会话全部移除，
  审核台改为 AstrBot WebUI 的插件可视化视图，复用 Dashboard 登录态。
- **不再依赖 OneBot 专用接口**：发送者昵称、群聊信息在扔漂流瓶时记录；
  评论 / 点赞直接使用 AstrBot 事件里的用户 ID。
- **移除 QQ Markdown / Gensokyo 适配**：由 AstrBot 各平台适配器负责消息格式。
- **配置改为 Schema**：不再写 `pyproject.toml` / NoneBot 配置项，改用 `_conf_schema.json`。
- **图片仍然压缩**：统一转成 WebP（动图保留动画）后再存盘，节省空间。

## ❓ 常见问题

**审核台入口看不到？**
见上文「查看不到审核台？」的四步排查。

**指令可以不带 `/` 吗？**
可以。到插件配置里关闭「指令必须带前缀」（`require_prefix`），之后直接发「扔漂流瓶 内容」即可；
想换前缀就把 `command_prefix` 改成 `!` 等。

**图片显示不出来？**
图片是在扔漂流瓶时从消息平台下载并压缩保存的，若当时下载失败会跳过该图，
日志中会有「有一张漂流瓶图片未能下载」的提示。

**群聊显示成一串数字？**
部分平台不会下发群名称，插件会回退显示群号，可在配置中关闭「展示来源群聊」。

**审核台是否需要单独登录？**
不需要，也不能绕过 WebUI 登录：页面在 Dashboard 的受限 iframe 中打开，只有已登录的管理员可见。

## 📝 更新记录

- **v1.1.0**：新增「指令必须带前缀」开关与可修改的插件指令前缀；启动时输出审核台与视图文件状态日志，便于排查 WebUI 不显示的问题。
- **v1.0.0**：从 NoneBot 版迁移到 AstrBot，审核台改为插件可视化视图。

## 🙏 致谢与许可

- 原作：[nonebot-plugin-web-bottle](https://github.com/luosheng520qaq/nonebot-plugin-web-bottle)（作者 Xican）
- 本项目为其 AstrBot 迁移版，遵循 MIT License，见 [LICENSE.txt](./LICENSE.txt)。
