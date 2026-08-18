"""Dependency-light browser app for environments without PyPI access."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).parent / "src"))

from adhesive_ai.features import (
    FORMULATION_FEATURE_LABELS,
    MOLECULE_FEATURE_LABELS,
    MOLECULE_KIND_LABELS,
)
from adhesive_ai.pipeline import run_screening

HOST = "127.0.0.1"
PORT = 8765


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Adhesive AI Lab</title>
  <style>
    :root { --ink:#17212b; --muted:#657383; --line:#d9e0e7; --accent:#0f766e; --warm:#f6f3ed; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:Segoe UI,Microsoft YaHei,sans-serif; color:var(--ink); background:#fbfcfd; }
    main { max-width:1200px; margin:auto; padding:28px 22px 50px; }
    header { border-bottom:1px solid var(--line); padding-bottom:18px; margin-bottom:20px; }
    .eyebrow { color:var(--accent); font-size:12px; font-weight:700; letter-spacing:.12em; }
    h1 { margin:7px 0; font-size:32px; }
    header p { margin:0; color:var(--muted); }
    .layout { display:grid; grid-template-columns:310px 1fr; gap:22px; }
    aside { background:var(--warm); border:1px solid var(--line); border-radius:8px; padding:18px; }
    label { display:block; color:var(--muted); font-size:13px; margin:14px 0 5px; }
    input { width:100%; padding:9px 10px; border:1px solid #bec9d3; border-radius:5px; background:white; font-size:14px; }
    button { width:100%; margin-top:18px; border:0; border-radius:5px; padding:11px; color:white; background:var(--accent); font-weight:700; cursor:pointer; }
    button:disabled { opacity:.6; cursor:wait; }
    .metrics { display:grid; grid-template-columns:repeat(5,1fr); gap:10px; }
    .metric { background:white; border:1px solid var(--line); border-radius:8px; padding:14px; min-height:100px; }
    .metric span { color:var(--muted); font-size:12px; }
    .metric strong { display:block; margin:7px 0 2px; font-size:25px; }
    .metric small { color:var(--muted); }
    .panels { display:grid; grid-template-columns:1.25fr 1fr; gap:14px; margin-top:14px; }
    section { background:white; border:1px solid var(--line); border-radius:8px; padding:14px; }
    h2 { font-size:16px; margin:0 0 10px; }
    canvas { width:100%; height:250px; border-top:1px solid #eef1f4; }
    table { border-collapse:collapse; width:100%; font-size:12px; }
    td,th { text-align:left; padding:7px; border-bottom:1px solid #edf0f2; }
    .status { color:var(--muted); font-size:13px; min-height:20px; margin-top:12px; }
    @media (max-width:850px) { .layout,.panels { grid-template-columns:1fr; } .metrics { grid-template-columns:repeat(2,1fr); } }
  </style>
</head>
<body>
<main>
  <header>
    <div class="eyebrow">MOLECULAR SCREENING WORKSPACE</div>
    <h1>粘附材料 AI 辅助分子模拟预测</h1>
    <p>离线浏览器版本：不依赖 Streamlit、Plotly 或 PyPI 网络访问。</p>
  </header>
  <div class="layout">
    <aside>
      <h2>配方输入</h2>
      <label>树脂 / 聚合物 SMILES</label><input id="resin" value="CC(C)C(=O)O">
      <label>增粘剂 SMILES</label><input id="tackifier" value="c1ccccc1O">
      <label>填料 / 助剂 SMILES</label><input id="filler" value="O=[Si](O)O">
      <label>树脂配比</label><input id="resin_ratio" type="number" value="65">
      <label>增粘剂配比</label><input id="tackifier_ratio" type="number" value="25">
      <label>填料配比</label><input id="filler_ratio" type="number" value="10">
      <label>温度 (°C)</label><input id="temperature_c" type="number" value="25">
      <label>湿度 (%)</label><input id="humidity_pct" type="number" value="45">
      <label>模拟步数</label><input id="simulation_steps" type="number" value="650">
      <button id="run" onclick="runPrediction()">运行 AI + 模拟</button>
      <div class="status" id="status"></div>
    </aside>
    <div>
      <div class="metrics" id="metrics"></div>
      <div class="panels">
        <section><h2>界面吸附轨迹</h2><canvas id="energy"></canvas></section>
        <section><h2>模型关注的特征</h2><canvas id="importance"></canvas></section>
      </div>
      <section style="margin-top:14px"><h2>分子特征</h2><div id="molecules"></div></section>
    </div>
  </div>
</main>
<script>
function value(id) { return document.getElementById(id).value; }
function esc(text) { return String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function drawLine(canvas, values, color) {
  const ratio = window.devicePixelRatio || 1, rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * ratio; canvas.height = 250 * ratio;
  const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio);
  const w = rect.width, h = 250, min = Math.min(...values), max = Math.max(...values);
  ctx.strokeStyle = '#d9e0e7'; ctx.beginPath(); ctx.moveTo(34,12); ctx.lineTo(34,h-26); ctx.lineTo(w-10,h-26); ctx.stroke();
  ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
  values.forEach((v,i) => { const x=34+i*(w-48)/Math.max(1,values.length-1); const y=12+(max-v)*((h-38)/Math.max(1e-9,max-min)); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }); ctx.stroke();
}
function drawBars(canvas, items) {
  const ratio = window.devicePixelRatio || 1, rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * ratio; canvas.height = 250 * ratio;
  const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio);
  const w=rect.width, row=26, max=Math.max(...items.map(x=>x.value),1);
  items.forEach((item,i) => { const y=18+i*row, bar=(w-150)*item.value/max; ctx.fillStyle='#657383'; ctx.font='12px Segoe UI'; ctx.fillText(item.name.slice(0,22), 4, y+12); ctx.fillStyle='#d97706'; ctx.fillRect(125,y,w-150<0?0:bar,14); });
}
function render(data) {
  const c=data.combined, specs=[['综合粘附功',c.adhesion_work_mj_m2,'mJ/m²'],['界面结合能',c.interface_energy_mj_m2,'mJ/m²'],['密度估计',c.density_g_cm3,'g/cm³'],['表面覆盖率',(c.surface_coverage*100).toFixed(1),'%'],['稳定性',(c.stability_score*100).toFixed(1),'%']];
  document.getElementById('metrics').innerHTML=specs.map(x=>`<div class="metric"><span>${x[0]}</span><strong>${x[1]}</strong><small>${x[2]}</small></div>`).join('');
  drawLine(document.getElementById('energy'), data.energy, '#0f766e');
  drawBars(document.getElementById('importance'), data.importance.map(x=>({name:data.formulation_feature_labels[x.feature] || x.feature,value:x.importance})));
  const rows=Object.entries(data.molecules).flatMap(([kind,values])=>Object.entries(values).map(([name,value])=>`<tr><td>${esc(data.molecule_kind_labels[kind] || kind)}</td><td>${esc(data.molecule_feature_labels[name] || name)}</td><td>${esc(value)}</td></tr>`));
  document.getElementById('molecules').innerHTML='<table><tr><th>组分</th><th>特征</th><th>值</th></tr>'+rows.join('')+'</table>';
}
async function runPrediction() {
  const button=document.getElementById('run'), status=document.getElementById('status'); button.disabled=true; status.textContent='计算中...';
  const body=new URLSearchParams({resin_smiles:value('resin'),tackifier_smiles:value('tackifier'),filler_smiles:value('filler'),resin_ratio:value('resin_ratio'),tackifier_ratio:value('tackifier_ratio'),filler_ratio:value('filler_ratio'),temperature_c:value('temperature_c'),humidity_pct:value('humidity_pct'),simulation_steps:value('simulation_steps')});
  try { const response=await fetch('/predict',{method:'POST',body}); const data=await response.json(); if(!response.ok) throw new Error(data.error||'预测失败'); render(data); status.textContent='完成'; }
  catch(error) { status.textContent=error.message; } finally { button.disabled=false; }
}
runPrediction();
</script>
</body>
</html>"""


