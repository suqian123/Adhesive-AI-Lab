# 文献数据校准

文献数据保存在独立的 `literature_results` 表，不会写入 `experimental_results`，也不会被标记为自有实验。

每行 CSV 必须包含以下字段。系统会为每个文献样品生成独立的 `LIT-…` 候选编号和配方指纹，不会修改项目的 `CL-…` 候选库：

```text
doi,sample_label,source_location,data_license,conditions,test_temperature_c,resin,dynamic_unit,filler_type,filler_pct,crosslink_density,wide_temp_adhesion_mpa,healing_efficiency_pct,atomic_oxygen_retention_pct,uv_retention_pct,am_feasibility
```

- `doi`：可解析的文献 DOI 或规范 DOI 字符串。
- `source_location`：数据在文献中的确切位置，例如 `Table 2, row 4` 或 `Figure 5 digitized`。
- `data_license`：数据集或出版物适用的许可/复用依据。
- `conditions`：JSON 对象，记录固化制度、湿度、应变速率、暴露剂量等与性质可比性相关的条件。
- `sample_label`：论文中的样品编号，例如 `PU3` 或 `DRPU-10`。
- 尽量补全树脂、动态键、填料、填料含量和交联密度等配方描述符；缺失描述符会降低模型适用性。

示例：

```csv
doi,sample_label,source_location,data_license,conditions,test_temperature_c,resin,dynamic_unit,filler_type,filler_pct,crosslink_density,wide_temp_adhesion_mpa,healing_efficiency_pct,atomic_oxygen_retention_pct,uv_retention_pct,am_feasibility
10.1234/example,PU-3,Table 2 row 4,CC BY 4.0,"{""humidity_pct"":50,""cure_temperature_c"":180}",25,PU,DielsAlder,PDA@CeO₂,5,0.65,31.2,82,88,86,74
```

导入并训练：

```powershell
.\.venv\Scripts\python.exe scripts\import_literature_results.py path\to\literature.csv
.\.venv\Scripts\python.exe scripts\train_literature_calibrated_model.py
```

## Conditioned adhesion data

Do not merge measurements from different temperatures or substrates into one wide-temperature value. Retain
`test_temperature_c`, `substrate_material`, `substrate_grade`, `surface_condition`,
`surface_cleaning`, `adhesion_test_method`, `surface_roughness_ra_um`,
`bondline_thickness_mm`, and `test_environment` whenever known. The standard matrix is
-180, -120, -60, 25, 80, 120, and 150 C. The first reference condition is aluminium alloy
6061-T6, solvent-degreased, 25 C, and lap shear. Literature measurements without all of
these conditions remain traceable reference data and must not calibrate a substrate-temperature curve.

训练产物会写入 `work/models/` 和 `model_versions`，其元数据会单独记录 `literature_rows` 与 `experimental_rows`。训练前应按 DOI/论文来源留出测试集，并只纳入定义、单位和测试条件可比的记录。
