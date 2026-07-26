# youtube-to-obsidian

将 YouTube 视频转换为可追溯的 Obsidian 笔记和经过浏览器验收的单文件 HTML。
当前版本：**v0.2.0**

这个仓库既是可部署到 Codex/Hermes 的 Skill，也是独立的字幕提取、HTML 渲染与
验收工具集。工作流优先复用 YouTube 已有字幕，失败时再使用 `yt-dlp` 或本地
Whisper；下载或模型不可用时必须说明降级，不能虚构字幕、截图或视频结论。

## 能做什么

- 识别标准 YouTube、`youtu.be` 和嵌入链接。
- 优先通过 `youtube-transcript-api` 获取人工或自动字幕。
- 用 `yt-dlp` 提取字幕、缩略图和公开元数据作为备选。
- 无可用字幕时，可选用本地 Whisper 转写。
- 按字幕时间戳和内容切换点选择关键画面，而不是机械定时截图。
- 生成严格的 `video-note/v2`、离线 Web HTML 和静态 Email HTML。
- 嵌入 JPEG、PNG、WebP，移动 HTML 后仍可离线阅读。
- 提供导航、筛选、折叠、复制、键盘访问和 A4 打印。
- 拒绝危险 URL、未知字段、缺失图片和 reader/evidence 泄漏。
- 用代表性标题归档最终 HTML，目录由用户或 Agent 明确设定。
- 与 Bilibili Skill 运行同一套语义契约、渲染和验收测试。

## v0.2.0 的核心变化

1. YouTube 与 Bilibili Skill 共用平台中立的
   `references/schema/video-note-v2.schema.json`。
2. 新增 golden-reference 驱动的 Web/Email HTML，不再只输出普通 Markdown。
3. reader-facing `note.json` 与 `evidence.json` 严格分离；原始字幕、OCR、
   grounding、confidence 等证据不得进入 HTML。
4. Email 是单独的静态 profile：无 JavaScript、无交互依赖、全部内容展开。
5. 最终 HTML 归档目录不再依赖作者电脑；CLI、环境变量、JSON 配置或 Agent 均可
   显式设置。

## 工作流

```text
YouTube URL
    │
    ├─ 字幕：youtube-transcript-api → yt-dlp → Whisper → 明确降级
    │
    ├─ 元数据：yt-dlp --dump-json / 页面公开数据
    │
    └─ 画面：字幕时间戳 → 内容切换点 → 可选视觉/OCR复核
                         │
                         ▼
            note.json + evidence.json
                         │
                         ├─ web profile → 离线交互 HTML
                         └─ email profile → 静态 Email HTML
                         │
                         ▼
       测试 + Playwright 验收 + 代表性标题归档
```

仓库负责字幕工具、公开契约、渲染、归档和验收。Agent 或宿主工作流负责把字幕、
元数据与经验证的画面理解整理成高质量 `note.json`；渲染器不会自行推断视频事实。

## 输出与语义边界

推荐运行目录：

```text
output/
├── transcript.json    # 带时间戳字幕
├── note.json          # 面向读者的 video-note/v2
├── evidence.json      # 原始字幕、OCR、时间对齐与覆盖证据
├── note.html          # Web profile
├── email.html         # 可选 Email profile
└── media/             # 构建阶段图片；最终 HTML 会内嵌
```

`video-note/v2` 支持段落、列表、callout、状态卡、特性卡、动态表格、代码块、可信
renderer SVG、图片和 accordion 等语义块。平台差异只能进入来源 badge、视频 URL、
平台名称、作者等元数据，不得分叉语义块和视觉语法。

以下内容只能留在 `evidence.json`：

```text
raw_subtitles
ocr
ocr_text
confidence
grounding
grounding_score
timestamp_alignment
extraction_trace
```

测试会用唯一 sentinel 验证这些字段和值没有泄漏到 HTML 源码。

## 安装

要求：

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- 核心字幕依赖：`youtube-transcript-api`、`yt-dlp`
- 视频处理或 Whisper 兜底需要 `ffmpeg`

```bash
git clone https://github.com/ezbug/youtube-to-obsidian.git
cd youtube-to-obsidian
uv sync --frozen
```

按需安装：

