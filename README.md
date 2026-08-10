# MuseArc

MuseArc 是面向超大、混乱歌曲库的清洗与统一管理桌面工具。

核心目标：

- 多来源导入（来源目录随时切换）
- 音频按原格式归档（不强制统一转码）
- 去重（近似重复判定）
- 曲词匹配（文件名不可靠时仍可处理）
- 审查队列（机器不确定条目交由人工确认）
- 歌单导出与播放

## 特色功能

### 核心能力

**批量导入归档与断点恢复流水线**

不是简单的"导入"，而是一个带状态机、增量缓存、按需落盘、可回滚的工业级流水线。支持扫描源目录 → 音频/歌词探测 → 指纹生成 → 去重判定 → 入库或入审查队列。具备断点恢复、暂停/取消、状态清单、路径级快速跳过索引，处理超大批量混乱歌曲库时能极大降低重复工作量。

**音频指纹去重（Chromaprint + hash32 汉明预筛）**

使用 Chromaprint 生成音频指纹，存储 32 位 hash 用于汉明距离预筛（阈值 14），将 O(N×M) 的指纹比对降到接近 O(N×K)。多维度决策（指纹相似度 + 元数据 + 格式优先级 + 质量评分）决定保留新/旧/both/review，区分"高相似近重复""中相似需审查""低相似可能是伴奏/翻唱"等多个层级。

**多版本识别与变体关系管理**

通过标题关键词推断版本类型（LIVE/REMIX/RADIO_EDIT/COVER/MAIN），高相似但版本不同时保留两者并建立 `track_variants` 关系表。同一首歌的现场版、混音版、电台版能共存而非互相覆盖，伴奏/翻唱版本也能自动建立关联提示。

**歌词匹配（规则 + LLM 混合，多级匹配策略）**

歌词与歌曲匹配采用"同目录优先 + 跨目录回退"双池策略。匹配方法分三级：ti 标签精确匹配 title（强匹配，直接绑定）、文件名匹配（需审查）、歌词标题匹配文件名（需审查）。可选 LLM（LM Studio）加权评分（规则 0.55 + LLM 0.45）。已有歌词内容相似度 ≥0.90 时自动跳过，大幅减少人工审查负担。

**LRCLIB 在线歌词补全**

内置 LRCLIB 歌词补全窗口，支持按条件筛选未链接歌词的歌曲，批量请求 LRCLIB API 补全，并提供确认与进度反馈流程。补全本地匹配失败后的在线兜底路径，形成完整的歌词获取闭环。

**审查队列系统（多类型、优先级、分组）**

统一的审查队列支持四种类型（DUPLICATE/LYRICS_MATCH/METADATA_CONFLICT/FILE_ISSUE），带优先级、状态流转（pending/resolved/ignored）、分组键。歌词审查支持按 group_key 聚合显示，减少重复操作，让用户能高效处理大量待审项。

**全量扫描工作**

导入后的"二次清洗"利器。可创建"全量歌曲筛选"工作，支持基于元数据相似（标题+艺术家归一化相同、时长差≤10s）或基于音频指纹相似（指定分数区间）自动筛选疑似重复歌曲，加入工作队列逐项处理。指纹相似度筛选支持多进程并行。

**撤销/重做**

所有破坏性操作（删除、替换、全量扫描工作创建等）记录到 undo_actions 表，支持撤销与重做。误删、误替换可回滚，配合软删除与垃圾箱，处理大型音乐库时几乎不可能"误删丢失"。

**多格式导出与转码**

支持将曲目按统一格式计划导出（mp3/flac/wav/m4a/aac/opus/ogg/wma/ape），同格式直接复制，异格式用 PyAV 转码，可同时导出绑定的歌词文件（.lrc）。适合导出到不同播放器场景。

**内置 + 外部播放器联动**

内置基于 QMediaPlayer 的播放栏（上一首/播放/下一首/进度/音量/设备自动切换）；同时支持通过 TCP JSON Lines 协议与外部播放器联动，支持加载歌单、播放文件、状态查询等命令。双播放器架构满足不同场景需求。

**偏好度评分（Love Score）**

基于播放次数、主动播放比例、完播率、跳过比例、全库播放占比、密集播放信号等多维度加权计算曲目"喜爱度"分数（-100 到 100），让曲库能"自学习"用户偏好。

### 架构设计

**严格四层架构**

`core/` → `infra/` → `services/` → `app/` → `ui/`。core 无副作用只放领域模型与纯函数，infra 封装外部依赖，services 实现业务，app 提供 Facade 唯一入口，ui 仅通过 Facade 访问后端。UI 绝不直接操作数据库或 infra，所有写操作经 Facade。

**Facade + Mixin 拆分**

`MuseArcFacade` 作为唯一外观入口，通过三个 mixin 拆分职责（导入导出 / 库管理 / 运行时与撤销重做），避免 Facade 变成巨型类。仓储层同样按 mixin 拆分，保持单一仓储入口的简洁性。

### 细节亮点

