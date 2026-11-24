# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0
#
# The cells of the notebook that revises a Forge run's plan, as a Thinkube
# Control notebook job on the meepsquared kernel, with no GPU. Control adds a
# first cell that sets PARAMETERS, as app/services/runs.py builds them. Each
# "# %%" line starts a cell.
#
# Meep² places the milestones again on the run's last plan and measures
# nothing: the time per step, the memory and one milestone's time are that
# plan's. The revised plan goes where a plan job puts its plan.

# %% Revise: the last plan with only its milestones placed again
import json
from pathlib import Path

import thinkube_models as tkm
from meepsquared.job import Plan

P = PARAMETERS
RUN_DIR = Path.home() / "thinkube" / "models" / "forge" / P["run_id"]
RUN_DIR.mkdir(parents=True, exist_ok=True)
plan = Plan.from_dict(P["previous_plan"]).with_milestones(P["milestones"], P["milestone_growth"], made=P["made"])
print(plan)
PLAN_FILE = RUN_DIR / "plan.json"
PLAN_FILE.write_text(json.dumps(plan.to_dict(), indent=2, default=str))
tkm._mlflow_client().log_artifact(P["mlflow_run_id"], str(PLAN_FILE), "forge")