```bash
uv sync --frozen --extra ocr
uv sync --frozen --extra whisper
uv sync --frozen --extra full
```

安装为 Codex Skill：

```bash
./scripts/install.sh --target codex --mode symlink
./scripts/doctor.sh
```

也可使用 `--target hermes`。卸载只删除安装入口，不删除仓库：

```bash
./scripts/uninstall.sh --target codex
```

## 获取字幕和元数据

首选 `youtube-transcript-api`。需要 `yt-dlp` 备选时：

```bash
uv run python scripts/extract_subtitles.py '<YouTube URL>' \
  --lang 'en,zh-CN' \
  --format srt
```

只获取自动字幕：

```bash
uv run python scripts/extract_subtitles.py '<YouTube URL>' \
  --lang 'en,zh-CN' \
  --format srt \
  --auto
```

获取公开元数据：

```bash
uv run yt-dlp --dump-json --no-download '<YouTube URL>'
```

字幕优先级：

1. `youtube-transcript-api`
2. `yt-dlp` 人工字幕
3. `yt-dlp` 自动字幕
4. 本地 Whisper
5. 只有标题和描述时，明确标记为降级摘要

YouTube 的 SABR、限流、地区限制或登录要求可能导致 `yt-dlp` 失败。Cookie 文件
必须由用户显式提供；Agent 不得读取浏览器 Cookie 数据库、密码、验证码或 2FA。

## 构建 `video-note/v2`

Agent 应以字幕时间戳确定章节和画面候选，只在出现步骤、界面、代码、配置、结果
或对比等内容切换时选图。reader 内容应回答：

- 视频教了什么？
- 为什么重要？
- 什么时候使用？

原始字幕、OCR 与模型置信度写入 `evidence.json`；读者 HTML 只保留经过整理和
来源分类的解释。最小合法 fixture 位于
`tests/fixtures/video-note-v2/minimal.json`，完整 block 示例位于
`tests/fixtures/video-note-v2/all-blocks.json`。

## 从 `note.json` 生成 HTML

Web profile：

```bash
uv run python scripts/render_html.py \
  --note '<构建目录>/note.json' \
  --output '<构建目录>/note.html' \
  --profile web
```

Email profile：

```bash
uv run python scripts/render_html.py \
  --note '<构建目录>/note.json' \
  --output '<构建目录>/email.html' \
  --profile email
```

### Web profile

- 单文件、离线、响应式。
- 桌面粘性导航，平板/手机导航移到正文上方。
- 特性筛选、accordion、复制按钮和 `aria-live` 反馈。
- 嵌入 favicon 与内容图片，运行时零网络依赖。
- A4 打印时隐藏导航和交互控件，保留有语义的背景色。

### Email profile

- 面向 Apple Mail 16+ 和静态 HTML 预览。
- 不含 JavaScript、按钮、accordion、sticky、外部样式或外部图片。
- 所有筛选组和折叠内容直接展开。
- 使用 email-compatible 内联样式和表格外壳。
- 可单独验证：`assert "<script" not in email_html.lower()`。

渲染失败返回退出码 `2`，错误会指出具体字段或图片路径。渲染输出采用临时文件
加原子替换；失败不会破坏已有 HTML。

## 归档最终 HTML

先读完整篇笔记，提炼能够同时表达“主题”和“主要结论、方法或冲突”的标题，
再归档已验收的 Web HTML。不要直接沿用视频原标题，也不要使用“视频笔记”
“video ID 总结”等泛化名称。

```bash
uv run python scripts/archive_html.py \
  --html '<构建目录>/note.html' \
  --title '<代表性标题>' \
  --source-id '<YouTube video ID>' \
  --archive-dir '<用户选择的目录>'
```

归档目录解析优先级：

1. `--archive-dir`
2. `VIDEO_NOTE_ARCHIVE_DIR`
3. `--config` 或 `VIDEO_NOTE_CONFIG` 指向的 JSON 文件
4. 仓库根目录中被 Git 忽略的 `archive-config.local.json`
5. 未配置则退出并要求用户或 Agent 明确选择

JSON 配置示例见 `archive-config.example.json`：

```json
{
  "archive_dir": "/path/chosen/by/the/user"
}
```

