# 2026-10-10 服务器计算引擎配置

服务器：`user@119.147.202.74:8005`，Ubuntu 22.04，应用目录 `/opt/adhesive-ai-lab`。

## 已安装引擎

| 用途 | 引擎 | 安装位置 |
| --- | --- | --- |
| DFT、CeO₂/PDA、NEB | VASP 6.4.2 std | `engines/vasp-6.4.2/bin/vasp_std` |
| 树脂体相 MD、界面 MD、粗粒化 | LAMMPS 30 Sep 2026 | `engines/lammps-30Sep2026/bin/lmp` |

VASP 使用用户本机现有 6.4.2 源码在服务器重新编译，以匹配 Ubuntu 22.04 的运行库。编译工具为 GNU Fortran 11.4、Open MPI 4.1.2、OpenBLAS、FFTW3 和 ScaLAPACK；采用目标服务器 CPU 指令优化。

LAMMPS 使用[官方稳定版 30 Sep 2026](https://github.com/lammps/lammps/releases/tag/stable_30Sep2026)源码编译，启用 MPI、OpenMP，以及 MOLECULE、KSPACE、MANYBODY、EXTRA-MOLECULE、MISC、REACTION、OPENMP。已核验项目需要的 `lj/cut/coul/long`、`fourier`、`cvff`、`pppm` 和 `bond/react` 功能。

## 应用配置

四类引擎配置已写入服务器 `work/multiscale_profiles.json` 和 `.env`。执行入口为：

- DFT：`deploy/compute/run-vasp.sh`，结果为 `OUTCAR`。
- 树脂体相和界面 MD：`deploy/compute/run-lammps.sh -in in.production`，结果为 `log.lammps`。
- 粗粒化：`deploy/compute/run-lammps.sh -in in.cg`，结果为 `log.lammps`。

2026-10-12 实测后，普通 DFT 改为 16 MPI/NCORE=4/NUMA，MD 和 CG 保留 8 MPI，5 图像 NEB 使用 40 MPI。OpenMP/OpenBLAS 固定为 1 线程。新增跨批次共用的 `ADHESIVE_MPI_BUDGET=48`，标准启动器和共享验证超过配额时等待。`ADHESIVE_CAMPAIGN_MAX_PARALLEL=6` 控制每批次提交数，同类 DFT 最多 3 个同时计算，验证也占配额。计时、适用范围、备份及部署核对见 [2026-10-12 性能优化](server-performance-audit-20261012.md)。

共享收敛/恢复运行器已启用标准资源池并改为 16 MPI；本机 WSL 默认仍为 4。手工工具 `scripts/run_vasp_task.py` 的 `--np` 默认仍为 4，直接运行 MPI，不参与资源池。本次配置和代码备份在服务器 `work/performance_checks/config-backup-20261012/`，更早备份在 `work/engine_setup/parallel-20261010T090504388889Z/env.before`。

服务配置 `/etc/systemd/system/adhesive-ai.service.d/compute.conf` 设置单线程环境和 `LimitMEMLOCK=infinity`。应用仍使用独立服务账号 `adhesive-ai`。

本机项目的引擎路径配置保留。代码补充了 Linux 原生启动、进程检查和收敛运行器控制；Windows 继续使用既有 WSL 方式。

## VASP 资源

经用户指定，沿用本机 POTCAR 和色散核：

- `work/vasp_resources/potpaw_PBE.64.tgz`
- `work/vasp_resources/vdw_kernel.bindat`
- 配置：`work/vasp_resources.json`，包含服务器绝对路径及 SHA-256。

上传后的资源校验值与本机原配置一致。资源权限限制为服务账号和管理员可访问；授权源码、二进制和 POTCAR 内容没有加入本机 Git 项目。

## 已完成验证

- 首次安装检查使用应用实际服务账号，通过当时的 4 进程 MPI 启动器运行。
- LAMMPS Lennard-Jones 小体系完成 100 步，热力学输出为有限数值，可被项目解析器读取。
- VASP H₂ 小体系达到 `EDIFF` 并正常结束，最终总能量为 `-6.72345963 eV`，可被项目解析器读取。
- 服务器的引擎适配、计算调度、MD/VASP 输入、原生进程控制及收敛停止保护等 61 项测试通过。
- 验证报告：`work/engine_checks/20261010T083943702872Z/report.json`。

调整为 8 个 MPI 进程后再次以服务账号运行安装检查：LAMMPS 完成 100 步，VASP H₂ 达到电子收敛并正常结束，总能量仍为 `-6.72345963 eV`；四类引擎启动器可用。新增报告为 `work/engine_checks/20261010T090507334491Z/report.json`。网页入口和外部计算页面的 AppTest 均通过，应用及 Nginx 健康检查正常，服务处于 active/running。新进程读取到的默认 MPI 进程数为 8、每批次并行任务数为 6。该检查验证运行可用性，不代表实际生产模型的加速比。

这些是软件安装和运行检查。CeO₂ 各晶面的收敛批准、MD 拓扑/力场及物性验证仍按项目原有规则执行；H₂ 和 Lennard-Jones 检查不会回写候选数据库，也不会生成材料生产批准。历史数据和本机 MD 模型资源没有在本次引擎配置中迁移。2026-10-12 已另外完成历史数据库及现有代理模型文件迁移，详见 [数据迁移记录](server-database-sync-20261012.md)；该迁移不包含 MD 拓扑与计算目录。

## 重新验证

SSH 登录后执行：

```bash
cd /opt/adhesive-ai-lab
sudo -u adhesive-ai .venv/bin/python deploy/compute/verify_engines.py
```

验证每次创建新的 `work/engine_checks/<编号>/`，保留输入、日志和报告。正式任务应从平台按现有审批和数据契约启动。

服务与网页访问方式见 [部署记录](server-instance-20261010.md)。更新前的环境和代码备份保存在服务器 `work/engine_setup/<编号>/backups/`。

源码和编译日志保存在服务器用户的私有目录 `/home/user/.codex-adhesive-engines-20261010/`，可用于排查和复现编译。
