# MuseArc

MuseArc 是面向超大、混乱歌曲库的清洗与统一管理项目。

核心目标：

- 多来源导入（来源目录随时切换）
- 音频按原格式归档（不强制统一转码）
- 去重（近似重复判定）
- 曲词匹配（文件名不可靠时仍可处理）
- 审查队列（机器不确定条目交由人工确认）
- 核心能力与 UI 解耦，可与播放器工程合并

## 当前实现

- 后端核心：SQLite 索引 + 导入流水线 + 去重 + 曲词匹配 + 导出 + 审查队列
- 媒体后端：`PyAV`（加载底层编解码 DLL，不依赖 `ffmpeg.exe` 参数进程）
- 指纹：基于音调转移序列的增强指纹（比初版能量差分更稳）
- UI 首版：侧栏全页可用（歌曲/审查/歌单/文件管理/导入历史/回收站/设置）
- 导入交互：后台线程进度窗口（0.2s 节流刷新）+ 取消（保留进度或全回退）+ 断点续传
- LLM 匹配增强：可选 LM Studio（OpenAI 兼容接口）
- LRCLIB 补全歌词：菜单“更多 -> 补全歌词”，可批量拉取并自动绑定

## 目录结构

> 完整、权威的项目结构见 [AGENTS.md](AGENTS.md)。此处仅给出核心脉络。

```
MuseArc/
├── src/musearc/                # 源代码
│   ├── core/                   # 领域基础层（无副作用）：models / enums / exceptions / constants / hashing / ids / paths / pinyin / text_normalize
│   ├── config/                 # 配置层：models.py（RuntimeConfig, LmStudioConfig, UiConfig）+ store.py
│   ├── infra/                  # 基础设施层：logging / db（schema+repositories）/ llm / media（PyAV, Chromaprint, mutagen）/ player
│   ├── services/               # 领域服务层：importer / dedupe / lyrics_match / exporter / library_ops / scanner
│   ├── app/                    # 应用外观层：cli + facade（唯一外观入口）+ facade_mixins_* + action_log
│   ├── ui/                     # 界面层（PySide6）：主窗口、页面、审查页、导入、播放器、设置等
│   └── ui_contracts/           # UI 契约层
├── tests/                      # 自动化测试（pytest，目前覆盖 core 层纯函数）
├── realLib/                    # 实际音乐库数据（gitignore）
├── tools/                      # 外部工具：chromaprint/bin/（Windows DLL）、export_build.py
├── docs/                       # 项目文档（见 docs/README.md 索引）
├── .agents/skills/             # Skill 定义文件（_index.md 为索引）
├── .trae/rules/                # AI 规则（功能变更检查清单）
├── .github/workflows/          # CI/CD（ruff lint + pytest）
├── AGENTS.md                   # Agent 入口文档（项目唯一真相源）
├── pyproject.toml              # 项目配置、依赖、构建、工具链
├── config.json.example         # 配置文件模板
├── LICENSE                     # MIT 许可证
├── CONTRIBUTING.md             # 贡献指南
├── CHANGELOG.md                # 更新日志
└── start.bat                   # Windows 快速启动
```

架构分层：`core → infra → services → app → ui`。UI 层绝不直接操作数据库或调用 infra，必须经 `MuseArcFacade`。详见 [AGENTS.md](AGENTS.md)。

## 依赖准备

1. 安装 Python 3.11+
2. 安装依赖

```bash
uv sync
```

> 说明：不需要手动配置 `ffmpeg.exe` 路径。媒体处理走 PyAV 库后端。

## 快速使用

初始化或打开音乐库：

```bash
uv run musearc init --library F:/Music/MyMuseArcLibrary
```

导入来源目录：

```bash
uv run musearc import --source F:/Downloads/MessySongs --library F:/Music/MyMuseArcLibrary
```

搜索：

```bash
uv run musearc search --query 晴天 --library F:/Music/MyMuseArcLibrary
```

导出：

```bash
uv run musearc export --track trk_xxx --track trk_yyy --out F:/Exports --fmt mp3 --bitrate 320k --library F:/Music/MyMuseArcLibrary
```

查看待人工审查：

