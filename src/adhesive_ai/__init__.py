"""AI-assisted adhesive material screening."""

from .pipeline import run_screening
from .candidate_library import build_candidate_library, save_candidate_library
from .multiscale import calculate_interface_and_cg, calculate_quantum_surface, calculate_resin_md
from .screening import closed_loop_screening, load_model, predict_screening, recommend_next_experiments, save_model, screen_candidates, train_screening_models, update_with_experiments
from .engines import compute_md_observables, generate_dft_inputs, generate_md_inputs, parse_dft_output, parse_lammps_thermo
from .coarse_grained import build_cg_interface_model, build_pda_ceo2_force_field
from .result_integration import apply_external_results, closed_loop_with_external_results, update_candidate_with_external_results
from .jobs import JobRecord, get_job_status, list_jobs, parse_job_result, read_job_output, submit_job

__all__ = [
    "run_screening", "build_candidate_library", "save_candidate_library",
    "calculate_quantum_surface", "calculate_resin_md", "calculate_interface_and_cg",
    "train_screening_models", "predict_screening", "screen_candidates", "update_with_experiments",
    "closed_loop_screening",
    "recommend_next_experiments", "generate_dft_inputs", "parse_dft_output",
    "save_model", "load_model",
    "generate_md_inputs", "parse_lammps_thermo", "compute_md_observables",
    "build_pda_ceo2_force_field", "build_cg_interface_model",
    "update_candidate_with_external_results", "apply_external_results", "closed_loop_with_external_results",
    "JobRecord", "submit_job", "get_job_status", "read_job_output", "parse_job_result", "list_jobs",
]
