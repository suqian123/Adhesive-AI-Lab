# 2026-10-12 多尺度计算性能优化

按用户选择的“单任务速度和批量吞吐兼顾”完成实测和部署。服务器为 `user@119.147.202.74:8005`，项目 `/opt/adhesive-ai-lab`，网页通过 SSH 隧道 `127.0.0.1:18501` 访问。

## 硬件与测量范围

当日检查：2×Intel Xeon Gold 6530，64 物理核心、128 逻辑线程、4 NUMA 节点，每节点 16 物理核心。内存约 503 GiB、可用约 425 GiB，项目磁盘可用约 607 GiB，无服务 CPU 配额限制。检查时只有共享验证的 4 个 VASP 进程。

基准复制已收敛的 CeO₂(111) `encut/600` 到独立目录：36 原子、ENCUT=600 eV，相同结构/POTCAR/k 点，固定 NBANDS=176、KPAR=1、ALGO=All、TIME=0.1、NELM=8，原子电荷启动。以最后 3 个电子步平均时间为主。4 进程测试开始时与旧验证短暂并行，最后 3 个计时电子步在旧验证结束后。

短段输出均为 `performance-only`，故意不要求电子收敛，不写数据库或提供生产批准。源输入四文件哈希前后一致。服务器报告为 `work/performance_checks/20261012T060337Z/report.json`，本地副本为 `outputs/server-performance-20261012/benchmark-report.json`。

## VASP 实测

| MPI | NCORE | 布局 | 8 步总耗时 / 秒 | 后 3 步平均 / 秒 | MPI 进程秒 / 电子步 |
| ---: | ---: | --- | ---: | ---: | ---: |
| 4 | 2 | 操作系统调度 | 473.08 | 39.37 | 157.46 |
| 8 | 2 | 操作系统调度 | 258.94 | 22.53 | 180.25 |
| 8 | 4 | 操作系统调度 | 234.74 | 20.18 | 161.46 |
| 8 | 2 | NUMA | 240.77 | 20.97 | 167.75 |
| **16** | **4** | **NUMA** | **124.86** | **10.57** | **169.14** |
| 32 | 8 | NUMA | 77.46 | 6.46 | 206.66 |

选择 16 MPI/NCORE=4/KPAR=1/NUMA。相对共享验证原来的 4 MPI，电子步约快 3.7 倍；相对原生产启动器 8 MPI/NCORE=2/无绑定，约快 2.1 倍。32 MPI 更快，但每电子步的 MPI 进程秒比 16 MPI 高约 22%，不作为均衡默认值。

48 MPI 预算可容纳 3 个普通 DFT。按隔离计时估计，3×16 的吞吐不会明显弱于原来的 6×8；尚未测量三个真实生产模型同时计算的吞吐。较大的 PDA 体系及不同 k 点网格仍需对应基准，不能直接套用此加速倍数。

## LAMMPS 检查

对现有 357 粒子 CG 模型，独立副本的 2,000 步测试中，1/2/4/8/16 MPI 的核心循环为 1.087/0.839/0.835/0.680/0.598 秒，包括启动的总耗时约 2.77/3.07/3.97/5.42/8.93 秒。

完整原始 100,000 步检查：2 MPI 在约 58,000 步后报 `Out of range atoms - cannot compute PPPM`，38.52 秒至失败；8 MPI 完成全部步数、退出码 0，总耗时 45.38 秒、核心循环 40.19 秒。因此最终保留 CG=8。一次全程运行成功也不等于模型已批准，仍需动力学稳定性及参数验证。

全原子 MD 尚缺经验证的拓扑/力场输入，保留原来的 8 MPI，没有用安装用的小模型推断生产速度。MD 时间步、温度扫描、总步数和 PPPM 精度均未改动。

## 最终服务器配置

| 配置 | 值 |
| --- | --- |
| 普通 DFT | `ADHESIVE_VASP_MPI_RANKS=16` |
| 新生成 VASP 输入 | `ADHESIVE_VASP_NCORE=4`，KPAR 保持 1 |
| 全原子 MD / CG | `ADHESIVE_LAMMPS_MPI_RANKS=8` / `ADHESIVE_CG_MPI_RANKS=8` |
| 5 图像 NEB | `ADHESIVE_NEB_MPI_RANKS=40`，每图像 8 MPI |
| 共享验证 | `ADHESIVE_VALIDATION_MPI_RANKS=16`、`ADHESIVE_VALIDATION_USE_LAUNCHER=1` |
| 全局 MPI 配额 | `ADHESIVE_MPI_BUDGET=48` |
| VASP 映射 | `ADHESIVE_VASP_MPI_BINDING=numa` |
| 单批次提交上限 | `ADHESIVE_CAMPAIGN_MAX_PARALLEL=6` |
| 旧入口回退值 | `ADHESIVE_MPI_RANKS=8` |
| 每 MPI 的 OpenMP/BLAS 线程 | 1 |

