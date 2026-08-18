"""Offline smoke test for the scientific core.

Run with the bundled Python runtime when Streamlit is not installed yet:
    python run_demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from adhesive_ai.pipeline import run_screening


if __name__ == "__main__":
    result = run_screening(
        resin_smiles="CC(C)C(=O)O",
        tackifier_smiles="c1ccccc1O",
        filler_smiles="O=[Si](O)O",
        resin_ratio=65,
        tackifier_ratio=25,
        filler_ratio=10,
        temperature_c=25,
        humidity_pct=45,
        simulation_steps=220,
    )
    print("离线核心验证通过")
    print(result["combined"])
