---
name: youtube-to-obsidian
description: YouTube视频转Obsidian笔记工作流。核心字幕与笔记流程在本仓库内完成；页面提取、vision、OCR、Whisper、导图、PPT和邮件均为可选宿主增强。触发词：「识别这个YouTube链接，帮我生成视频笔记」「YouTube视频笔记」「youtube xxx 总结」。
---

## Codex 运行覆盖规则（优先于下文的历史 Hermes 示例）

- 仅使用本仓库及运行参数指定的输入、缓存和输出目录；不要依赖用户目录、reviewer profile 或 Windows 路径。
- 使用本机 `ffmpeg`、本地 Whisper 与 Ollama vision。模型或下载不成功时写出明确的降级原因，不虚构图文分析。
- PaddleOCR 3.x 使用 `PaddleOCR(lang='ch')`；不要复用旧示例中的 `lang='chinese'`。
- Safari 仅可复用已有登录状态；不得输入密码、验证码或 2FA，且不得尝试读取 Safari cookie 数据库。
- 发送邮件前先生成 HTML；邮件默认只保留草稿，只有用户明确要求并显式调用邮件脚本时才发送。

## 当前 HTML 渲染入口

面向读者的 HTML 必须从 `video-note/v2` 生成；canonical schema 是
`references/schema/video-note-v2.schema.json`，golden 设计参数见
`references/html-design-analysis.md`。不要根据文字描述重新设计通用文章页。

```bash
python3 scripts/render_html.py \
  --note '<output-dir>/note.json' \
  --output '<output-dir>/note.html' \
  --profile web
```

需要静态邮件时将 profile 改为 `email`。Web 输出允许 renderer-owned 交互；
email 输出不得含脚本、折叠或仅交互可见的内容。两者均须离线单文件、嵌入已
验证图片、拒绝危险 URL，并把原始字幕、OCR、grounding、confidence 留在
`evidence.json`。缺图或 schema 错误必须明确失败，不可生成降级占位页。

交付前必须运行：

```bash
uv run --extra dev pytest -q
uv run --extra dev python scripts/acceptance_check.py
```

浏览器验收固定使用 Playwright CLI `0.1.17` / Headless Chromium `150`，覆盖
1440、1024、768、390 四个视口、键盘操作、离线重载和 A4/12mm 打印。

# YouTube to Obsidian 视频笔记工作流

## 核心功能

将YouTube视频一键转换为Obsidian笔记，包含字幕文件、页面数据和总结笔记；导图、PPT设计建议与邮件摘要为可选输出。

## 工作流程

### Step 1: 识别视频

从用户消息中提取YouTube链接，格式：
- 标准链接：`https://www.youtube.com/watch?v=xxx`
- 短链接：`https://youtu.be/xxx`
- 嵌入链接：`https://www.youtube.com/embed/xxx`

### Step 2: 下载字幕 + 提取页面数据

**双重提取：**

#### 2.1 下载字幕（按优先级尝试）

**方法A：youtube-transcript-api（推荐，最可靠）**
```python
from youtube_transcript_api import YouTubeTranscriptApi
ytt_api = YouTubeTranscriptApi()
transcript = ytt_api.fetch("VIDEO_ID")
# 转为 SRT
for i, entry in enumerate(transcript.snippets, 1):
    start = entry.start
    end = start + entry.duration
    print(f"{i}\n{fmt_time(start)} --> {fmt_time(end)}\n{entry.text}\n")
```
- ✅ 无需 ffmpeg，纯 Python，最稳定
- ✅ 自动获取可用字幕（含自动生成）
- ⚠️ 需要 `pip install youtube-transcript-api`

**方法B：yt-dlp（备用）**
```bash
python scripts/extract_subtitles.py <video_url> [--lang en,zh-CN] [--auto]
# 或直接用 yt-dlp
yt-dlp --write-subs --write-auto-subs --sub-langs "en,zh" --skip-download -o "output.%(ext)s" <url>
```
- ⚠️ 2025年起 YouTube 强制 SABR 流媒体，yt-dlp 频繁 403
- 如果 403，切换到方法A

**方法C：video-summary --subtitle**
```bash
video-summary "<url>" --subtitle
```
- 底层也用 yt-dlp，同样可能 403

