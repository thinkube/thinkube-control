# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""An update of a venv package replaces the requirement for the same platform only."""

from app.api.jupyter_venvs import requirement_key

ARM = 'vllm @ https://example/vllm-linux_aarch64.whl ; platform_machine == "aarch64"'
X86 = 'vllm @ https://example/vllm-linux_x86_64.whl ; platform_machine == "x86_64"'


def test_a_requirement_for_another_platform_is_another_requirement():
    assert requirement_key(ARM) != requirement_key(X86)


def test_the_same_platform_with_other_spaces_is_the_same_requirement():
    assert requirement_key(ARM) == requirement_key('vllm @ https://example/other.whl;platform_machine=="aarch64"')


def test_without_a_marker_the_name_decides():
    assert requirement_key("tk-llebre @ git+https://x@a") == requirement_key("tk_llebre @ git+https://x@b")
    assert requirement_key("transformers==5.20.2") == ("transformers", "")
