# 操作调度器

planner 起草完整方案，reviewer 审查准确版本，evaluator 评估实际结果，strategist 提出迭代策略。
scheduler 是第五个独立角色，负责已有操作能力内的调度、故障诊断与有界技术重试。
它没有科学方案写入、审批、任意程序执行或配置修改权限；实际动作由宿主校验并实施。

角色交接、审批版本、科学实验预算与技术重试分别记录。每次工作流启动创建新的会话。
该主版本不包含实验分支的后续提示词、工具和压缩改造。

运行及配置见 [使用说明](USAGE.zh-CN.md)，模块边界见 [架构](ARCHITECTURE.md)，
执行事实见 [统计与执行事实](docs/STATISTICS-AND-EXECUTION-FACTS.md)。
