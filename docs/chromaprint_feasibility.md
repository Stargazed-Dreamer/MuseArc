# Chromaprint 接入可行性简报

## 结论

Chromaprint 适配当前项目是可行的，建议以“可选后端”方式逐步接入，而不是直接替换现有指纹实现。

## 与当前功能的匹配度

当前项目在导入链路里依赖指纹完成：
- 重复检测（直接判重 / 进入审查）
- 相似候选推荐（给审查界面）
- 阈值驱动的自动分流

Chromaprint 在“跨工具兼容性”和“稳定可复现”方面有明显优势，适合你提出的“未来可与外部生态兼容”目标。

## 如果接入，改动有多大

整体改动量评估：`中等偏大`（约 4~8 个文件，核心逻辑+配置+打包）。

主要改动点：
- `src/musearc/infra/media/fingerprint.py`
  - 抽象统一接口（`fingerprint_file / encode / decode / similarity`）
  - 新增 Chromaprint 实现（建议独立文件）
- `src/musearc/services/dedupe.py`
  - 重新标定 `keep/review` 阈值，避免误判率上升
- `src/musearc/services/importer.py`
  - 支持新旧指纹 payload 共存（迁移期）
- 配置层（`config models/store`）
  - 新增 `fingerprint_backend = custom|chromaprint`
  - 增加回滚开关
- 打包与运行环境
  - Windows 需处理动态库分发和加载路径

## 风险与注意事项

- 分数语义变化：Chromaprint 的相似度语义与当前自定义算法不同，阈值不能照搬。
- 历史兼容：旧库里已有 `fingerprint_payload`，迁移期必须可继续比对。
- 依赖落地：需要稳定的 Python 绑定与动态库分发方案。

## 建议实施路径

1. 第一阶段：并行后端（默认仍使用现实现），先打通端到端链路。
2. 第二阶段：同一批样本离线评测，校准阈值（误判/漏判对比）。
3. 第三阶段：指标达标后再切默认到 Chromaprint，保留一键回退。

## 工时粗估

- 最小可运行接入：1~2 天
- 阈值校准与回归：2~4 天（取决于样本规模）
- 文档与发布收尾：0.5~1 天
