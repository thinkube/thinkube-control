#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The values of an application's spec.env variables.

thinkube.yaml declares each variable with an optional default; a variable
without one is required. The value a deploy gives a variable wins over its
default; a required variable that is given none stops the deploy, naming
it. The values given at deploy are recorded in the app's metadata
ConfigMap, under ENV_KEY, so regenerating the manifests and redeploying
keep them.
"""

import json
from typing import Dict, List, Mapping

ENV_KEY = 'env'


def declared(thinkube_spec: Mapping) -> List[Mapping]:
    """The spec.env entries, less those a dependency wires: the dependency's URL wins."""
    spec = thinkube_spec.get('spec') or {}
    wired = {d['env'] for d in spec.get('dependencies') or [] if 'env' in d}
    return [e for e in spec.get('env') or [] if e['name'] not in wired]


def given_values(thinkube_spec: Mapping, params: Mapping) -> Dict[str, str]:
    """The spec.env variables the deploy gave a value, as the deploy gave them: what is recorded."""
    return {e['name']: str(params[e['name']]) for e in declared(thinkube_spec) if params.get(e['name']) not in (None, '')}


def kept(thinkube_spec: Mapping, recorded_values: Mapping[str, str], given: Mapping[str, str]) -> Dict[str, str]:
    """The values a redeploy records: those recorded at the last deploy, with the ones given now winning.

    A recorded variable that thinkube.yaml no longer declares is dropped.
    """
    names = {e['name'] for e in declared(thinkube_spec)}
    return {**{k: v for k, v in recorded_values.items() if k in names}, **given}


def resolve(thinkube_spec: Mapping, given: Mapping[str, str], app_name: str) -> Dict[str, str]:
    """Each spec.env variable's value: the one given at deploy, else its default; a required one given none is an error."""
    values: Dict[str, str] = {}
    missing: List[str] = []
    for entry in declared(thinkube_spec):
        name = entry['name']
        if name in given:
            values[name] = given[name]
        elif 'default' in entry:
            values[name] = str(entry['default'])
        else:
            missing.append(name)
    if missing:
        raise ValueError(
            f"{app_name}: thinkube.yaml declares {', '.join(missing)} in spec.env without a default, "
            f"and the deploy gave no value. Give {'it' if len(missing) == 1 else 'them'} when deploying."
        )
    return values


def encode(given: Mapping[str, str]) -> str:
    return json.dumps(dict(given), sort_keys=True)


def recorded(config_map_data: Mapping[str, str]) -> Dict[str, str]:
    """The values given at deploy, as the metadata ConfigMap records them; an app deployed before they were recorded has none."""
    return json.loads(config_map_data[ENV_KEY]) if ENV_KEY in config_map_data else {}
