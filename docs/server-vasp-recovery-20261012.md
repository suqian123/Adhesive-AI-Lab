# 2026-10-12 服务器 CeO₂(111) ENCUT=600 恢复

服务器网页 `127.0.0.1:18501` 的“共享 VASP 验证有 1 项未通过电子收敛检查”来自服务器自身的验证目录，与数据库迁移无关。

## 失败原因

本次诊断确认 CeO₂(111) 的 `encut/450`、`encut/520` 已正常结束并达到电子收敛；当前失败点为 `encut/600`。该点于北京时间 2026-10-10 22:25 结束，使用 `ALGO=Normal`、`NELM=120`，耗时约 1 小时 54 分钟。程序返回码为 0，但最终电子循环未达到 `EDIFF=1e-6`，OUTCAR 明确记录未收敛。

最后电子步能量变化约 0.019113 eV，此前能量变化反复振荡，因此仅增加原算法的步数缺乏充分依据。服务器应用保持 `active/running`、NRestarts=0，恢复前未发现活动 VASP 进程。

## 已执行恢复

北京时间 2026-10-12 11:21，使用已更新的 `scripts/prepare_vasp_encut600_recovery.py` 归档失败输出并准备一次受控重算：

- `ALGO=All`、`TIME=0.1`、`NELM=240`；保留 `EDIFF=1e-6`。
- `ISTART=0`、`ICHARG=2`，从原子电荷启动，失败运行的 CHGCAR/WAVECAR 均归档，不复用较低 ENCUT 点的检查点。
- POSCAR、POTCAR、KPOINTS 的 SHA-256 在准备前后相同；ENCUT、U、色散、磁性、偶极修正与展宽参数逐项核对未变。
- 标记 `scf_recovery.json` 为 attempt=1、state=prepared-fresh-atomic。
- 通过 `restart_vasp_convergence()` 启动共享运行器及其脚本快照，仍使用 4 个 MPI 进程，后台运行不依赖本机 SSH 会话。

选择直接优化算法参考了同一服务器 `encut/450` 的成功恢复，同时符合 [VASP ALGO 官方说明](https://vasp.at/wiki/ALGO) 对 Normal 失败及 DFT+U 体系的建议。这是待验证的恢复方案，不能保证该点或后续各点收敛。

失败输出归档在服务器：

```text
/opt/adhesive-ai-lab/work/vasp_validation/ceo2-111-baseline-v1/encut/600/.scf-recovery-backups/20261012T032132Z/
```

更新前的恢复脚本备份：`work/engine_setup/vasp-scf-20261012/prepare_vasp_encut600_recovery.py.before`。本机原有 VASP 验证输入及结果保持原状，只同步了恢复工具代码和测试。

## 状态与验证

恢复后的首次检查确认 `encut/600` 为 active/running，completed=2、failed=0、total=14、approved=false。failed=0 表示原失败点已进入新一轮计算，不表示已经通过。未生成生产批准文件。

北京时间 11:23 再次核对：电子迭代日志已推进到第 1 步，4 个 VASP 进程各接近 100% CPU，无标准错误输出。运行器已脱离 SSH 会话、父进程为 PID 1；网页 AppTest 无运行异常和错误元素，已显示“当前 encut/600 · 正式收敛计算”。

本机相关测试 14 项通过、1 项 Linux 专用测试跳过；服务器相同测试 15 项全部通过。测试涵盖归档及物理参数保留、错误晶面拒绝、重复恢复拒绝、最终电子收敛检查和 Linux 进程控制。

后续状态读取服务器 `runner_control.json`、`status.tsv`、各点 `run_status.json`、`vasp.stdout.log` 和 OUTCAR。只有最终电子循环达到 EDIFF 且正常结束才能计为完成；600 eV 通过后运行器会继续下一点，任何新失败都会再次停止。整套能量、力与模型收敛检查完成并满足原阈值后才可能产生生产批准。

本地保存了诊断、恢复及后续核对记录：`outputs/server-vasp-recovery-20261012/`。

## 后续：600 eV 通过及 650 eV 恢复

600 eV 已在约 43 分钟后达到原 EDIFF 标准并正常结束，验证推进至 650 eV。650 eV 原 Normal/NELM=120 计算于北京时间 14:08 达到上限而未收敛，自动推进再次停止。

同日 14:40 归档失败输出，使用一次 All/TIME=0.1/NELM=240 原子电荷恢复。结合服务器实测采用 16 MPI/NCORE=4/NUMA，保留 ENCUT=650、EDIFF=1e-6 及物理模型。归档在 `encut/650/.scf-recovery-backups/20261012T064000Z/`。恢复工具新增受限的 `--cutoff 650` 参数、输入 cutoff 匹配检查及服务器 NCORE 设置读取，重复恢复仍被阻止。

14:45 检查恢复处于运行中、电子步 25，实际每步约 11–12 秒，3/14 已完成、approved=false。性能结果、配额和验证记录见 [多尺度性能优化](server-performance-audit-20261012.md)，本地证据在 `outputs/server-performance-20261012/`。运行中不等于通过，不复用未收敛检查点，不生成生产批准。

14:53，650 eV 已按原 EDIFF 收敛并正常结束：61 个电子步，780.669 秒，约 13 分钟。14:55 的 1×1×1 k 点也已通过，14:57 进度为 5/14，继续 2×2×1 k 点，failed=0、approved=false。INCAR 核对确认仅 ALGO/TIME/NELM/NCORE 变化。详细核对记录为 `outputs/server-performance-20261012/completed650.json`。
