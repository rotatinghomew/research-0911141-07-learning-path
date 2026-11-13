# 数据口径

attempts 按 observed_at 记录作答，presented_at 是当时分配时间，result=correct、wrong、skipped。相同 item 的重做是新 attempt，不是新题。items 含多项 skills、启用时间和退休时间，availability 以当时为准；prerequisites 是知识点间先修关系。selection_reason 是分配日志而非随机化概率。练习编号与学生编号均为虚构。

## 数据版本

`fixtures/data.json` 是可直接导入的一份完整材料，`product.json` 保存页面默认范围。导入必须提交完整对象，不把省略字段解释为删除补丁；以规范化 JSON 的 SHA-256 标识内容，同内容多次导入复用版本。原始记录只追加版本，保存的分析材料始终指向原来版本。

页面中的范围影响当前描述，不回写源数据。`null`、缺记录与实际零不能合并；数据格式问题应在导入时明确报错，而不是生成看似成功的空结果。涉及单位、时间或关系的解释以本文件为准，统计方法和业务决策没有预设结果。

## 目录

`learning_path/domain.py` 提供现行领域校验和描述，`validation.py` 处理基础引用与数值校验，`service.py` 连接领域操作和材料存档，`storage.py` 负责内容寻址数据与历史记录，`server.py` 提供本地接口。`web/app.js` 使用接口构建交互页面，不自行推导独立口径。
