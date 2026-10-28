# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A venv package update keeps the venv's version pins."""

from app.api.jupyter_venvs import pinned_constraints

PACKAGES = [
    "ipykernel",
    "trl>=1.16",
    "transformers==5.20.2",
    "kubernetes==36.0.3",
    'vllm @ https://example/vllm-linux_aarch64.whl ; platform_machine == "aarch64"',
    "tk-llebre @ git+https://github.com/thinkube/llebre@abc",
    "unsloth==2026.10.7 --no-deps",
]


def test_the_version_pins_are_the_constraints():
    assert pinned_constraints(PACKAGES, "vllm @ https://example/other.whl") == ["transformers==5.20.2", "kubernetes==36.0.3"]


def test_the_updated_package_is_not_held_at_its_old_pin():
    assert pinned_constraints(PACKAGES, "transformers==5.21.0") == ["kubernetes==36.0.3"]


def test_ranges_urls_and_options_are_not_pins():
    pins = pinned_constraints(PACKAGES, "ipykernel")
    assert "trl>=1.16" not in pins
    assert not any("@" in p or "--" in p for p in pins)


def test_a_pin_with_extras_is_a_constraint_without_them():
    pins = pinned_constraints(["nvidia-modelopt[hf]==0.47.0", 'torch[cuda]==2.11.0 ; platform_machine == "x86_64"'],
                              "boto3")
    assert pins == ["nvidia-modelopt==0.47.0", 'torch==2.11.0 ; platform_machine == "x86_64"']
