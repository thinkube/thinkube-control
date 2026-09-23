# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A GPU is named by its PCI ID, so a node with an old pci.ids still names a GB10."""

from app.services.gpu_names import gpu_name, is_gpu
from app.services.node_manager import NodeManager

GB10 = "000f:01:00.0 VGA compatible controller [0300]: NVIDIA Corporation Device [10de:2e12] (rev a1)"
BRIDGE = "0000:00:00.0 PCI bridge [0604]: NVIDIA Corporation Device [10de:22ce] (rev 01)"
GTX_1080_TI = "01:00.0 VGA compatible controller [0300]: NVIDIA Corporation GP102 [GeForce GTX 1080 Ti] [10de:1b06] (rev a1)"
HDMI_AUDIO = "01:00.1 Audio device [0403]: NVIDIA Corporation GP102 HDMI Audio Controller [10de:10ef] (rev a1)"
UNKNOWN = "01:00.0 3D controller [0302]: NVIDIA Corporation Device [10de:2bff] (rev a1)"


def test_a_gb10_is_named_even_when_pci_ids_does_not_know_it():
    assert gpu_name(GB10) == "NVIDIA GB10 (DGX Spark / Blackwell)"


def test_a_known_gpu_takes_its_name_from_pci_ids():
    assert gpu_name(GTX_1080_TI) == "NVIDIA GeForce GTX 1080 Ti"


def test_an_unknown_gpu_keeps_its_pci_id():
    assert gpu_name(UNKNOWN) == "NVIDIA Device [10de:2bff]"


def test_bridges_and_audio_functions_are_not_gpus():
    assert [is_gpu(line) for line in (GB10, BRIDGE, GTX_1080_TI, HDMI_AUDIO)] == [True, False, True, False]


def test_the_support_check_reads_the_new_names():
    nm = NodeManager.__new__(NodeManager)
    assert nm._is_gpu_cuda_compatible(gpu_name(GB10))
    assert not nm._is_gpu_cuda_compatible(gpu_name(GTX_1080_TI))
    assert not nm._is_gpu_cuda_compatible("NVIDIA GP104 [10de:1b80]")
