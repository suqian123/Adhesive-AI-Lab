# CE/二硫键网络开发进展，2026-10-30

候选 `CL5-00016`，计算模型 `MDM-00203D56BC3B1B90`。此模型包含用户指定的 BADCy/DCDDS/CTBN 配方；源候选指纹 `FMT-553A4410C5387F8A` 尚未编码这些具体分子身份，正式回写需另行绑定。

## 已生成并检查的连接关系

`scripts/build_ce_networks.py` 根据三个氰酸酯基构成一个三嗪环的计量规则生成化学连接图。随机选择成键对象，禁止同一三元组重复使用同一树脂单体。RDKit 检查价态、分子式和原子数守恒、总电荷、残余氰酸酯基、三嗪环及二硫键数量，三套均通过。单体反应产物对照和完整网络检查两个测试通过。

| 任务 | 转化率 | 三嗪环 | 最大树脂连通分量中的单体数 |
| --- | ---: | ---: | ---: |
| md-resin-x1 | 80.00% | 120 | 211/225 |
| md-resin-x2 | 94.67% | 142 | 225/225 |
| md-resin-x3 | 100.00% | 150 | 225/225 |

三套总组成均为 `C3909H3187N465O454S88`，包含 4916 个非氢原子。CTBN 单独保留为未与树脂共价连接的代表链，这是待检验的建模假设。最大连通分量是图论指标，不能当作实测凝胶含量。文件 `connectivity.json` 的坐标明确为 `null`，`network.smiles` 只定义连接关系；这些文件尚不是三维交联网络或 LAMMPS 输入。随机连接的空间可实现性需要检验，后续应采用装箱、按距离固化和松弛流程。

## 三维装箱与量子化学准备

已生成 CTBN 代表链初始构象和包含 181 个 BADCy、44 个 DCDDS、1 条 CTBN 的 Packmol 输入，预期共 8103 个原子。85 Å 立方盒和 2 Å 分子间间隔是初始装箱设置，不能视作验证过的材料密度。CTBN 的 MMFF 优化达到 500 步上限，尚未确认几何收敛；其构象只用于准备，仍需松弛。

