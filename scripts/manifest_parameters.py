"""The parameters a template declares, and the values an application was deployed with.

manifest.yaml lists a template's parameters under `parameters:`. Each one given a
value at deploy time becomes an environment variable of the application's
containers, and nothing else of the deployment's variables does. The values are
kept in the application's metadata ConfigMap under PARAMETERS_KEY, so regeneration
renders the same variables.

Both deploy paths use this module: the first deploy in deploy_application.py and
regeneration in manifest_generator.py.
"""

import json
from typing import Dict, List, Mapping

PARAMETERS_KEY = 'parameters'


def declared_parameter_names(manifest: Mapping) -> List[str]:
    if 'parameters' not in manifest:
        raise ValueError(
            "manifest.yaml has no 'parameters' list. "
            "Declare 'parameters: []' when the template takes none."
        )
    return [p['name'] for p in manifest['parameters']]


def deployed_values(names: List[str], params: Mapping) -> Dict[str, str]:
    """The declared parameters given a value at deploy time."""
    return {name: str(params[name]) for name in names if params.get(name)}


def encode(values: Mapping[str, str]) -> str:
    return json.dumps(dict(values), sort_keys=True)


def recorded_values(names: List[str], config_map_data: Mapping[str, str], app_name: str) -> Dict[str, str]:
    """The declared parameters' values, as the metadata ConfigMap records them."""
    if not names:
        return {}
    if PARAMETERS_KEY not in config_map_data:
        raise RuntimeError(
            f"The metadata ConfigMap of {app_name} does not record the values of its "
            f"parameters ({', '.join(names)}). Redeploy {app_name} to record them."
        )
    values = json.loads(config_map_data[PARAMETERS_KEY])
    return {name: values[name] for name in names if name in values}
