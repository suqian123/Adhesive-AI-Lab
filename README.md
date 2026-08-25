# 粘附材料 AI 辅助分子模拟预测

面向粘附材料配方早期筛选的 Python + Streamlit MVP。

## 功能

- 输入树脂、增粘剂、填料的 SMILES 和质量配比
- 提取轻量分子结构特征
- 训练可版本化的回归/分类代理模型，预测宽温域黏附、自修复、抗原子氧、抗紫外和增材制造指标
- 生成 PDA@CeO₂ 粗粒化界面拓扑，并支持外部 LAMMPS/GROMACS 任务提交和输出回读
- 展示能量轨迹、表面覆盖率、分子特征和特征重要性

当前默认页面仍使用明确标记的物理启发代理结果进行前筛；只有提交并完成 VASP/QE/CP2K/LAMMPS/GROMACS 外部任务后，结果才可作为真实计算数据回写候选库。实验表单支持批次、重复记录、模型版本归档、残差校正和下一轮候选推荐。

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
- 项目标准启动方式仍是 `streamlit run app.py`，离线版使用 `.\run_offline.ps1`。

## 候选库

- 通过 `adhesive_ai.build_candidate_library()` 可生成覆盖 CE、PN、PI、硅橡胶、PU 及其共混/改性体系的候选表。
- 表中包含树脂结构、动态修复单元、PDA@CeO₂ 填料、固化/后固化条件，以及耐高温性、低温韧性、粘附强度、自修复性能和空间环境稳定性等目标指标。
