# YouTube to Obsidian 工作流详解

## 流程概览

```
用户输入 → 识别YouTube链接 → 下载字幕 + 提取页面 → AI总结 → Obsidian优化 → Canvas导图 → PPT设计建议
```

## Step 1: 识别YouTube视频

### 支持的链接格式
- 标准：`https://www.youtube.com/watch?v=dQw4w9WgXcQ`
- 短链接：`https://youtu.be/dQw4w9WgXcQ`
- 嵌入：`https://www.youtube.com/embed/dQw4w9WgXcQ`

### 提取视频ID
```python
import re
def extract_video_id(url):
    patterns = [
        r'(?:v=|\\/)([0-9A-Za-z_-]{11}).*',
        r'(?:youtu\.be\/)([0-9A-Za-z_-]{11})',
    ]
    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            return match.group(1)
    return None
```

## Step 2: 下载字幕 + 提取页面数据

### 2.1 字幕下载（按优先级）

**方法A：youtube-transcript-api（首选，最可靠）**
```python
from youtube_transcript_api import YouTubeTranscriptApi
import re

ytt_api = YouTubeTranscriptApi()
transcript = ytt_api.fetch("VIDEO_ID")

# 转为 SRT
def fmt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

srt_lines = []
plain_lines = []
for i, entry in enumerate(transcript.snippets, 1):
    start = entry.start
    end = start + entry.duration
    srt_lines.append(f"{i}")
    srt_lines.append(f"{fmt_time(start)} --> {fmt_time(end)}")
    srt_lines.append(entry.text)
    srt_lines.append("")
    plain_lines.append(entry.text)

with open("transcript.srt", "w") as f:
    f.write("\n".join(srt_lines))
with open("transcript_plain.txt", "w") as f:
    f.write(" ".join(plain_lines))
```
- ✅ 无需 ffmpeg，纯 Python
- ✅ 自动获取可用字幕（含自动生成）
- ⚠️ 需要 `pip install youtube-transcript-api`

**方法B：yt-dlp（备用，可能 403）**
```bash
yt-dlp --write-subs --write-auto-subs --sub-langs "en,zh" \
  --skip-download -o "output.%(ext)s" <url>
```

**方法C：yt-dlp 元数据（始终可用，用于获取标题/描述/章节）**
```bash
yt-dlp --dump-json --no-download "<url>" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('TITLE:', d.get('title',''))
print('DURATION:', d.get('duration',''), 'sec')
print('CHAPTERS:', d.get('chapters', []))
print('DESCRIPTION:', d.get('description','')[:1000])
"
```

### 2.2 页面数据提取

**方法A：defuddle（如果已安装）**
```bash
defuddle parse <video_url> --md -o page_content.md
```

**方法B：yt-dlp --dump-json（备用，始终可用）**
- 从元数据中提取标题、描述、标签、章节

**方法C：browser_navigate + snapshot**
- 导航到 YouTube 页面，提取描述和评论

## Step 3: AI总结

### 输入
- 字幕文件（.srt 或 .txt）
- 页面数据（描述、标签等）

### 处理
1. 解析字幕时间轴，提取关键内容
2. 结合页面描述补充上下文
3. 识别核心主题和观点
4. 生成结构化总结

### 输出格式
```markdown
---
title: "视频标题"
date: 2026-06-08
source: https://www.youtube.com/watch?v=xxx
tags: [tag1, tag2]
related:
  - <output-dir>/{标题}_字幕.srt
  - <output-dir>/{标题}_页面数据.md
  - <output-dir>/{标题}_导图.canvas
  - <output-dir>/{标题}_PPT设计建议.md
---

# 视频标题

## 核心观点
- 观点1
- 观点2

## 详细内容
...
```

## Step 4: Obsidian Markdown优化

### Front Matter 规范
```yaml
---
title: "视频标题"
date: 2026-06-08
tags: [YouTube, 标签1, 标签2]
source: "https://www.youtube.com/watch?v=xxx"
related:
  - <output-dir>/{标题}_字幕.srt
  - <output-dir>/{标题}_页面数据.md
  - <output-dir>/{标题}_导图.canvas
  - <output-dir>/{标题}_PPT设计建议.md
---
```

### Callouts 使用
```markdown
> [!tip] 核心要点
> 视频最重要的观点总结

> [!note] 补充说明
> 相关背景信息

> [!caution] 注意事项
> 需要特别注意的内容
```

### Wikilinks
```markdown
相关内容：[[相关笔记名称]]
参见：[[另一个笔记|显示文本]]
```

## Step 5: Canvas导图生成

### Canvas格式
```json
{
  "nodes": [
    {
      "id": "unique_hex_id",
      "type": "text",
      "x": 0, "y": 0,
      "width": 300, "height": 100,
      "color": "6",
      "text": "## 中心主题"
    }
  ],
  "edges": [
    {
      "id": "unique_hex_id",
      "fromNode": "parent_id",
      "toNode": "child_id",
      "fromSide": "bottom",
      "toSide": "top"
    }
  ]
}
```

### 颜色预设
- `"1"` - 红色（警告/重要）
- `"2"` - 橙色（行动项）
- `"3"` - 黄色（问题/笔记）
- `"4"` - 绿色（正面/完成）
- `"5"` - 青色（信息/详情）
- `"6"` - 紫色（概念/抽象）

### 布局原则
- 中心节点居中
- 主分支径向分布
- 最小间距：水平 320px，垂直 200px
- ID 必须唯一（8-12位随机hex）

## Step 6: PPT设计建议

### 分析维度
1. **内容类型**：教程/评测/观点/记录
2. **目标受众**：技术人员/普通用户/专业人士
3. **叙事框架**：
   - 教程类 → Teach → Practice → Apply
   - 架构类 → Problem → Solution → Proof
   - 观点类 → What → So What → Now What

### 输出格式
```markdown
## PPT设计方案

### 配色方案
- 主色调：[颜色+说明]
- 辅助色：[颜色+说明]

### 内容结构
- 幻灯片数量：X页
- 结构：封面 → 目录 → [具体结构]

### 幻灯片结构（详细）
1. 封面页
2. 目录页
3. [内容页1]
   ...
N. 总结页
```

## 保存位置（macOS）

| 文件类型 | 路径 |
|---------|------|
| 字幕.srt | `<output-dir>/{标题}_字幕.srt` |
| 页面数据.md | `<output-dir>/{标题}_页面数据.md` |
| 总结.md | `<output-dir>/{标题}_总结.md` |
| 导图.canvas | `<output-dir>/{标题}_导图.canvas` |
| PPT设计建议.md | `<output-dir>/{标题}_PPT设计建议.md` |

## 邮件发送

```bash
python scripts/send_email.py \
  "<recipient>" "📺 {视频标题} | AI视频总结" \
  /tmp/email_body.txt
```
- SMTP: smtp.qq.com:465 (SSL)
- 配置：通过 `EMAIL_FROM`、`EMAIL_AUTH_CODE` 或 `EMAIL_AUTH_FILE` 在运行时注入