**字幕提取决策树：**
```
youtube-transcript-api 可用？
├── 是 → 用方法A（首选）
└── 否 → yt-dlp 能下载？
    ├── 是 → 用方法B
    └── 否（403）→ 安装 youtube-transcript-api 后用方法A
```

**获取视频元数据（标题、描述、章节等）：**
```bash
yt-dlp --dump-json --no-download "<url>" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('TITLE:', d.get('title',''))
print('DURATION:', d.get('duration',''), 'sec')
print('DESCRIPTION:', d.get('description','')[:1000])
print('CHAPTERS:', d.get('chapters', []))
"
```
- 即使视频下载 403，`--dump-json` 通常仍可获取元数据

#### 2.2 提取页面数据

**方法A：defuddle（如果已安装）**
```bash
defuddle parse <video_url> --md -o page_content.md
```

**方法B：yt-dlp --dump-json（备用，始终可用）**
- 从 2.1 的元数据中提取标题、描述、标签、章节
- 已包含视频的核心页面信息

**方法C：browser_navigate + snapshot**
- 导航到 YouTube 页面，提取描述和评论
- 适用于需要评论内容的场景

### Step 3: 生成AI总结

使用video-summary Skill的能力：
1. 分析字幕内容结构
2. 提取核心观点和关键知识点
3. 结合页面描述补充上下文
4. 生成Markdown格式的总结

### Step 4: 视频截图分析（图文对应笔记）

在生成AI总结后，对视频进行**关键帧截图 + vision 画面分析**，产出与字幕总结一一对应的图文笔记。

#### 4.1 内容时间戳驱动截图（非机械间隔）

**不再按固定秒数截帧。改为从字幕/转录的段落时间戳中提取关键时间点，只在有"内容切换"时截图。**

```python
import json

with open("/tmp/transcript.json") as f:
    data = json.load(f)

CONTENT_KW = ["介绍","展示","步骤","位置","点击","设置","配置","安装","下载","运行",
              "代码","函数","API","界面","功能","效果","结果","对比","总结"]

key_timestamps = []
for seg in data["segments"]:
    t = seg["text"]
    s = seg["start"]
    if any(kw in t for kw in CONTENT_KW):
        if s > 1 and (not key_timestamps or s - key_timestamps[-1] > 3):
            key_timestamps.append(s)

print(f"内容截图点: {len(key_timestamps)} 个")
```

#### 4.2 YouTube 截图方案

**方法A（推荐）：YouTube 官方缩略图 API（不下载视频）**

YouTube 为每个视频预生成了多个时间点的缩略图，可直接通过 URL 获取：

```bash
# 获取视频 ID
VIDEO_ID=$(echo "$URL" | sed -E 's/.*(v=|youtu.be\/)([a-zA-Z0-9_-]{11}).*/\2/')

# 下载所有可用缩略图（YouTube 自动生成的预览帧）
yt-dlp --write-thumbnail --skip-download -o "/tmp/yt_thumb/%(id)s" "$URL"
```

**方法B（需要视频内截图）：yt-dlp + 片段下载**

```bash
# 对每个内容时间戳，只下载 2 秒片段
for ts in 18 28 38 50 56; do
  yt-dlp -f "bestvideo[height<=720]" \
    --download-sections "*$((ts-1))-$((ts+2))" \
    --force-keyframes-at-cuts \
    -o "/tmp/yt_seg_${ts}.mp4" "$URL" -q

  ffmpeg -ss 0.5 -i "/tmp/yt_seg_${ts}.mp4" \
    -vframes 1 -q:v 2 -vf "scale=1280:-1" \
    "/tmp/frames/dye_$(printf '%04d' $ts).jpg"
  rm "/tmp/yt_seg_${ts}.mp4"
done
```

#### 4.3 帧质量过滤 + OCR 文字提取

```python
import cv2, os, glob
# 拉普拉斯去模糊
FRAMES_DIR = "/tmp/frames"
for fpath in sorted(glob.glob(f"{FRAMES_DIR}/*.jpg")):
    img = cv2.imread(fpath, cv2.IMREAD_GRAYSCALE)
    if img is None or cv2.Laplacian(img, cv2.CV_64F).var() < 50:
        os.remove(fpath)

# OCR 提取画面文字
from paddleocr import PaddleOCR
import json
ocr = PaddleOCR(use_angle_cls=True, lang='ch')
ocr_results = {}
for fpath in sorted(glob.glob(f"{FRAMES_DIR}/*.jpg")):
    result = ocr.ocr(fpath, cls=True)
    texts = [l[1][0] for l in (result[0] or []) if l[1][1] > 0.5]
    ocr_results[os.path.basename(fpath)] = texts
with open(f"{FRAMES_DIR}/ocr_results.json", "w") as f:
    json.dump(ocr_results, f, ensure_ascii=False, indent=2)
```

