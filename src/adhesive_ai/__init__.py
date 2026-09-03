"""AI-assisted adhesive material screening."""

from .pipeline import run_screening
from .candidate_library import build_candidate_library, save_candidate_library
from .multiscale import calculate_interface_and_cg, calculate_quantum_surface, calculate_resin_md
from .screening import closed_loop_screening, load_model, predict_screening, recommend_next_experiments, save_model, screen_candidates, train_screening_models, update_with_experiments
from .engines import compute_md_observables, generate_dft_inputs, generate_md_inputs, parse_dft_output, parse_lammps_thermo
from .coarse_grained import build_cg_interface_model, build_pda_ceo2_force_field
from .result_integration import apply_external_results, closed_loop_with_external_results, update_candidate_with_external_results
from .jobs import JobRecord, get_job_status, list_jobs, parse_job_result, read_job_output, read_job_result_text, register_imported_job, split_job_command, submit_job, update_job_metadata
from .workflow import IntegrationResult, calculation_payload, integrate_completed_job, load_connected_state
from .database import load_candidates
from .campaign import CalculationTask, MultiscaleCampaign, build_multiscale_campaign, campaign_task_frame, requirement_coverage, validate_candidate_contract, write_multiscale_campaign
from .mechanism import fuse_candidate_mechanism, mechanism_provenance_frame
from .campaign_runner import (
    advance_campaign_run, available_engine_profiles, campaign_environment_frame, campaign_run_frame,
    engine_profiles_from_env, get_campaign_run, integrate_campaign_run,
    list_campaign_runs, load_engine_profiles, resume_approved_vasp_tasks,
    save_engine_profiles, start_campaign_run,
)
from .vasp_production import (
    VaspBaseline, build_ceo2_model, dopamine_tetramer, prepare_campaign_dft_task,
    validate_vasp_input_set, write_convergence_suite, write_neb_model, write_vasp_model,
)
from .md_production import (
    DEFAULT_MD_BASELINE,
    MDStructureBaseline,
    prepare_md_structure_baseline,
    provision_bulk_md_inputs,
    provision_interface_md_inputs,
)
from .vasp_resources import install_vasp_resources, load_vasp_resource_config

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
    "JobRecord", "submit_job", "register_imported_job", "get_job_status", "read_job_output", "read_job_result_text", "parse_job_result", "list_jobs", "split_job_command", "update_job_metadata",
    "IntegrationResult", "calculation_payload", "integrate_completed_job", "load_connected_state", "load_candidates",
    "CalculationTask", "MultiscaleCampaign", "build_multiscale_campaign", "campaign_task_frame",
    "requirement_coverage", "validate_candidate_contract", "write_multiscale_campaign",
    "fuse_candidate_mechanism", "mechanism_provenance_frame",
    "advance_campaign_run", "available_engine_profiles", "campaign_environment_frame", "campaign_run_frame",
    "engine_profiles_from_env", "get_campaign_run", "integrate_campaign_run",
    "list_campaign_runs", "load_engine_profiles", "resume_approved_vasp_tasks",
    "save_engine_profiles", "start_campaign_run",
    "VaspBaseline", "build_ceo2_model", "dopamine_tetramer", "prepare_campaign_dft_task",
    "DEFAULT_MD_BASELINE", "MDStructureBaseline", "prepare_md_structure_baseline",
    "validate_vasp_input_set", "write_convergence_suite", "write_neb_model", "write_vasp_model",
    "install_vasp_resources", "load_vasp_resource_config",
]