四类标准引擎入口及共享收敛脚本使用 `deploy/compute/mpi_launch.py`。文件锁和进程身份控制跨批次配额，合计超过 48 MPI 时等待，结束释放、失效记录自动清除。当前验证占 16 MPI，余下 32 MPI 可供其他任务；未通过科学验证的任务仍按原规则阻断。任意手工直接执行 mpirun 的命令不经过该资源池。

NEB 保留 IMAGES=5，启动前检查 MPI 与 IMAGES/KPAR/NCORE/NPAR 的整除关系，修正原 8 MPI 对 5 图像不兼容的问题。普通 DFT 每 NUMA 域 4 MPI；FFT 分组不能完整落在域内时回退到操作系统调度。

49 个未运行输入仅修改 NCORE=4，逐文件比较其他 INCAR 行及结构/POTCAR/KPOINTS 哈希，刷新原批次输入哈希。原批次保存的 max_parallel=16 改为 6。完成的验证输入和输出保留。审计为服务器 `work/performance_checks/input-policy-20261012.json`，本地 `outputs/server-performance-20261012/input-policy.json`。

## 部署、备份与验证

代码已同步本机和服务器。服务器 `.env` 使用上述设置；本机 `.env` 保留原引擎配置，`.env.example` 提供新参数。网页已显示“服务器运行”和 48 MPI 配额，默认提交上限 6。

- 配置和代码备份：服务器 `work/performance_checks/config-backup-20261012/`，含 `.env`，仅服务器保管。
- 未运行输入和批次记录备份：`work/performance_checks/input-backup-20261012/`。
- 650 eV 失败输出：`work/vasp_validation/ceo2-111-baseline-v1/encut/650/.scf-recovery-backups/20261012T064000Z/`。

600 eV 已按原标准收敛，耗时约 43 分钟。650 eV 原 Normal/NELM=120 在北京时间 14:08 未收敛而停止。14:40 归档失败输出，用在 450/600 eV 成功的 All/TIME=0.1/NELM=240 原子电荷恢复，NCORE=4/MPI=16，保留 ENCUT=650、EDIFF=1e-6、U、色散和其他物理设置，不复用失败检查点。

14:45 核对：运行器 PID/PGID=269223，3/14 已完成，650 eV 恢复电子步 25，实际每步约 11–12 秒，尚未收敛、approved=false。服务重启前确认运行器不属于网页服务 cgroup，重启后继续推进。后台计算不依赖本机联网；任何新的未收敛仍会停止自动推进。

14:53 更新：650 eV 达到原 EDIFF 标准并正常结束，61 个电子步、总耗时 780.669 秒（约 13 分钟）。14:55 的 1×1×1 k 点验证也已通过，耗时 144.312 秒。14:57 进度为 5/14、failed=0，正在 2×2×1 k 点第 3 电子步，整套验证尚未批准。核对 650 eV 归档前后 INCAR，仅 ALGO/TIME/NELM/NCORE 四项变化；物理参数保留。证据为本地 `outputs/server-performance-20261012/completed650.json`。

本地 MPI/输入/恢复测试 28 项通过、3 项 Linux 测试跳过；服务器加上 Bash 运行器共 34 项通过，最后配置读取调整的 20 项测试也通过。VASP H₂ 达到电子收敛，LAMMPS 安装检查完成 100 步。网页 AppTest 无异常/错误，选择器默认 6、显示 48 MPI 配额。服务 active/running，健康接口正常、NRestarts=0。安装检查及基准均不提供生产批准。

## 方法依据

- [VASP 并行优化及短段计时](https://vasp.at/wiki/Optimizing_the_parallelization)
- [VASP NCORE](https://vasp.at/wiki/NCORE)、[KPAR](https://vasp.at/wiki/KPAR)、[IMAGES](https://vasp.at/wiki/IMAGES)
- [Open MPI 4.1 映射与绑定](https://www.open-mpi.org/doc/v4.1/man1/mpirun.1.php)
- [LAMMPS 性能测量](https://docs.lammps.org/Speed_measure.html)
- [VASP ALGO](https://vasp.at/wiki/ALGO)