#### 4.4 并行 vision 分析

使用 Hermes vision 能力 + OCR 文字进行综合分析。截图超过 10 张时用 `delegate_task` 分组并行处理。

**分析规则：**
1. 将截图路径提供给模型做 vision 分析（优先本地 `vision_analysis` MCP）
2. 同时加载 OCR 结果
3. 分析五要素：画面内容、OCR文字、对应字幕、关键信息、上下文

**vision 调用参数：**
- 使用 `keep_alive: "15m"` 保持模型常驻（避免每次冷启动）
- 用 `curl` subprocess 代替 httpx（httpx 在部分环境有 502 bug）
- 截图和字幕一起发给模型：`"This is a screenshot. The narrator says: '{transcript}'. Based on BOTH describe..."`

#### 4.5 截图排版规则
1. **一行一张截图**，占满可用宽度，最大高度 500px
2. 每张截图下方三段式：标题行 + AI+字幕融合描述 + UP主原声引用
3. AI+字幕融合描述必须自然口语化，禁止「这是一个...」「该图片显示...」模板化开头

#### 4.6 vision 分析规则
1. 截图+字幕一起发给模型，不能只看图
2. 优先本地模型，`keep_alive` 保持常驻，`curl` subprocess 代替 httpx
3. 分析场景类型（地图/室内/室外/UI）、是否有标记、对应讲解内容

> **回退**：所有截图方案均失败时仅输出文字笔记。

### 完整性检查清单（必做，不可跳过）
交付 HTML 前逐项确认：
- [ ] 每张截图都嵌入了对应的字幕原文
- [ ] 每个颜色/章节都有至少 1 张代表截图
- [ ] 截图总数与内容时间戳数量一致，无遗漏（如有截图方案失败的需在文档中标注）
- [ ] 所有 `data:image/jpeg;base64` 的 img 标签能正常渲染（检查是否有空 src）
- [ ] AI+字幕融合描述不是模板化开头（禁止「这是一个...」「该图片显示...」）
- [ ] 输出的文件总大小合理（1080p 约 15-20MB，480p 约 3-5MB）
- [ ] 特殊章节（如"特殊染料""总结""附录"）也有对应的截图，不遗漏任何内容段落

```python
import cv2, os, glob
import numpy as np

FRAMES_DIR = "/tmp/youtube_screenshots/frames"
QUALITY_THRESHOLD = 100  # 拉普拉斯方差 > 100 为清晰

frames = sorted(glob.glob(f"{FRAMES_DIR}/*.jpg"))
for fpath in frames:
    img = cv2.imread(fpath, cv2.IMREAD_GRAYSCALE)
    if img is None:
        os.remove(fpath)
        continue
    laplacian_var = cv2.Laplacian(img, cv2.CV_64F).var()
    if laplacian_var < QUALITY_THRESHOLD:
        os.remove(fpath)  # 删除模糊帧
        print(f"REMOVED blurry: {os.path.basename(fpath)} (var={laplacian_var:.1f})")
    else:
        print(f"KEPT clear: {os.path.basename(fpath)} (var={laplacian_var:.1f})")
```

**阈值经验值：**
| 视频类型 | 推荐阈值 | 说明 |
|---------|---------|------|
| 屏幕录制/教程 | ≥ 50 | 画面静态，容易清晰 |
| 实拍/室外 | ≥ 100 | 运动模糊多 |
| 动画/特效 | ≥ 80 | 过渡帧多 |

#### 4.4 OCR 提取截图文字（Phase 2）

对每张截图使用 PaddleOCR 提取画面中的文字（代码、UI 按钮、参数等），作为 vision 分析的补充信息：

```bash
pip install paddleocr
```

