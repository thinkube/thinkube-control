# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A load is refused on a node the backend's base Deployment excludes by required node affinity."""

from app.services.llm_pod_manager import unmet_required_affinity

ARM = {"kubernetes.io/arch": "arm64", "kubernetes.io/hostname": "tkspark", "nvidia.com/gpu.memory": "122000"}
AMD = {"kubernetes.io/arch": "amd64", "kubernetes.io/hostname": "tkamd2", "nvidia.com/gpu.memory": "24576"}


def required(*terms):
    return {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": list(terms)}}}


def term(*exprs):
    return {"matchExpressions": list(exprs)}


AMD64_ONLY = required(term({"key": "kubernetes.io/arch", "operator": "In", "values": ["amd64"]}))


def test_an_architecture_rule_excludes_the_other_architecture():
    reason = unmet_required_affinity(AMD64_ONLY, ARM)
    assert reason == "kubernetes.io/arch In ['amd64'], and the node's kubernetes.io/arch is 'arm64'"
    assert unmet_required_affinity(AMD64_ONLY, AMD) is None


def test_no_required_affinity_admits_every_node():
    assert unmet_required_affinity(None, ARM) is None
    preferred_only = {"nodeAffinity": {"preferredDuringSchedulingIgnoredDuringExecution": [
        {"weight": 100, "preference": term({"key": "nvidia.com/gpu.memory", "operator": "Gt", "values": ["20480"]})}]}}
    assert unmet_required_affinity(preferred_only, AMD) is None


def test_one_matching_term_is_enough():
    either = required(term({"key": "kubernetes.io/arch", "operator": "In", "values": ["amd64"]}),
                      term({"key": "kubernetes.io/hostname", "operator": "In", "values": ["tkspark"]}))
    assert unmet_required_affinity(either, ARM) is None


def test_every_expression_of_a_term_must_match():
    both = required(term({"key": "kubernetes.io/arch", "operator": "In", "values": ["arm64"]},
                         {"key": "nvidia.com/gpu.memory", "operator": "Gt", "values": ["200000"]}))
    assert "nvidia.com/gpu.memory Gt ['200000']" in unmet_required_affinity(both, ARM)


def test_exists_and_not_in():
    assert unmet_required_affinity(required(term({"key": "gpu", "operator": "Exists"})), ARM) == \
        "gpu Exists, and the node's gpu is not set"
    not_amd = required(term({"key": "kubernetes.io/arch", "operator": "NotIn", "values": ["amd64"]}))
    assert unmet_required_affinity(not_amd, ARM) is None
    assert unmet_required_affinity(not_amd, AMD) is not None