- **文本乱码修复（Mojibake Repair）**：自动检测拉丁扩展字符异常聚集、U+FFFD 替换符等乱码特征，尝试 UTF-8/GB18030/GBK 等多编码修复并评分选最佳。中文音乐库标签编码混乱是历史遗留痛点，此功能可自动修复。
- **文本归一化**：NFKC 标准化 + casefold + 括号内容移除 + 特殊字符统一 + CJK 保留，作为去重与匹配的准确性根基。
- **软删除 + 垃圾箱**：曲目/歌词删除支持软删除（deleted_at 字段），关联歌词可移至垃圾箱目录而非直接删除，支持恢复。
- **去重候选缓存**：按时长窗口缓存去重候选，新入库曲目增量追加，把 SQL 查询从 O(N) 降到 O(1) 缓存命中。
- **断点保存节流策略**：按"每 5 个文件或每 0.8 秒"节流保存断点状态，平衡性能与安全。
- **LLM 隐私友好默认**：LM Studio 默认关闭，仅用于歌词匹配评分绝不用于自动决策，通过本地 OpenAI 兼容接口通信，尊重用户选择权。
- **Chromaprint 自动发现**：Windows 下从环境变量或内置目录查找 DLL，自动处理别名复制与 reload；Linux/macOS 依赖系统库自动发现，加载失败优雅降级。
- **完整文档体系**：中英双语架构详解、快速开始、UI 规范、导入流水线、导出格式规范等。

## 跨平台部署

MuseArc 支持 Windows / Linux / macOS。架构本身良好跨平台，各平台差异主要体现在音频指纹的系统依赖。

### 系统依赖

| 平台 | Chromaprint 安装方式 | 说明 |
|------|---------------------|------|
| Windows | 开箱即用 | DLL 内置在 `tools/chromaprint/bin/`，无需额外安装 |
| Linux | `sudo apt install chromaprint-tools` | Debian/Ubuntu 系；Arch 用 `pacman -S chromaprint` |
| macOS | `brew install chromaprint` | 需先安装 Homebrew |

> 未安装 Chromaprint 时，指纹去重功能不可用（优雅降级），其他功能不受影响。

### 安装

```bash
uv sync
```

> 镜像源说明：`uv.lock` 中可能锁定特定 PyPI 镜像源。如遇网络问题，可设置环境变量 `UV_INDEX_URL` 切换到默认 PyPI 或其他镜像后重新执行 `uv lock`。

### 启动

```bash
# 通用方式（所有平台）
uv run musearc ui

# Windows 快捷启动
start.bat

# Linux/macOS 快捷启动
./start.sh
```

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
├── start.bat                   # Windows 快速启动
└── start.sh                    # Linux/macOS 快速启动
```

架构分层：`core → infra → services → app → ui`。UI 层绝不直接操作数据库或调用 infra，必须经 `MuseArcFacade`。详见 [AGENTS.md](AGENTS.md)。

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
- 可通过"继续未完成导入"恢复中断任务。

## 导入策略（当前）

- 伪装/坏文件：探测或解码失败 => 入审查队列
- 极短文件：按阈值视为试听/异常 => 入审查队列
- 去重：
  - 候选收缩：按时长窗口筛选
  - 指纹相似度：音调转移序列 + 位移容错比对
  - 相似度分段（当前默认）：
    - `0.50 ~ 1.00`：视为同一歌曲，进入"重复保留/替换"决策（优先更高音质）
    - `0.30 ~ 0.50`：不自动定论，进入人工审查
    - `0.10 ~ 0.30`：倾向同曲不同版本（常见为原版/伴奏），默认保留并记录关联提示
    - `0.01 ~ 0.10`：倾向翻唱/异演唱者版本，默认保留并记录关联提示
    - `< 0.01`：默认视为不同歌曲
  - 版本保护：Live/Remix/Radio Edit 优先保留多版本
  - 质量优先：高质量可替换低质量副本
  - 审查延后入库：`REVIEW` 条目不会提前入库，人工在审查页"保存勾选的文件"后才执行入库/替换
- 曲词匹配：
  - 规则分（文件名近似 + token overlap）
  - 可选 LM Studio 增强评分
  - 低置信度自动进审查队列
  - 纯音乐占位歌词：直接跳过，不再额外生成"未匹配歌曲"的人工审查项

## LRCLIB 补全流程

1. 进入菜单"更多 -> 补全歌词"打开非模态窗口。
2. 第一步筛选：
   - 强制条件：满足 API 所需字段（标题/艺术家/专辑/时长）。
   - 可选条件：未链接歌词、不是纯音乐。
3. 第二步确认请求歌曲列表。
4. 第三步执行请求并展示逐条状态（成功/跳过/失败及原因）。
5. 导入成功后自动双向绑定歌词，并给歌曲写入标签 `歌词来自lrclib=是`。

## 名称分组与拼音规则

- 名称分组按 `语言种类 + 首字母` 进行，少于 5 项的小组会合并到"其它(少于5项)"。
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