```bash
uv run musearc review --library F:/Music/MyMuseArcLibrary
```

查看运行时配置（LM Studio、UI 行为等）：

```bash
uv run musearc config show
```

修改运行时配置（仅传入的参数会被更新，其余保持不变）：

```bash
# 启用 LM Studio 并设置端点与模型
uv run musearc config set --lmstudio-enabled --lmstudio-endpoint http://127.0.0.1:1234/v1 --lmstudio-model qwen2.5-7b

# 调整 UI 行为
uv run musearc config set --force-save-threshold 50 --undo-max-actions 200
```

配置文件位于音乐库根目录的 `config.json`，模板见 [config.json.example](config.json.example)。

启动 UI：

```bash
uv run musearc ui --library F:/Music/MyMuseArcLibrary
```

UI 导入说明：

- 导入时弹出进度窗口，展示当前文件名和累计统计。
- 取消时可选：`保留已处理并停止` 或 `全部回退并停止`。
- 可通过“继续未完成导入”恢复中断任务。

## 导入策略（当前）

- 伪装/坏文件：探测或解码失败 => 入审查队列
- 极短文件：按阈值视为试听/异常 => 入审查队列
- 去重：
  - 候选收缩：按时长窗口筛选
  - 指纹相似度：音调转移序列 + 位移容错比对
  - 相似度分段（当前默认）：
    - `0.50 ~ 1.00`：视为同一歌曲，进入“重复保留/替换”决策（优先更高音质）
    - `0.30 ~ 0.50`：不自动定论，进入人工审查
    - `0.10 ~ 0.30`：倾向同曲不同版本（常见为原版/伴奏），默认保留并记录关联提示
    - `0.01 ~ 0.10`：倾向翻唱/异演唱者版本，默认保留并记录关联提示
    - `< 0.01`：默认视为不同歌曲
  - 版本保护：Live/Remix/Radio Edit 优先保留多版本
  - 质量优先：高质量可替换低质量副本
  - 审查延后入库：`REVIEW` 条目不会提前入库，人工在审查页“保存勾选的文件”后才执行入库/替换
- 曲词匹配：
  - 规则分（文件名近似 + token overlap）
  - 可选 LM Studio 增强评分
  - 低置信度自动进审查队列
  - 纯音乐占位歌词：直接跳过，不再额外生成“未匹配歌曲”的人工审查项

## LRCLIB 补全流程

1. 进入菜单“更多 -> 补全歌词”打开非模态窗口。
2. 第一步筛选：
   - 强制条件：满足 API 所需字段（标题/艺术家/专辑/时长）。
   - 可选条件：未链接歌词、不是纯音乐。
3. 第二步确认请求歌曲列表。
4. 第三步执行请求并展示逐条状态（成功/跳过/失败及原因）。
5. 导入成功后自动双向绑定歌词，并给歌曲写入标签 `歌词来自lrclib=是`。

## 名称分组与拼音规则

- 名称分组按 `语言种类 + 首字母` 进行，少于 5 项的小组会合并到“其它(少于5项)”。
- 汉字首字母获取方式：使用 `pypinyin` 的 `lazy_pinyin()` 对首字符转拼音，再取首字母（大写）。
- 英文直接按 A-Z。
- 其它语种（如日文、韩文、特殊符号）当前默认回退到 `#` 组；后续可接入对应语言的转写库做细分。

示例（代码位置：`src/musearc/core/pinyin.py`）：

```python
from pypinyin import lazy_pinyin

ch = "晴"
initial = lazy_pinyin(ch)[0][0].upper()  # Q
```

## 开发与贡献

- 架构约束、目录约定、Skill 系统：见 [AGENTS.md](AGENTS.md)
- 贡献流程、提交规范、测试与 lint：见 [CONTRIBUTING.md](CONTRIBUTING.md)
- 变更历史：见 [CHANGELOG.md](CHANGELOG.md)

```bash
# 安装开发依赖（含 pytest / ruff / mypy）
uv sync --extra dev

# 运行测试
uv run pytest tests/ -v

# Lint
uv run ruff check src/ tests/
```

## 许可证

[MIT License](LICENSE)
