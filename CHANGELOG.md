# 更新日志

本文件记录 MuseArc 的变更,遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式。

## [Unreleased]

### Added — 新增模块与基础设施

- **core/exceptions.py**:领域异常基类 `MuseArcError` 及 7 个子类(`ValidationError`/`NotFoundError`/`ConflictError`/`BusinessRuleError`/`MediaError`/`PlayerError`/`ConfigError`),统一异常层次
- **core/constants.py**:集中共享常量(`FAVORITES_PLAYLIST_ID`/`FAVORITES_PLAYLIST_NAME`/`DEFAULT_TAG_FIELD`/`DEFAULT_TAG_FIELDS`/偏好等级范围),消除 4 处重复定义
- **infra/logging.py**:统一日志配置模块 `configure_logging()`,提供格式化输出与可选文件落盘
- **tests/**:自动化测试骨架(conftest.py + core 层纯函数测试 43 项,覆盖 `text_normalize`/`pinyin`/`hashing`/`ids`)
- **LICENSE**:GPL v3 许可证
- **CONTRIBUTING.md**:贡献指南(开发环境、架构约束、测试、提交规范)
- **CHANGELOG.md**:本文件
- **config.json.example**:配置文件模板(含全部默认字段)
- **docs/README.md**:文档导航索引
- **.github/workflows/ci.yml**:CI/CD(lint + test)
- **pyproject.toml**:dev 依赖组(pytest/pytest-qt/ruff/mypy/pytest-cov)、ruff/pytest/mypy/coverage 工具配置、license 字段

### Changed — 重构与改进

- **异常层次统一**:`MediaCommandError` 继承 `MediaError`、`PlayerClientError` 继承 `PlayerError`,纳入 `MuseArcError` 体系
- **FAVORITES_PLAYLIST_ID 去重**:`facade.py`/`facade_mixins_import_export.py`/`facade_mixins_library.py`/`facade_mixins_runtime.py` 不再各自定义,统一从 `core.constants` 导入;`repositories_common.py` 改为重导出
- **架构违规修复**:
  - `ui/player_link_page.py` 不再直接 import `infra.player.client`,改从 `app.facade` 导入(`PlayerClient`/`PlayerClientError` 由 Facade 重导出)
  - `ui/import_worker.py` 不再直接 import `services.import_runtime`,改从 `app.facade` 导入(`ImportControl` 由 Facade 重导出)
- **AGENTS.md 项目结构补全**:补全 `core/exceptions.py`、`core/constants.py`、`infra/logging.py`、`repositories_common.py`、全部 `repositories_mixins_*.py`、`media/commands.py`、`media/ffmpeg_tools.py`、`main_window_pages*.py`、`tests/`、新增根目录文件等
- **WAL 模式文档对齐**:AGENTS.md 的"SQLite 并发"条目原写"绝不启用 WAL",与 `connection.py` 实际行为冲突;更新为准确描述(尝试启用 WAL 提升多线程读并发,只读环境回退,绝不使用多连接池)

### Fixed — 修复

- **.gitignore 致命规则**:移除 `test*`(会忽略所有测试文件,阻碍添加测试)
- **.gitignore 配置错误**:`config.toml` 改为 `config.json`(实际配置文件是 JSON 格式)
- **.gitignore 补全**:新增 `realLib/`、`*.db`、`*.muse_playlist.json`、`*.muse_stats.json`、`lyrics_llm_review/`、`*.bak`、`*.log`、`.pytest_cache/`、`.ruff_cache/`、`.mypy_cache/`、`build/`、`dist/` 等规则
- **仓储层 print 副作用**:移除 `repositories_mixins_meta_import.py`、`repositories_mixins_tracks_lyrics.py` 中 3 处 `print()` 调试输出(均为 `logger.info` 的重复,违反"仓储层无副作用"约束)

## [0.2.0] - 2026-07

### 之前版本

- 后端核心:SQLite 索引 + 导入流水线 + 去重 + 曲词匹配 + 导出 + 审查队列
- 媒体后端:PyAV(不依赖 ffmpeg.exe 外部进程)
- 指纹:基于音调转移序列的增强指纹
- UI 首版:侧栏全页可用(歌曲/审查/歌单/文件管理/导入历史/回收站/设置)
- 导入交互:后台线程进度窗口 + 取消 + 断点续传
- LLM 匹配增强:可选 LM Studio
- LRCLIB 补全歌词:批量拉取并自动绑定
