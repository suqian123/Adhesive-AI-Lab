# CeO₂(111) 电子收敛修复（2026-09-07）

原验证的前五项虽然返回码为零，但均达到 NELM=160，没有达到 EDIFF。
第六项 kpoints/3x3x1 的受控恢复使用 ALGO=Damped、TIME=0.4；
输出停在第 218 步，最后几步能量变化达到数千至上万 eV。
原 CHGCAR 是较粗网格的初始种子，WAVECAR 为零字节，不能称作第 158 步检查点。

原薄片配比为 Ce12O24，最短原子间距 2.343 Å，但底面 Ce 终止、顶面 O 终止。
用 Ce(+4)/O(-2) 形式电荷计算的垂直离子偶极为 −74.977 e·Å；这不是实际 DFT 电荷密度的偶极。
修正切面后保留相同原子数、横向晶格和层数，得到两面 O 终止的 O–Ce–O 三层堆叠，
形式离子偶极在浮点精度内为零。2、3、4 层均有回归测试。
该终止面与文献采用的常规 CeO₂(111) 模型一致：
[Grinter et al., Ce=O Terminated CeO₂ (2021)](https://pmc.ncbi.nlm.nih.gov/articles/PMC8251574/)。

完整旧目录已归档到：
`work/vasp_validation/archive/ceo2-111-baseline-v1-polar-20260907T063255Z/`。
归档保留所有旧输入、输出、电荷、波函数和恢复记录，没有删除旧计算结果。
新的 12 项验证仍使用 `work/vasp_validation/ceo2-111-baseline-v1/`，从零重新验证。
原数据不能用于新终止面的数值批准；已有旧 (111) 生产任务包会被静态校验拦截，需要重新生成。

物理设置继续使用已确认的 a=5.411 Å、PBE-D3(BJ)、Ce Ueff=4.5 eV、ISPIN=2、
干净化学计量基准的初始 MAGMOM=0、LMAXMIX=6、LDIPOL 和 EDIFF=1E-6。
求解采用 ALGO=Normal、较小混合幅度、干净原子电荷初始化，并保存 WAVECAR。
首项为 encut/450；只有正常结束且最终电子循环达到 EDIFF 才推进下一项。
这仍是待验证的计算设置，结构修正不等于已经证明数值收敛。

代码防护：

- 完成计数、检查点复用、最终批准统一核实 OUTCAR 的最终电子循环；返回码零不足以通过。
- 跨任务 CHGCAR 还要求 POSCAR、POTCAR 一致；WAVECAR 额外要求 KPOINTS 一致。
- 未通过电子收敛的任务返回失败，禁止页面自动重复启动。
- 日志超过 15 分钟无更新只提示核实进程，不能单独证明进程退出。
- 新启动器保存运行脚本快照及日志，避免运行中的 Bash 读到后来修改的脚本。
- WSL 进程探测显式处理输出编码，避免 GBK 解码线程异常。

状态文件为 `runner_control.json`、`status.tsv` 和各任务的 `run_status.json`；
电子迭代见对应目录的 `vasp.stdout.log` 与 `OUTCAR`。
`scripts/start_vasp_validation.py` 提供带重复进程检查的启动入口。
失败后须先诊断并归档失败尝试，不能反复调用启动入口强行续跑。

测试入口：Windows 运行 `python -m pytest`；WSL 运行
`python3 /mnt/e/Adhesive-AI-Lab/tests/test_vasp_runner_shell.py`。
后者使用假的 MPI 程序验证返回码零但未收敛的停止行为，不启动真实 VASP。
