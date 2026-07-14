from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_eval_module():
    script_path = Path(__file__).resolve().parent / "jobscout_auto_eval.py"
    spec = importlib.util.spec_from_file_location("jobscout_auto_eval", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_eval_script_uses_server_sample_data_and_local_outputs():
    module = load_eval_module()

    server_dir, output_dir = module.resolve_paths("eval_outputs")

    assert server_dir.name == "server"
    assert (server_dir / "sample_data" / "sample_resume.md").exists()
    assert output_dir.parent == Path(__file__).resolve().parent
    assert output_dir.name == "eval_outputs"
