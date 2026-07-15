# youtube-to-obsidian

面向 Codex 的 YouTube 视频转 Obsidian 笔记 Skill。核心字幕与笔记流程包含在本仓库中；页面提取、视觉分析、OCR、Whisper、导图、PPT 和邮件均为可选增强。

## 核心流程

1. 优先使用 `youtube-transcript-api` 获取现有字幕。
2. 字幕接口不可用时，使用 `yt-dlp` 作为备选；仓库提供 `scripts/extract_subtitles.py` 辅助脚本。
3. 没有字幕时，可选用本地 Whisper 转写。
4. 使用字幕时间戳选择内容切换点，生成总结和可选关键帧，而不是机械地按固定间隔截图。
5. 输出 Obsidian Markdown；导图、PPT 和邮件按需生成。

如果下载、模型或可选工具不可用，工作流必须说明降级原因，不得虚构字幕或图文分析。

## 环境要求

- Python 3.11 或更高版本
- [uv](https://docs.astral.sh/uv/)
- 核心字幕依赖：`youtube-transcript-api`、`yt-dlp`
- 视频处理或 Whisper 兜底需要 `ffmpeg`

安装锁定依赖：

```bash
uv sync --frozen
```

按需安装可选能力：

```bash
uv sync --frozen --extra ocr
uv sync --frozen --extra whisper
uv sync --frozen --extra full
```

## 安装到 Codex

推荐使用符号链接。仓库更新后，Codex 可直接使用最新版本：

```bash
./scripts/install.sh --target codex --mode symlink
./scripts/doctor.sh
```

卸载只移除安装入口，不删除源码仓库：

```bash
./scripts/uninstall.sh --target codex
```

安装脚本也支持 `--target hermes`，但当前 README 和测试以 Codex 新版为准。

## 字幕提取

首选在工作流中调用 `youtube-transcript-api`。需要使用 `yt-dlp` 备选脚本时：

```bash
uv run python scripts/extract_subtitles.py '<YouTube URL>' \
  --lang 'en,zh-CN' \
  --format srt
```

只获取自动生成字幕时增加 `--auto`。处理频道时可用 `--limit <数量>` 限制视频数。

视频元数据可通过以下命令获取：

```bash
uv run yt-dlp --dump-json --no-download '<YouTube URL>'
```

## 安全边界

- 不要提交真实 `.env`。`.env.example` 只列出可选变量名，不包含真实值。
- 不上传 Cookie、下载视频、字幕、模型、缓存、运行结果或个人 Obsidian 数据。
- Safari 只允许复用已有登录状态；不输入密码、验证码或 2FA，也不读取 Cookie 数据库。
- 邮件默认不发送。只有用户明确要求并显式调用邮件脚本时才发送。
- 非敏感路径和行为设置写入本机配置，不写入 `.env`。

## 测试

```bash
uv run --extra dev pytest -q
```

完整工作流、字幕优先级和可选增强说明见 `SKILL.md`。