def _number(form: dict[str, list[str]], name: str, default: float) -> float:
    try:
        return float(form.get(name, [default])[0])
    except (TypeError, ValueError):
        return default


def _prediction(form: dict[str, list[str]]) -> dict:
    result = run_screening(
        resin_smiles=form.get("resin_smiles", ["CC(C)C(=O)O"])[0],
        tackifier_smiles=form.get("tackifier_smiles", ["c1ccccc1O"])[0],
        filler_smiles=form.get("filler_smiles", ["O=[Si](O)O"])[0],
        resin_ratio=_number(form, "resin_ratio", 65),
        tackifier_ratio=_number(form, "tackifier_ratio", 25),
        filler_ratio=_number(form, "filler_ratio", 10),
        temperature_c=_number(form, "temperature_c", 25),
        humidity_pct=_number(form, "humidity_pct", 45),
        simulation_steps=int(_number(form, "simulation_steps", 650)),
    )
    simulation = result["simulation"]
    return {
        "combined": result["combined"],
        "molecules": result["molecules"],
        "formulation_feature_labels": FORMULATION_FEATURE_LABELS,
        "molecule_feature_labels": MOLECULE_FEATURE_LABELS,
        "molecule_kind_labels": MOLECULE_KIND_LABELS,
        "importance": result["importance"].to_dict("records"),
        "energy": simulation.energy[::max(1, len(simulation.energy) // 180)].tolist(),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, content: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        if self.path == "/":
            self._send(HTML.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send(b"Not found", "text/plain; charset=utf-8", 404)

    def do_POST(self) -> None:
        if self.path != "/predict":
            self._send(b"Not found", "text/plain; charset=utf-8", 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            payload = json.dumps(_prediction(form), ensure_ascii=False).encode("utf-8")
            self._send(payload, "application/json; charset=utf-8")
        except Exception as exc:
            payload = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
            self._send(payload, "application/json; charset=utf-8", 400)

    def log_message(self, format: str, *args: object) -> None:
        print(format % args)


if __name__ == "__main__":
    print(f"离线版已启动: http://{HOST}:{PORT}")
    HTTPServer((HOST, PORT), Handler).serve_forever()