已准备 BADCy、DCDDS 和三苯氧基三嗪参考片段的 XYZ 输入与哈希，服务器执行分子 RHF/6-31G* 几何优化。H₂ 安装自检达到 SCF 收敛，能量为 -1.1267553172 Hartree。服务器最新状态（2026-10-30 08:13 UTC）：BADCy 几何优化收敛，能量 -910.463150441884 Hartree，最大梯度分量 3.3719×10⁻⁵ Hartree/Bohr；DCDDS 正在几何优化（截至 08:29 UTC，geomeTRIC 已输出第 4 步），三嗪参考片段待运行。服务器已装 AmberTools 22 RESP/respgen/espgen 工具；但当前 QM 引擎是 PySCF，`espgen` 的 Gaussian 输出适配不能直接套用，因此尚未生成或拟合 RESP 电荷。需实现并验证 PySCF ESP 网格到 RESP 格式的转换后再执行拟合。所得量子参考仍需完成波函数稳定性、ESP/RESP、键角/扭转标定与独立力场验证。参考：[PySCF SCF](https://pyscf.org/user/scf.html)。

服务器新建隔离工具环境，安装 AmberTools、Packmol、PySCF 和 geomeTRIC，使用清华大学维护的 conda-forge 镜像以解决下载缓慢。镜像说明：[TUNA Anaconda](https://mirrors.tuna.tsinghua.edu.cn/help/anaconda/)。软件实际版本与包来源由 `environment-explicit.txt` 留存。

工具安装已完成，PySCF 版本为 2.14.0。初次装箱有约 0.075 Å 盒边界越界，未通过坐标检查；原输出保留在 `.first-attempt-20261030T073130Z`。将装箱区域收进盒内 1 Å 后重跑，8103 个原子、226 个分子的初始未反应混合物通过原子数、有限坐标、盒边界和分子间距离检查，未发现低于 1.98 Å 的分子间接触；最新服务器 PDB 和验证 JSON 已下载并存入本机同名 `packing/` 子目录。这些检查只批准初始装箱，尚未验证分子内松弛、密度或交联网络空间实现。

服务器任务为 `adhesive-md-toolchain-20261029.service` 和 `adhesive-ce-qm-20261030.service`。量子任务会等待工具就绪后运行初始装箱和量子参考。装箱失败单独保留报告，不会被量子任务完成状态掩盖。所有步骤均不生成 MD 生产批准、不解锁原批次、不回写材料物性。

本机及服务器同名目录：`work/md_baselines/cl5-00016-badcy-dcdds-ctbn-v1/`；服务器前缀 `/opt/adhesive-ai-lab/`。子目录为 `networks/`、`packing/`、`qm_inputs/` 和运行时的 `qm_references/`。

## 2026-10-30 17:10（北京时间）续作

DCDDS 于 17:01:37 完成 RHF/6-31G* 几何优化，能量 -1588.380762240211 Hartree，最大梯度分量 1.8591×10⁻⁵ Hartree/Bohr。三苯氧基三嗪参考分子随后启动几何优化。BADCy 已启动独立的波函数稳定性检查；截至本次快照仍在计算，尚无实际 RESP 电荷拟合结果。

新增 `ce_resp.py` 和 RESP 运行队列，直接按 Amber ESP 格式写出 PySCF 静电势，记录 Bohr 坐标与原子单位势能，避免构造虚假的 Gaussian 输出。采用暴露的 Bondi 球面 Fibonacci 点，训练壳层为 1.4/1.6/1.8/2.0 倍半径；独立检查点采用 1.5/1.7/1.9 倍半径及不同点相位。这个自定义表面网格不等同于 Gaussian MK 网格，网格敏感性和多构象比较仍待完成。方法参考：[PySCF MEP 示例](https://github.com/pyscf/pyscf/blob/master/examples/1-advanced/031-MEP.py)、[Amber ESP 格式实现](https://github.com/Amber-MD/AmberClassic/blob/main/src/antechamber/espgen.c)。

转换流程已做实际工具验证：AmberTools RESP 从合成水模型的静电势恢复了已知电荷 -0.82/+0.41/+0.41 e；PySCF 批量积分与逐点 `int1e_rinv` 路径差异小于 2×10⁻¹⁶ 原子单位。3 个本机测试通过，检查表面排斥、平移不变性、固定宽度字段、单位、符号与不完整输出拒绝。BADCy/DCDDS 的 SDF→AC→respgen 预检通过，原子顺序与坐标保持一致；预检中的零电荷仅是格式占位，未用于实际参数或生产任务。

服务器新增 `adhesive-ce-resp-20261030.service`，按参考分子完成顺序执行波函数内外稳定性检查、ESP 生成和两阶段 RESP 拟合；失败会停止并记录原因。新增 `adhesive-ce-gaff2-audit-20261030.service`，等待 RESP 完成后清点 GAFF2 参数，记录估算项和 `ATTN/NEED/MISSING` 项；仅在没有明确缺项时尝试生成待验证的 Amber 分子拓扑。这项审查尚未开始实际分子参数计算，仍等待 RESP 输出，不能视为力场验证已经通过。

新输出位于 `resp_references/` 和 `gaff2_audit/`。当前量化输出、适配器检查证据、优化坐标和检查点已取回本机，`resp_references/source_manifest.json` 记录源代码和隔离环境清单的 SHA-256。已启动的后台计算各使用 4 个线程；GAFF2 审查队列等待期间不进行数值计算。

后续仍需完成独立扭转势/几何/能量比较、固化位点及 CTBN 的参数化，再建立空间交联与松弛流程并检查体相稳定性。单分子电荷不能直接复制到反应后网络；三套连接图仍不是经过力场验证的三维生产拓扑。
