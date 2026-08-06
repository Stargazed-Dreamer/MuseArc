# AGENTS.md - MuseArc 项目指引

> **这是 AI Agent 的入口文档。** Agent 每次启动时首先读取此文件，了解项目全貌。
> 人类开发者也应阅读此文件，它是项目唯一的"真相源"。

## 项目定位

面向超大混乱歌曲库的清洗与统一管理桌面工具——导入归档、指纹去重、曲词匹配、审查队列、导出播放。

## 项目简介

MuseArc 解决"来自多来源的大量音乐文件命名混乱、格式混杂、重复多、歌词缺失"的问题。核心能力：批量导入归档（哈希+指纹去重）、LRCLIB/LMM 歌词匹配、审查队列管理、歌单导出、内置/外部播放器。

## 技术栈

- **语言/框架**：Python >= 3.11 + PySide6 >= 6.7.0
- **核心依赖**：PyAV >= 12.0.0（媒体解码）、pychromaprint + pyacoustid（音频指纹）、mutagen >= 1.47.0（标签读写）、numpy >= 1.26.0、pypinyin >= 0.53.0
- **数据层**：SQLite + 原生 SQL（无 ORM），Pydantic >= 2.7.0 做数据验证
- **构建工具**：hatchling + uv
- **部署方式**：本地桌面应用，`uv run musearc ui` 启动

## 启动方式

```bash
# 启动 GUI
uv run musearc ui

# CLI 入口
uv run musearc --help

# Windows 快捷启动
start.bat
```

## 关键约束

### 架构分层（必须遵守）

- **必须**严格遵循四层架构：`core/` → `infra/` → `services/` → `app/` → `ui/`
- **绝不**让 `ui/` 直接操作数据库，所有写操作必须通过 `MuseArcFacade`，内部用 `with db.session()` 进入
- **绝不**让 `ui/` 直接调用 `infra/`，必须经过 `services/` 或 `app/facade`
- `core/` 无副作用，只放领域模型、枚举、纯函数
- `infra/` 封装外部依赖（DB、媒体、LLM、播放器），对外暴露函数式接口
- `services/` 实现业务逻辑，可调用 `infra/` 和 `core/`
- `app/facade.py` 是唯一外观入口，通过 mixin 拆分（`facade_mixins_*.py`）

### 数据库

- **必须**先修改 `infra/db/schema.sql`，再修改 `repositories.py` 及相关 mixin
- 仓储类用 mixin 拆分：`repositories_mixins_tracks_lyrics.py`、`repositories_mixins_ops.py` 等
- **绝不**在仓储层做业务逻辑判断，只做数据存取
- 连接管理在 `infra/db/connection.py`，使用 `with db.session()` 上下文

### 媒体处理

- **必须**用 PyAV（`av` 库）做音频解码，**绝不**依赖 ffmpeg.exe 外部进程
- Chromaprint DLL 内置在 `tools/chromaprint/bin/`，Windows 专用
- 指纹提取在 `infra/media/fingerprint.py`，标签读写用 mutagen

### UI 层

- PySide6 界面，主窗口逻辑拆分为 `main_window_logic.py`、`main_window_components.py`、`main_window_helpers.py`
- 页面按功能拆分：`main_window_pages_tracks.py`、`main_window_pages_lyrics.py`、`main_window_pages_ops.py`
- 后台任务用 `import_worker.py`（QThread），长任务用 `long_task.py`
- **绝不**在 UI 线程做耗时操作（数据库查询、媒体处理）

### LLM 集成

- LM Studio 通过 OpenAI 兼容接口通信，配置在 `config/models.py` 的 `LmStudioConfig`
- 默认关闭（`enabled=False`），需用户手动开启
- 仅用于歌词匹配评分，**绝不**用于自动决策

### 常见陷阱

- **PyAV 导入**：`import av` 即可，不要尝试 `pip install ffmpeg-python`，这是两个不同的库
- **Chromaprint 路径**：DLL 路径在运行时通过 `tools/chromaprint/bin/` 解析，**绝不**硬编码绝对路径
- **SQLite 并发**：本项目是单进程桌面应用，使用 `with db.session()` 每次创建并关闭连接（无连接池复用）。`connection.py` 会尝试启用 WAL 模式以提升多线程读取并发（导入 worker 线程读取时不阻塞 UI 查询），在只读/受限环境自动回退；**绝不**使用多连接池
- **QThread**：`import_worker.py` 中的 worker **必须**用信号槽通信，**绝不**直接操作 UI 控件

## 项目结构