```python
from paddleocr import PaddleOCR
import glob, json

ocr = PaddleOCR(use_angle_cls=True, lang='ch')
FRAMES_DIR = "/tmp/youtube_screenshots/frames"

frames = sorted(glob.glob(f"{FRAMES_DIR}/*.jpg"))
ocr_results = {}

for fpath in frames:
    result = ocr.ocr(fpath, cls=True)
    fname = os.path.basename(fpath)
    texts = []
    for line in result[0] if result[0] else []:
        text = line[1][0]
        confidence = line[1][1]
        if confidence > 0.5:  # 只保留高置信度文字
            texts.append(text)
    ocr_results[fname] = texts

# 保存 OCR 结果供后续 vision 分析使用
with open(f"{FRAMES_DIR}/ocr_results.json", "w") as f:
    json.dump(ocr_results, f, ensure_ascii=False, indent=2)
```

OCR 提取的文字会在 vision 分析时一并提供给模型，帮助理解代码、按钮文本、参数面板等内容。

#### 4.5 复制截图到 Obsidian vault

将截图复制到 Obsidian vault 的附件文件夹，使笔记中的图片引用能正常显示：

```bash
VAULT_ATTACH_DIR=\"<output-dir>/{视频标题}_attachments\"
mkdir -p \"$VAULT_ATTACH_DIR\"
cp /tmp/youtube_screenshots/frames/*.jpg \"$VAULT_ATTACH_DIR/\"
```

#### 4.6 并行 vision 分析：delegate_task + OCR 增强（Phase 1+2）

对每张截图，使用 Hermes 的 vision 能力 + OCR 提取文字进行综合分析。截图超过 10 张时，用 `delegate_task` 并行处理分帧组以加速：

**核心分析规则：**

1. **传给模型看截图**：将截图路径提供给模型做 vision 分析（优先使用本地 `vision_analysis` MCP，零 API 费用）
2. **同时加载 OCR 结果**：从 `ocr_results.json` 读取该帧的 OCR 识别文字，一并提供给模型
3. **分析要点**：
   - 📍 **画面内容**：UI界面、代码、图表、人物、实物等
   - 📝 **画面文字（OCR）**：该帧中识别出的代码片段、按钮文字、参数值等
   - 🏷️ **对应字幕**：匹配该时间点的字幕文本（从 Step 2 获取）
   - 💡 **关键信息**：画面传达的核心信息和操作步骤
   - 🔗 **上下文**：这张图在视频中的位置和作用

**并行策略：**

当截图超过 10 张时，使用 Hermes 的 `delegate_task` 将帧分组并行分析：

```markdown
# 分组规则
- 每 5 张截图为 1 组（最后不足 5 张的独立成组）
- 每组附带 OCR 结果文件路径和对应字幕文本
- 使用 delegate_task(tasks=[...]) 并行提交所有组
- 每组返回该组帧的图文分析 Markdown
- 所有组返回后，按时间戳顺序合并成完整笔记
```

**示例（伪码）：**
```
帧列表：25 张截图
→ 分 5 组，每组 5 张
→ 5 个 delegate_task 并行运行
→ 每组分析结果返回
→ 按时间戳合并输出
```

**如果 vision 模型不支持看图**，使用 OCR 结果 + 字幕文本生成纯文字描述，格式简化：

```markdown
### 00:00:15
> [!note] 画面描述（基于 OCR）
> **OCR 识别文字**：[该帧中提取的文字内容]
> **对应字幕**：[字幕文本]
> **关键信息**：[基于文字的推断]
```

#### 4.7 生成图文笔记内容

将截图+分析结果整合为 Obsidian 兼容的 Markdown，格式严格如下：

```markdown
## 📸 视频截图笔记

### 00:00:15 — 标题/描述
![[{视频标题}_attachments/frame_0001.jpg]]
> [!abstract] 画面分析
> **画面内容**：[描述画面中有什么]
> **对应字幕**：[该时间点对应的字幕文本]
> **关键信息**：[画面传达的核心信息]
> **上下文**：[这张图在视频中的位置和作用]

---

### 00:00:30 — 标题/描述
![[{视频标题}_attachments/frame_0002.jpg]]
> [!abstract] 画面分析
> **画面内容**：[描述画面中有什么]
> **对应字幕**：[字幕文本]
> **关键信息**：[核心信息]
> **上下文**：[位置和作用]

---
```

