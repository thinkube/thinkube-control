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


def values_in_use(names: List[str], container_env: Mapping[str, str]) -> Dict[str, str]:
    """The declared parameters as the running containers carry them.

    An application deployed before the values were recorded has them nowhere
    in its ConfigMap, but its containers were given them as environment
    variables and still hold them. Reading them there is reading the same
    values, not guessing at them.
    """
    return {name: str(container_env[name]) for name in names if container_env.get(name)}


def recorded_values(names: List[str], config_map_data: Mapping[str, str], app_name: str,
                    container_env: Mapping[str, str] | None = None) -> Dict[str, str]:
    """The declared parameters' values: as the metadata ConfigMap records them, else as the containers carry them.

    Regenerating without the values would drop the variables from the
    manifests, so when neither the ConfigMap nor the running containers hold a
    declared parameter the regeneration stops and says which.
    """
    if not names:
        return {}
    if PARAMETERS_KEY in config_map_data:
        values = json.loads(config_map_data[PARAMETERS_KEY])
        return {name: values[name] for name in names if name in values}
    in_use = values_in_use(names, container_env or {})
    if in_use:
        return in_use
    raise RuntimeError(
        f"The metadata ConfigMap of {app_name} does not record the values of its parameters "
        f"({', '.join(names)}), and its containers do not carry them either. Deploy {app_name} "
        f"with those parameters to record them."
    )
