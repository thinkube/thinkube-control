# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Names an NVIDIA GPU from an `lspci -nn` line.

lspci takes names from the node's pci.ids file. A file older than the GPU
has no name for it, and lspci prints "Device xxxx". The playbooks recognise
a GB10 by its name, so these GPUs are named here by their PCI ID.
"""

import re

NVIDIA_NAMES_BY_PCI_ID = {
    "10de:2e12": "GB10 (DGX Spark / Blackwell)",
    "10de:2330": "H100 PCIe",
    "10de:2331": "H100 SXM5",
    "10de:2339": "H100 NVL",
    "10de:20b0": "A100 PCIe 40GB",
    "10de:20b2": "A100 SXM4 40GB",
    "10de:20f1": "A100 SXM4 80GB",
}

# A display or 3D controller; the other NVIDIA functions (HDMI audio, PCI
# bridges) are not GPUs.
GPU_CLASS = re.compile(r"\[030[0-2]\]")
PCI_ID = re.compile(r"\[(10de:[0-9a-f]{4})\]")


def is_gpu(lspci_nn_line):
    return bool(GPU_CLASS.search(lspci_nn_line)) and "NVIDIA" in lspci_nn_line


def gpu_name(lspci_nn_line):
    """The GPU's name, e.g. "NVIDIA GeForce RTX 3090" or "NVIDIA GB10 (DGX Spark / Blackwell)".

    Format: "01:00.0 VGA compatible controller [0300]: NVIDIA Corporation GA102 [GeForce RTX 3090] [10de:2204] (rev a1)"
    """
    pci_id = PCI_ID.search(lspci_nn_line)
    if pci_id is None:
        raise ValueError(f"No NVIDIA PCI ID in lspci line: {lspci_nn_line}")
    if pci_id.group(1) in NVIDIA_NAMES_BY_PCI_ID:
        return f"NVIDIA {NVIDIA_NAMES_BY_PCI_ID[pci_id.group(1)]}"
    # After the vendor: the chip, the marketing name in brackets when
    # pci.ids has one, then the PCI ID. Without a marketing name the PCI ID
    # stays in the name, so an unknown GPU can still be looked up.
    vendor_end = lspci_nn_line.index("NVIDIA Corporation") + len("NVIDIA Corporation")
    model = lspci_nn_line[vendor_end:pci_id.start()]
    marketing = re.search(r"\[([^\]]+)\]", model)
    if marketing:
        return f"NVIDIA {marketing.group(1).strip()}"
    return f"NVIDIA {model.strip()} [{pci_id.group(1)}]"
