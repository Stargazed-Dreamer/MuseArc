# 贡献指南

感谢参与 MuseArc 开发。本指南帮助新开发者快速上手。

## 开发环境准备

### 前置要求

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) 包管理器
- Windows / Linux / macOS（跨平台支持；Windows 开箱即用，Linux/macOS 需安装系统 chromaprint 库：`apt install chromaprint-tools` / `brew install chromaprint`）

### 初始化

```bash
# 克隆后安装依赖(含开发工具)
uv sync --extra dev

# 验证安装
uv run musearc --help

# 运行测试
uv run --extra dev pytest tests/ -v
```

### 日常开发命令

```bash
# 运行全部测试
uv run --extra dev pytest

# 运行测试并生成覆盖率
uv run --extra dev pytest --cov=musearc --cov-report=term-missing

# Lint 检查
uv run --extra dev ruff check src/ tests/

# 自动修复 Lint 问题
uv run --extra dev ruff check --fix src/ tests/

# 类型检查(可选,当前为非强制)
uv run --extra dev mypy src/musearc

# 启动 GUI 手动验证
uv run musearc ui
```

## 架构约束(必须遵守)

项目严格遵循四层架构:`core/` → `infra/` → `services/` → `app/` → `ui/`。

1. **core 层无副作用**:只放领域模型、枚举、常量、异常、纯函数。**绝不** import infra/services/app/ui。
2. **infra 层封装外部依赖**:DB、媒体、LLM、播放器。对外暴露函数式接口。
3. **services 层实现业务逻辑**:可调用 infra 和 core,**绝不**操作 UI 控件。
4. **app/facade.py 是唯一外观入口**:UI 层**仅**通过 `MuseArcFacade` 访问后端。
   - 需要的 infra/services 类型(如 `PlayerClient`、`ImportControl`)已由 Facade 重导出,从 `musearc.app.facade` 导入。
5. **ui 层不直接操作数据库或 infra**:所有写操作经 Facade。
6. **耗时操作不阻塞 UI 线程**:用 `QThread`(`import_worker.py`)或 `long_task.py`。
7. **QThread Worker 仅通过信号槽通信**:**绝不**直接操作 UI 控件。

详细约束见 [AGENTS.md](AGENTS.md) 的"关键约束"章节。

## 数据库变更流程

1. 先修改 `infra/db/schema.sql`(使用 `CREATE TABLE IF NOT EXISTS`)
2. 若为旧库兼容,在 `infra/db/connection.py` 的 `_migrate_schema` 添加列迁移
3. 更新 `core/models.py` 的 Pydantic/dataclass 模型
4. 在对应 `repositories_mixins_*.py` 添加仓储方法(SQL 用 `?` 占位符,**绝不**拼接字符串)
5. 在 services 层添加业务方法
6. 在 Facade mixin 注册
7. 更新 UI(如需要)

参考 Skill:`.agents/skills/db-change.md`。

## 添加新功能流程

参考 Skill:`.agents/skills/add-feature.md`,按 core → infra → services → app → ui 顺序实现。

## 测试

- 测试位于 `tests/` 目录,按 `tests/<层>/test_<模块>.py` 组织
- 优先为 `core/` 纯函数补测试(无副作用、易测试)
- 使用 `tmp_path` fixture 处理临时文件,不依赖外部状态
- 用标准 `assert` 语句,不用 unittest 风格

## 提交规范

- 提交信息描述"为什么"而非"做了什么"
- 不提交 `config.json`(含个人配置)、`realLib/`(用户数据)、`*.db`、`*.muse_playlist.json` 等运行时产物(已在 `.gitignore` 中排除)
- 不提交 `lyrics_llm_review/` 等临时工作区

## 功能变更检查清单

每次新增功能、修改模块、重构代码后,对照 [.trae/rules/project_rules.md](.trae/rules/project_rules.md) 逐项检查。

## Skill 系统

项目使用 Skill 系统管理可复用工作流,定义在 `.agents/skills/`。索引见 `_index.md`。创建新 Skill 参考 `.agents/skills/skill-creator.md`。
