# 粘附材料 AI 辅助分子模拟预测

面向粘附材料配方早期筛选的 Python + Streamlit MVP。

## 功能

- 输入树脂、增粘剂、填料的 SMILES 和质量配比
- 提取轻量分子结构特征
- 训练随机森林代理模型，预测粘附功、界面结合能和密度
- 运行可重复的粗粒化界面吸附模拟
- 展示能量轨迹、表面覆盖率、分子特征和特征重要性

当前模拟器用于配方排序和趋势判断，不替代生产级全原子 MD。后续可把 `simulation.py` 替换为 LAMMPS/GROMACS 适配器，把 `model.py` 的合成数据替换成真实实验数据。

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
