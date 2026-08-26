# 粘附材料 AI 辅助分子模拟预测

面向粘附材料配方早期筛选的 Python + Streamlit MVP。

## 功能

- 输入树脂、增粘剂、填料的 SMILES 和质量配比
- 提取轻量分子结构特征
- 训练可版本化的回归/分类代理模型，预测宽温域黏附、自修复、抗原子氧、抗紫外和增材制造指标
- 生成 PDA@CeO₂ 粗粒化界面拓扑，并支持外部 LAMMPS/GROMACS 任务提交和输出回读
- 为单个候选生成覆盖 CeO₂ 晶面/氧空位/羟基化、PDA 结合、交联树脂温度扫描、界面 MD 和粗粒化模拟的多尺度任务包
- 一键生成并启动多尺度计算，按依赖关系后台调度任务、实时显示进度，并在完成后汇总回写和重训模型
- 批量导入实验 CSV，记录代理/外部计算/实验来源，并自动重训、归档和推荐下一轮实验
- 展示能量轨迹、表面覆盖率、分子特征和特征重要性

当前默认页面仍使用明确标记的物理启发代理结果进行前筛；只有提交并完成 VASP/QE/CP2K/LAMMPS/GROMACS 外部任务后，结果才可作为真实计算数据回写候选库。实验表单支持批次、重复记录、模型版本归档、残差校正和下一轮候选推荐。

“多尺度计算任务包”是可复现的计算清单和数据契约，不虚构真实计算结果。其中粗粒化起始模型可直接生成；真实 DFT 和全原子 MD 在运行前仍需提供经验证的 CeO₂/PDA/树脂结构、交联拓扑、力场、DFT(+U) 参数及计算软件环境。页面中的“需求实现与科学就绪状态”会持续显示这些边界。

“单配方机理探索”以候选编号为入口，自动融合该候选的实验记录、外部 DFT、树脂 MD、界面 MD 和模型预测。融合优先级为实验 > 外部计算 > 代理；每个指标单独记录来源，外部计算没有输出的字段不会被误标为真实数据。标量 MD 数据只作为 25°C 锚点，温度曲线的代理部分会标记为混合来源。

主页面严格按五个板块组织：候选数据库生成 → 多尺度计算方案 → 外部计算任务与结果回写 → AI 筛选与候选排序 → 实验验证、模型更新与再筛选。候选机理与真实数据融合位于 AI 筛选板块内，用于核验排序依据。

## 启动

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

浏览器打开 `http://localhost:8501`。

## 网络受限时的离线启动

如果无法安装 Streamlit、Plotly 或 Pytest，可以直接运行：

```powershell
.\run_offline.ps1
```

浏览器打开 `http://127.0.0.1:8765`。这个版本使用 Python 内置 HTTP 服务和原生 Canvas，复用同一套 AI 预测与粗粒化模拟核心，不需要访问 PyPI。

## 测试

```powershell
pytest
```

## 配置说明

- 复制 `.env.example` 为 `.env`，再填写 MySQL 连接信息；也可以使用 `DATABASE_URL=mysql://用户:密码@主机:端口/数据库`。
- 首次配置后运行 `python scripts/init_database.py`，创建数据表并验证读写权限。
- 默认优先使用 MySQL；未配置 MySQL 时，实验记录和模型版本自动落盘到 `work/adhesive_ai_lab.sqlite3`，可通过 `ADHESIVE_SQLITE_PATH` 修改位置。
- 外部任务记录默认保存在 `work/jobs`，任务命令以参数列表执行，不经过 shell 拼接。
- 一键计算会先自动检测本机的 VASP、Quantum ESPRESSO、CP2K、LAMMPS 和 GROMACS，也可在页面下拉选择求解器或填写自定义任务包装器；点击“保存为项目默认配置”后写入本地 `work/multiscale_profiles.json`。`.env` 命令仍作为高级配置和首次默认值，执行记录保存在 `work/campaign_runs`。
- 自定义包装器命令应包含 `{task_file}`，例如 `python tools/run_dft.py {task_file}`。程序会向包装器传入任务数据契约，由包装器生成并验证真实输入；直接调用求解器时，系统会检查 VASP、QE、CP2K、LAMMPS 或 GROMACS 的必要输入文件。
- 任务按“DFT/体相 MD → 界面 MD → 粗粒化”顺序推进。缺少软件、命令或输入时任务会显示为“阻塞”，不会生成或回写虚构结果。
- 项目标准启动方式仍是 `streamlit run app.py`，离线版使用 `.\run_offline.ps1`。

## 候选库

- 通过 `adhesive_ai.build_candidate_library()` 可生成覆盖 CE、PN、PI、硅橡胶、PU 及其共混/改性体系的候选表。
- 表中包含树脂结构、动态修复单元、PDA@CeO₂ 填料、固化/后固化条件，以及耐高温性、低温韧性、粘附强度、自修复性能和空间环境稳定性等目标指标。
