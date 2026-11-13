# 听见 Studio 2.0 · AI 双语字幕精修工作台

> 由原 Windows `Tkinter + Argos` 批处理字幕工具全面重构升级而来，融合了 **VideoLingo / VideoCaptioner** 的多步上下文反思精翻管线与 **SubPlayer / LLM-Subtrans** 的可视化双语编辑体验。
>
> 📦 **旧版归档说明**：原 v1.0 版本（Windows `Tkinter` 桌面版 + Argos 离线模型）已完整备份保存在 [`legacy-v1`](https://github.com/AtomBoyC/ListenTo-Subtitle-Editor/tree/legacy-v1) 分支。

## 相比 v1.0 解决了哪些核心痛点？

| 维度 | 旧版 v1.0 (`Tkinter + Argos`) | 重构版 Studio v2.0 (`FastAPI + 现代 Web 剪辑台`) |
| --- | --- | --- |
| **翻译准确度** | Argos 小模型逐词硬翻、日译中需经英语二次中转，遇换行 `\n` 或 `<i>` 标签直接把单句切碎孤立翻译（如把 `subtitle editor` 译为「收看字幕编辑」） | **滑动窗口上下文 + 强制术语表 +「直译→审校反思→地道意译」管线**，自动结合前后字幕处理跨时间轴断句、角色口吻与字幕字数 |
| **交互与校对** | 盲盒批处理，无法预览字幕内容、无法修改单句错译、API 密钥重启即丢 | **双语可视化逐行编辑器**：行内即点即改、`Tab` 快速跳行、单句 AI 重翻/口语润色/缩字（提供候选词一键替换）、全文查找替换、撤销/重做 |
| **音视频预览** | 无任何画面或时间轴对照 | **16:9 实时字幕监看台**：支持本地拖入 MP4/WebM/MOV/MP3 零上传同步播放，无视频时亦可按时间轴模拟播放预览双语排版 |
| **格式与编码** | 自动模式遇 GBK/GB18030 旧字幕报错中断；Windows 专属 `Start.cmd` | 自动识别 UTF-8 / UTF-16 / GB18030 / Big5 / Shift-JIS；完整保留 ASS 样式头、注释与 `{\pos}` / `{\an8}` 特效标签；macOS / Linux / Windows 全平台即开即用 |

## 快速启动

### 方式一：Docker / Docker Compose 一键部署（推荐服务器 / NAS / 跨平台）

支持 `linux/amd64` 与 `linux/arm64`（Apple Silicon / 树莓派 / 群晖 / 飞牛 OS / Linux 云服务器）双架构镜像：

**1. 使用 `docker run` 直接启动：**

```bash
docker run -d \
  --name listento-studio \
  -p 8765:8765 \
  -v $(pwd)/data:/data \
  -v $(pwd)/exports:/exports \
  --add-host=host.docker.internal:host-gateway \
  --restart unless-stopped \
  atomboyc/listento-subtitle-editor:latest
```

> 也可拉取 GitHub Container Registry 镜像：`ghcr.io/atomboyc/listento-subtitle-editor:latest`

**2. 使用 `docker compose` 启动：**

```bash
docker compose up -d
```

启动后浏览器访问 **`http://localhost:8765`**，API Key 与字幕项目进度会自动持久化保存在挂载的 `./data` 目录中。

---

### 方式二：本机源码直接运行

#### macOS / Linux

```bash
chmod +x start.sh
./start.sh
```

启动后浏览器会自动打开 **`http://127.0.0.1:8765`**。

#### Windows

双击运行 **`Start.cmd`** 即可自动配置 `.venv` 并打开浏览器工作台。

## 核心工作流

1. **配置 AI 引擎**：点击右上角 **「配置 API Key / 模型名」**，一键选择 **DeepSeek、OpenAI、Google Gemini、硅基流动、通义千问、OpenRouter 或本地 Ollama**，点击「测试 API 连通性」并保存（密钥持久化存储在本地 `.data/settings.json`，被 `.gitignore` 忽略）。
2. **载入字幕与媒体**：拖入 `.srt / .ass / .ssa / .vtt` 字幕文件（或点击顶部「示例字幕」体验），可选拖入本地视频文件联动监看。
3. **提取术语表 & 全文精翻**：
   - 可先点击左侧 **「✨ AI 自动扫描全片提取人名/术语表」** 自动生成背景摘要与统一译名表；
   - 选择 **「✨ 反思精翻」** 或 **「⚡ 极速上下文」**，点击 **「AI 全文精翻」** 实时流式查看每一批翻译进度与审校笔记。
4. **逐行精修与无损导出**：对语速预警行或重点台词点击右侧 `✨` 进行单句润色/缩字，最后点击右上角 **「无损导出」** 一键生成双语或纯译文的 `SRT / ASS / VTT / SSA / TXT`。

## 运行自动化测试

```bash
./.venv/bin/python -m unittest discover -s tests -v
```