需要本机长期使用时，可复制为 `archive-config.local.json` 并填写路径。该文件已被
`.gitignore` 排除，不会进入提交或 GitHub。

归档器会清理文件名中的不安全字符、创建父目录，以临时文件加原子链接发布并
校验 SHA-256/字节一致性。同名且内容相同会复用；同名但内容不同会先追加
`（video ID）`，再次冲突再追加序号。Email 版本需显式添加 `--profile email`，文件名会变成
`<代表性标题>—Email.html`。PDF 只是打印验收证据，不是默认交付物。

## 配置与隐私

仓库不包含任何个人电脑路径、Vault 位置、Cookie、邮箱认证信息或运行产物。路径
均由调用者设置：

| 项目 | 推荐设置方式 |
|---|---|
| 构建目录 | Agent/宿主 CLI 参数 |
| 最终 HTML 目录 | `--archive-dir` 或 `VIDEO_NOTE_ARCHIVE_DIR` |
| 归档 JSON 配置 | `--config` 或 `VIDEO_NOTE_CONFIG` |
| Obsidian Vault | Agent/宿主配置或 `config.example.yaml` 的 `vault` |
| YouTube Cookie 文件 | `YOUTUBE_COOKIES_FILE` 或显式参数 |

安全规则：

- 不提交真实 `.env`；`.env.example` 只列变量名。
- 不提交视频、字幕、截图、缓存、acceptance 产物或个人 Obsidian 数据。
- 用户文本始终转义；只允许 `http`、`https` 和 renderer 验证后的本地嵌入资源。
- 用户 HTML、用户 SVG 和危险 URL scheme 会被拒绝。
- 缺图必须失败，不能生成静默占位页。
- 邮件默认不发送；只有用户明确要求并显式运行邮件脚本时才发送。

## Golden HTML 与验收

`references/html-golden/` 是只读视觉基线；设计分析和实测 token 位于
`references/html-design-analysis.md`。渲染器复用其信息架构、空间系统、语义
颜色和交互语言，同时有意修复三个问题：390px 溢出、favicon 请求和复制反馈的
持久 `aria-live`。

完整验收：

```bash
uv run --extra dev pytest -q
uv run --extra dev python scripts/acceptance_check.py
```

验收固定使用 Playwright CLI `0.1.17` / Headless Chromium `150`：

| 视口 | 必须满足 |
|---|---|
| 1440px | 1280px shell、250px 导航、24px gap、3 列卡片 |
| 1024px | 保留双栏、3 列卡片 |
| 768px | 导航上移、2 列卡片 |
| 390px | 1 列卡片、`scrollWidth <= 390` |

同时检查零 console/page error、零失败请求、键盘操作、焦点状态、离线重载、
图片内嵌和 A4/12mm 打印。证据写入 git 忽略的 `acceptance/`。

## 跨仓库一致性

Bilibili 与 YouTube 实现允许内部代码不同，但以下项目必须等价：

- `video-note/v2` schema 和未知字段策略
- normalized semantic model
- HTML 设计 token 与语义 block 行为
- Web/Email profile
- 安全、缺图、URL 和 evidence 验证
- CLI 退出码和错误语义
- 固定视口、离线、无障碍和打印验收

平台差异只能出现在来源元数据中。共享 fixture 和 parity report 用于阻止两个
Skill 随版本演进而分叉。

## 仓库结构

```text
.
├── SKILL.md
├── scripts/
│   ├── extract_subtitles.py
│   ├── render_html.py
│   ├── archive_html.py
│   └── acceptance_check.py
├── references/
│   ├── schema/video-note-v2.schema.json
│   ├── html-golden/
│   └── html-design-analysis.md
└── tests/
```

## 已知边界

- YouTube 接口、限流、SABR、地区限制和登录要求会随平台变化。
- Whisper、OCR、视觉分析、PPT 和邮件属于可选增强，不是默认成功条件。
- 仓库当前没有单一“输入 URL 后自动生成所有内容”的模型编排器；Agent/宿主负责
  生成经过证据约束的 `note.json`。
- 没有字幕、画面或来源证据时必须说明降级，不得伪装为完整视频分析。

更细的 Agent 执行规则、字幕优先级和历史兼容说明见 `SKILL.md`。
