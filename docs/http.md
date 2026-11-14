# 本地接口

服务只用于本地教研复核，不连接真实人员目录或外部数据源。数据以 UTF-8 JSON 传输；未找到对象返回 404，无效业务输入返回 400，正文超出 8MB 返回 413。

- `GET /health` 返回就绪状态。
- `GET /api/context` 返回产品配置、默认版本、已导入版本、保存记录目录、已发布建议目录与模型版本。
- `GET /api/data?revision=版本` 返回该版本完整原始数据；省略版本使用演示材料。
- `POST /api/datasets` 接收 `label` 和完整 `data`，返回新建或复用的 `revision`。
- `POST /api/explore` 接收可选 `revision` 和 `selection`，返回数据指纹、实际选择和领域描述。
- `POST /api/mastery` 接收 `revision`、`learner` 与可选 `policy`（`prerequisites`、`excluded_items`、`as_of`），返回按知识点的掌握判断、证据明细与下一份练习建议。策略非法（未知引用、自环、循环、错误日期）返回 400。
- `POST /api/backtest` 接收 `revision`、`mode`（`leave_one_student_out` 或 `incremental_known_learners`）与可选 `policy`，返回含泄漏检查、模型与全体率基线对比、冷启动子集和按学生拆分的回测报告。
- `POST /api/notebooks` 使用选择参数和 `title`，服务端重新计算后保存描述，不能由客户端伪造结果。
- `GET /api/notebooks` 返回材料目录，`GET /api/notebooks/编号` 返回冻结描述、选择条件和所用数据；编号非整数返回 400。
- `POST /api/advice` 服务端按当前数据与策略重算掌握判断并发布不可变建议快照；`GET /api/advice` 返回发布目录，`GET /api/advice/编号` 返回冻结的建议、策略与数据版本。

领域描述包含 `summary`、`rows`、`series` 和 `notes`，学习进展另含作答历史。掌握判断的标签只有 `mastered`、`emerging`、`needs_practice`、`unknown` 四种，均附带证据引用与不确定性说明。增添业务能力时需保持现有原始记录、保存材料和已发布建议可读取。
