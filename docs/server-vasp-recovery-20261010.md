# 2026-10-10 服务器 VASP 电子收敛失败诊断与恢复

## 诊断

失败点为服务器 `work/vasp_validation/ceo2-111-baseline-v1/encut/450`，36 原子的无空位 CeO₂(111) O–Ce–O 三层基准结构，Gamma 1×1×1 采样。

本轮使用 4 个 MPI 进程，运行约 71 分钟，在北京时间 2026-10-10 18:24 结束。VASP 返回码为 0，但在第 120 次电子迭代耗尽 `NELM=120`，未达到 `EDIFF=1e-6`；输出明确包含 `EDIFF was not reached (unconverged)`。最后电子步能量变化约 0.028484 eV，前面的能量变化反复振荡。正常程序退出并不代表电子收敛，平台将该点标为 failed 是正确行为。

这次运行使用的是固定 4 进程的共享收敛脚本，因此本轮失败并不是由默认生产启动器改为 8 进程直接引起的。服务器服务 active、NRestarts=0，无异常退出或内存耗尽证据。

## 已采取的恢复措施

确认没有活动 VASP 进程、失败点尚未执行过受控恢复，且 clean_baseline 与预期失败条件一致后，使用项目已有 `scripts/prepare_vasp_scf_recovery.py` 的 `prepare()` 处理函数：

- 将失败日志和所有旧可变输出归档到该任务 `.scf-recovery-backups/20261010T103201Z/`。
- 调整 `ALGO=All`、`TIME=0.1`、`NELM=240`。
- 使用 `ISTART=0`、`ICHARG=2`，从原子电荷开始；已归档的失败 CHGCAR/WAVECAR 不参与新计算。
- 保持 `EDIFF=1e-6`、ENCUT=450、几何结构、赝势、U 值与色散模型不变。
- 记录 `scf_recovery.json`，attempt=1，state=prepared-fresh-atomic。
- 经 `restart_vasp_convergence()` 启动新的共享收敛运行器，脚本快照为验证目录下 `.runner-script.sh`。

恢复计算于北京时间 18:32 启动。初次检查确认运行器及 4 个 VASP 进程正常启动，网页服务正常；这不表示电子收敛已经通过。结果以服务器本轮 OUTCAR 的最终电子收敛与正常结束检查为准。

北京时间 18:33 再次检查：电子迭代日志已推进到第 3 步，4 个 VASP 进程各接近 100% CPU 使用率，无标准错误输出；运行器 active/running，任务尚未完成，approved=false、生产批准文件不存在。首次几步 DAV 为冷启动时的轨道初始化，后续按 ALGO=All 执行正式优化。

本机同套基准的 encut/450 历史恢复曾使用以上设置，在 59 次电子迭代达到收敛；该记录仅用于选择恢复方案，不作为本轮服务器任务的结果或批准证据。官方算法说明见 [VASP IALGO](https://vasp.at/wiki/IALGO)，电子步上限说明见 [VASP NELM](https://vasp.at/wiki/NELM)。

## 后续行为

2026-10-12 核对确认本次 450 eV 恢复已通过，520 eV 也已通过；600 eV 随后耗尽 120 个电子步。该点已另外归档并启动受控恢复，详见 [600 eV 恢复记录](server-vasp-recovery-20261012.md)。

当前共享矩阵共 14 个检查点。运行器按顺序检查各点：通过电子收敛的点才能作为合格结果；后续任何点失败会再次停止推进。完成矩阵后仍需通过原有能量、力及模型收敛比较才能产生生产批准。本次没有伪造完成状态、放宽验收标准或生成生产批准。

本地诊断输出保存在 `outputs/server-vasp-diagnosis-20261010.txt`，恢复输出保存在 `outputs/server-vasp-recovery-20261010.txt`。服务器后台运行器与本地 SSH 诊断会话独立，退出本地诊断连接不终止计算。