**使用 `![[...]]` Obsidian wikilink 语法引用图片**，而不是标准 Markdown `![](path)`，因为：
- Obsidian 渲染 `![[attachment.png]]` 会正确显示 vault 内的附件
- 如果图片在 vault 的附件文件夹中，Obsidian 会自动管理

**图片太多时的精简策略**：在正文中只展示 5-10 个最具代表性的图文条目，其余以附录形式列出：

```markdown
<details>
<summary>📎 其余截图（共 25 张）</summary>

| 时间戳 | 缩略图 | 画面内容 |
|--------|--------|---------|
| 00:01:45 | ![[..._attachments/frame_0003.jpg\|100]] | [简短描述] |
| 00:02:00 | ![[..._attachments/frame_0004.jpg\|100]] | [简短描述] |
| ... | ... | ... |

</details>
```

#### 4.8 截图质量准则

- **分辨率**：截图宽度保持 960px（平衡清晰度和文件大小）
- **格式**：JPEG，质量因子 3（ffmpeg `-q:v 3` ≈ 高质量 JPEG）
- **文件名**：`frame_时间戳秒数.jpg`（如 `frame_0015.jpg`），方便对应回视频时间
- **清理临时文件**：截图完成后清理 `/tmp/youtube_screenshots/`

> **Phase 1 依赖安装**：`pip install scenedetect`
> **Phase 2 依赖安装**：`pip install paddleocr opencv-python`
> **回退机制**：如果 PySceneDetect 未安装，自动回退到 ffmpeg 间隔抽帧；如果 PaddleOCR 未安装，跳过 OCR 步骤；如果 opencv-python 未安装，跳过帧质量过滤。不影响核心流程。

### Step 5: Obsidian Markdown优化

**使用obsidian-markdown Skill优化格式**

在保存字幕和总结前，调用obsidian-markdown Skill的规范：
1. 添加正确的YAML front matter（title, date, tags, related, source）
2. 使用Obsidian特有的语法：
   - 使用callouts高亮重要信息（`> [!note]`, `> [!tip]`）
   - 使用wikilinks关联相关笔记
   - 使用Properties语法
3. 确保语法正确：
   - 标题层级正确
   - 列表格式规范
   - 代码块正确标记

### Step 6: 跳过导图生成

**不再生成 `.canvas` 导图文件。** HTML 笔记已包含完整结构化内容（截图+字幕+表格），浏览器直接打印即可，无需额外导图。HTML 的色系分组 → 截图+字幕 → 位置表格排版已覆盖原导图的信息层级。

### Step 7: 输出PPT设计建议为.md文件（关键）

**使用afrexai-presentation-mastery Skill**

在生成设计建议前，先调用presentation-mastery的设计分析能力：
1. 分析笔记内容主题
2. 确定目标受众
3. 设计slide结构
4. 提出设计建议（配色、布局、视觉层次）

#### 7.1 创建Presentation Brief

根据视频内容填充brief：

```yaml
presentation_brief:
  title: "视频标题"
  format: "conference"  # 根据内容类型选择
  audience:
    roles: ["技术爱好者", "开发者"]
    knowledge_level: "intermediate"
    disposition: "supportive"
  objective:
    one_sentence: "观众将理解XXX的核心概念和实践方法"
```

#### 7.2 选择叙事框架

根据视频内容类型选择：

| 内容类型 | 推荐框架 |
|---------|---------|
| 教程/步骤类 | Teach → Practice → Apply |
| 架构/系统类 | Problem → Solution → Proof |
| 观点/评测类 | What → So What → Now What |
| 故事/分享类 | Hero's Journey |

#### 7.3 输出PPT设计建议为.md文件

**将设计建议保存为Markdown文件**，与总结放在同一文件夹：

```yaml
# PPT设计建议头部
---
title: "{视频标题} - PPT设计建议"
type: ppt-design-brief
source: {YouTube链接}
date: {当前日期}
related:
  - <output-dir>/{标题}_总结.md
---
```

**设计建议内容模板**（从afrexai-presentation-mastery的输出中提取）：