```
MuseArc/
├── src/musearc/                # 源代码
│   ├── core/                   # 领域基础层（无副作用）
│   │   ├── models.py           # 数据模型（TrackInsert, LyricsInsert, ImportReport）
│   │   ├── enums.py            # 枚举（DuplicateDecision, ReviewKind, TrackKind）
│   │   ├── exceptions.py       # 领域异常基类（MuseArcError 及子类）
│   │   ├── constants.py        # 共享常量（FAVORITES_PLAYLIST_ID 等）
│   │   ├── hashing.py          # 哈希工具
│   │   ├── ids.py              # ID 生成
│   │   ├── paths.py            # 路径工具
│   │   ├── pinyin.py           # 拼音首字母分组
│   │   └── text_normalize.py   # 文本归一化
│   ├── config/                 # 配置层
│   │   ├── models.py           # 配置模型（RuntimeConfig, LmStudioConfig, UiConfig）
│   │   └── store.py            # 配置持久化
│   ├── infra/                  # 基础设施层
│   │   ├── logging.py          # 统一日志配置（configure_logging）
│   │   ├── db/                 # 数据库
│   │   │   ├── schema.sql      # 表结构 + 索引
│   │   │   ├── connection.py   # 连接管理 + schema 迁移
│   │   │   ├── repositories.py # 仓储主类
│   │   │   ├── repositories_common.py          # 仓储共享辅助与常量重导出
│   │   │   └── repositories_mixins_*.py        # 仓储 mixin（tracks_lyrics, ops, meta_import, playlists, tracks_maintenance）
│   │   ├── llm/client.py       # LM Studio 匹配器
│   │   ├── media/              # 媒体处理
│   │   │   ├── audio_io.py     # PyAV 音频解码
│   │   │   ├── fingerprint.py  # 音频指纹（Chromaprint）
│   │   │   ├── prober.py       # 媒体探测
│   │   │   ├── tag_writer.py   # 标签读写（mutagen）
│   │   │   ├── transcoder.py   # PyAV 转码
│   │   │   ├── commands.py     # MediaCommandError 异常基类
│   │   │   └── ffmpeg_tools.py # ffmpeg 路径查找（保留,当前未使用）
│   │   └── player/client.py    # 外部播放器 TCP JSON Lines 客户端
│   ├── services/               # 领域服务层
│   │   ├── importer.py         # 导入服务
│   │   ├── importer_pipeline.py # 导入流水线
│   │   ├── import_runtime.py   # 导入运行时（ImportControl）
│   │   ├── dedupe.py           # 去重判定
│   │   ├── lyrics_match.py     # 歌词匹配
│   │   ├── exporter.py         # 导出服务
│   │   ├── library.py          # 库管理
│   │   ├── library_ops.py      # 库操作
│   │   └── scanner.py          # 文件扫描
│   ├── app/                    # 应用外观层
│   │   ├── cli.py              # CLI 入口（typer）
│   │   ├── facade.py           # Facade 主类（唯一外观入口,UI 仅经此访问后端）
│   │   ├── facade_mixins_*.py  # Facade mixin（import_export, library, runtime）
│   │   └── action_log.py       # 操作日志（app_logs.json）
│   ├── ui/                     # 界面层（PySide6）
│   │   ├── app.py              # UI 启动入口
│   │   ├── main_window*.py     # 主窗口（拆分为 logic, components, helpers）
│   │   ├── main_window_pages*.py # 主窗口页面（tracks, lyrics, ops, common）
│   │   ├── review_page*.py     # 审查页（拆分为 song, lyrics mixin）
│   │   ├── import_*.py         # 导入相关（dialog, management_page, worker）
│   │   ├── player_bar.py       # 内置播放器栏
│   │   ├── player_link_page.py # 外部播放器连接页
│   │   ├── lrclib_window.py    # LRCLIB 歌词补全窗口
│   │   ├── id3_update_window.py # ID3 标签修复窗口
│   │   ├── settings_page.py    # 设置页
│   │   ├── track_grid.py       # 歌曲网格视图
│   │   ├── track_table_model.py # 歌曲表格模型
│   │   ├── table_models.py     # 通用表格模型
│   │   ├── selection.py        # 选择管理
│   │   ├── sort_controls.py    # 排序控件
│   │   ├── long_task.py        # 长任务工具
│   │   └── theme.py            # 主题管理
│   └── ui_contracts/           # UI 契约层
│       └── import_review.py    # 导入审查接口
├── tests/                      # 自动化测试（pytest）
│   ├── conftest.py             # pytest 全局配置（sys.path 设置）
│   └── core/                   # core 层纯函数测试
├── realLib/                    # 实际音乐库数据（gitignore）
│   ├── db/musearc.db           # SQLite 数据库
│   ├── data/tracks/            # 归档音频文件
│   ├── data/lyrics/            # 归档歌词文件
│   └── manifests/              # 元数据与状态
├── tools/                      # 外部工具
│   ├── chromaprint/bin/        # Chromaprint DLL（Windows）
│   └── export_build.py         # 导出构建脚本
├── docs/                       # 项目文档（见 docs/README.md 索引）
├── .agents/skills/             # Skill 定义文件
│   ├── _index.md               # Skill 索引（入口）
│   └── *.md                    # 各 Skill 定义
├── .trae/rules/                # AI 规则
│   └── project_rules.md        # 功能变更检查清单
├── .github/workflows/          # CI/CD（lint + test）
├── pyproject.toml              # 项目配置、依赖、构建、工具链
├── config.json.example         # 配置文件模板（含全部默认字段）
├── LICENSE                     # MIT 许可证
├── CONTRIBUTING.md             # 贡献指南
├── CHANGELOG.md                # 更新日志
└── start.bat                   # Windows 快速启动
```

## Skill 系统

所有 Skill 定义在 `.agents/skills/` 目录，索引文件为 `_index.md`。

- **Skill 是可执行的知识**：不是文档，而是一组精确的工作流指令
- **唯一目录**：`.agents/skills/` 是 Skill 文件的唯一存放位置
- **索引入口**：`_index.md` 列出所有 Skill 的触发词、依赖、输出

## 测试

```bash
# 项目暂无自动化测试套件
# 验证方式：uv run musearc ui 启动后手动测试
```

## 功能变更检查清单

见 `.trae/rules/project_rules.md`。
