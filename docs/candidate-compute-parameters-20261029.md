# 候选计算参数补全记录

2026-10-29 对服务器 MySQL 的全部 1260 条候选执行第一轮参数研究与缺失登记，其中 v4 为 360 条、v5 为 900 条。树脂包括 CE 262、Silicone 257、PN 247、PU 247、PI 247 条。参数保存在 `candidates.properties.computation_parameters`，批次为 `compute-parameter-research-20261029-v1`。

所有候选均关联了有明确适用范围的原始研究路线参考。参考中的具体材料、固化温度、物性、速率等没有直接转移为候选的数值。LAMMPS 官方方法资料与候选参数分开记录，文档默认时间步长没有作为稳定步长批准。原有物理启发代理预测没有升级为经验证的计算参数。

目前只有用户明确指定的 CL5-00016 可以记录 BADCy/DCDDS 单体身份、CTBN 牌号、供应商典型 Mn 3150 g/mol 和官能度 1.8、用户提供的约 26% AN 规格，以及三个转化率建模假设。这些属于模型提案；原有配方指纹尚未编码具体单体和配比。其余 1259 条候选没有足够结构证据，单体和商品参数留空。

30 个计算参数字段均有缺失登记，未知值保存为 JSON `null`、CSV 空单元格。所有候选的力场文件、电荷、时间步长、截断、交换速率、DFT 截断能等正式求解器参数仍为空；生产就绪候选数为零。应先确定具体结构、网络、表面及工况，再校验文献参数适用性。

应用前备份：`/opt/adhesive-ai-lab/work/parameter_enrichment/20261029T094428Z/candidates-before.json`。事务内核对全部候选身份后更新 1260 行。应用后逐条对照备份，确认除新增的 `computation_parameters` 外原有 properties 全部一致，候选编号、配方指纹、版本和树脂/动态键字段一致。网页健康检查通过。相关证据隔离测试 2 项通过。

本地导出：`work/parameter_enrichment/research-20261029/candidate_parameters.csv`；同名目录也已保存服务器。逐字段来源和空缺见 `plan.json`，统计见 `summary.json`。来源目录见 `resources/compute_parameter_sources.json`。脚本 `scripts/enrich_candidate_compute_parameters.py` 可重建清单，遇到已有不同参数记录会停止，避免覆盖已有参数。