```markdown
## PPT设计方案

### 配色方案
- 主色调：[颜色+说明]
- 辅助色：[颜色+说明]
- 强调色：[颜色+说明]

### 内容结构
- 幻灯片数量：X页
- 结构：封面 → 目录 → [具体结构]

### 布局建议
- 标题页：居中/左侧
- 内容页：单栏/双栏/卡片式
- 总结页：要点列表/高亮框

### 视觉层次
- 标题：XXpt，粗体
- 正文：XXpt，常规
- 强调：使用[颜色/图标/对比]

### 叙事框架
- 框架名称：[选择的框架]
- 开场：[建议的开场方式]
- 收尾：[建议的收尾方式]

### 建议的PPT风格
- 风格：[tech_dark / business_blue / creative_purple / academic_white / minimal_gray]
- 字体：[字体建议]
- 每页核心：一页一核心，标题=结论

### 幻灯片结构（详细）
1. 封面页
2. 目录页
3. [内容页1]
4. [内容页2]
   ...
N. 总结页
```

**保存位置**：`<output-dir>/{标题}_PPT设计建议.md`

### Step 8: 生成 HTML 邮件摘要并发送

**不再发送纯文本邮件。改为生成带截图和排版表格的 HTML 邮件，与视频笔记 HTML 同款排版。**

**使用send_email.py脚本**

根据总结内容和截图，生成完整 HTML 邮件正文（与视频笔记 HTML 同款样式，包含截图+字幕+表格）：

1. 将 Step 4-6 产出的所有截图 base64 嵌入 HTML（使用 `data:image/jpeg;base64,...`）
2. 按颜色章节组织：每章节标题 → 截图（2列网格）→ 位置表格
3. 章节顺序：染料系统结构 → 各色系（红/紫/蓝/天蓝/绿/黄/橙/豆绿）→ 特殊染料
4. 底部附视频信息（标题、UP主、BV号、时长）

```bash
# 生成 HTML 邮件文件
python3 -c "
# 读取已有 HTML 笔记文件，提取 \<body\> 内内容
with open('{输出路径}/视频标题.html') as f:
    html = f.read()
# 精简样式（邮件客户端不支持复杂CSS）
# 保存为邮件体
with open('/tmp/email_body.html', 'w') as f:
    f.write(html)
"

# 发送（脚本需支持 HTML 格式；仅在用户明确要求时执行）
python scripts/send_email.py \
  "<recipient>" "📺 {视频标题} | AI视频总结（图文版）" \
  /tmp/email_body.html \
  --html
```

**发送配置**：
- 通过 `EMAIL_FROM`、`EMAIL_AUTH_CODE` 或显式指定的 `EMAIL_AUTH_FILE` 注入；仓库不读取宿主私有配置。
- SMTP 主机和端口由调用方配置；不在 Skill 中保存邮箱、授权码或收件人。

**HTML 邮件排版规范：**
- 使用内联 CSS（邮件客户端不支持 `<style>` 标签）
- 截图宽度控制在 300px 以内（邮件正文不加载过重）
- 表格使用 `<table border="1" cellpadding="4">`
- 每张截图配字幕引用（灰色斜体）
- 总文件大小控制在 1MB 以内（QQ邮箱附件限制）

> ⚠️ 如果 QQ SMTP 发送失败（IP阻断），将 HTML 邮件另存为 `邮件_{标题}.html`，提示用户手动转发。

## 保存位置

| 文件类型 | 路径 |
|---------|------|
|| 字幕.srt | `<output-dir>/{标题}_字幕.srt` |
| 页面数据.md | `<output-dir>/{标题}_页面数据.md` |
| **总结笔记.html**（最终交付物，取代.md总结） | `<output-dir>/{标题}.html` |
| 截图附件 | `<output-dir>/{标题}_attachments/` |
| **PPT设计建议.md** | `<output-dir>/{标题}_PPT设计建议.md` |

> **输出变化**：不再生成 `.canvas` 导图文件和 `.md` 格式的总结笔记。HTML 文件是最终交付物，浏览器打开 → 打印 → PDF 即可。

## 文件关联

使用obsidian-markdown Skill的规范：
- 总结.md使用YAML front matter关联：
```yaml
related:
  - <output-dir>/{标题}_字幕.srt
  - <output-dir>/{标题}_页面数据.md
  - <output-dir>/{标题}_attachments/
  - <output-dir>/{标题}_PPT设计建议.md
```
- 使用wikilinks替代相对路径（Obsidian会自动追踪）

## 依赖Skill

- youtube-subtitle-extractor：YouTube字幕下载（yt-dlp，备用方案）
- youtube-transcript-api：YouTube字幕提取（Python库，首选方案，`pip install youtube-transcript-api`）
- defuddle：页面内容提取（去广告/导航，可选）
- video-summary：AI总结生成
- **ffmpeg**：视频关键帧截图（`brew install ffmpeg`）
- **Hermes vision 能力** / **`vision_analysis` MCP**：截图画面内容分析（本地模型已部署，通过 MCP 调度，免费）
- obsidian-markdown：Obsidian格式优化
- obsidian-canvas-creator：Canvas导图生成（MindMap+Freeform布局）
- excalidraw-diagram：Excalidraw图表生成（架构图、手绘风格）
- afrexai-presentation-mastery：PPT设计建议（.md格式输出）

## 依赖脚本

| 脚本 | 用途 |
|------|------|
| `scripts/send_email.py` | 可选邮件发送；显式调用时才会联网 |

## 注意事项

1. **字幕优先用 youtube-transcript-api**：最稳定，不受 YouTube SABR 限制。yt-dlp 作为备用。
2. **页面数据灵活获取**：defuddle 可选，yt-dlp --dump-json 或 browser 均可替代。
3. **设计驱动**：PPT设计建议在生成PPT前完成，供用户手动生成PPT使用
4. **内容为王**：设计服务于内容，不要过度设计
5. **HTML邮件格式**：生成带截图和内联CSS的HTML邮件，严格遵循Step 8规范，不再发送纯文本邮件
6. **Obsidian格式**：使用callouts、wikilinks、Properties等Obsidian特有语法增强可读性
7. **截图与字幕对应**：Step 4 的截图笔记必须与对应时间点的字幕内容保持语义一致，一张截图对应一段说明
8. **ffmpeg 安装**：截图功能依赖 ffmpeg，macOS 上 `brew install ffmpeg`，Linux 上 `apt install ffmpeg`
9. **vision 模型**：截图分析依赖 Hermes 的 vision 能力，确保当前模型支持图像输入
10. **依赖安装**：首次使用前确保 `pip install youtube-transcript-api yt-dlp`

## PPT生成规则（重要）

**当使用Claude Code生成PPT时**：
1. 从`总结.md`提取内容
2. **同时**从`PPT设计建议.md`获取设计规范（配色、布局、风格等）
3. 将两者结合，写入Claude Code的提示词
4. 确保生成的PPT既内容准确又设计专业

## 常见坑（Pitfalls）

1. **yt-dlp 403 错误**：YouTube 自 2025 年起强制 SABR 流媒体，yt-dlp 下载视频/音频经常 403。字幕提取用 `youtube-transcript-api`（纯 Python，不受影响）。元数据用 `yt-dlp --dump-json`（通常不触发 403）。截图时如果视频下载 403，改用 yt-dlp 缩略图模式或告知用户无法截图。
2. **视频无字幕**：部分视频既无手动字幕也无自动字幕。此时需用 Whisper 转录音频（需 ffmpeg + 足够内存），或仅基于描述和元数据生成笔记。
3. **defuddle 未安装**：不影响流程，用 `yt-dlp --dump-json` 或 `browser_navigate` 替代。
4. **send_email.py 路径**：使用本仓库的 `scripts/send_email.py`；凭据只从运行时环境或 `EMAIL_AUTH_FILE` 读取。
5. **ffmpeg 不在 PATH**：macOS 上可能装在 homebrew 或 krita 内部，用 `find /opt /usr/local ~/homebrew -name ffmpeg` 定位后加到 PATH。
6. **YouTube 页面需登录**：某些视频的描述/字幕面板需要登录才能查看，browser_navigate 可能看不到完整内容。
7. **截图太多导致笔记过长**：如果视频过长导致截图超过 30 张，正文只放 5-10 张代表帧，其余以可折叠 `<details>` 附录形式呈现。
8. **vision 分析失败**：如果当前模型不支持 vision 或分析结果不准确，回退为仅标注时间戳和截图文件名，不做详细画面分析。
9. **截图文件名冲突**：不同视频的截图统一放在 `{标题}_attachments/` 文件夹下，文件名以 `frame_时间戳.jpg` 格式区分，避免覆盖。
