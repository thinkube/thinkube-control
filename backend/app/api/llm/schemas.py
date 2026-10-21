# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator


class ModelState(str, Enum):
    registered = "registered"
    deployable = "deployable"
    loading = "loading"
    available = "available"
    unloading = "unloading"


class ModelTier(str, Enum):
    flexible = "flexible"
    performance = "performance"


class ModelEntry(BaseModel):
    id: str
    name: str
    server_type: List[str] = Field(default_factory=list)
    serving_name: Optional[str] = None
    task: str = "text-generation"
    quantization: Optional[str] = None
    size: Optional[str] = None
    description: Optional[str] = None
    context_window: Optional[int] = None
    context_length: Optional[int] = None
    params_b: Optional[float] = None
    active_params_b: Optional[float] = None
    reasoning_format: Optional[str] = None
    # vLLM --tool-call-parser for this model, when the family's default does not
    # match the format the model emits (e.g. hermes for a Qwen3 that emits JSON).
    tool_call_parser: Optional[str] = None
    # vLLM --speculative-config JSON (e.g. '{"method": "mtp", "num_speculative_tokens": 1}')
    # for models that ship a draft / MTP head. None = no speculative decoding.
    speculative_config: Optional[str] = None
    # Real on-disk checkpoint size in bytes (summed safetensors/weight files),
    # measured from MLflow rather than estimated from params×dtype. Used by the
    # sizing logic so multimodal/mixed-precision weights aren't under-counted.
    weight_bytes: Optional[int] = None
    # Memory measured by thinkube-metadata's scripts/calibrate_models.py, keyed
    # by memory type ("uma", "discrete"); the load is sized from it.
    calibration: Optional[Dict[str, Any]] = None
    # Run vLLM in eager mode (--enforce-eager): skip torch.compile + CUDA-graph
    # capture. Keeps the init memory peak ≈ weights (small, predictable) at a
    # small decode cost on bandwidth-bound models.
    enforce_eager: bool = False
    # A signed runtime package (a Flare package): the publisher namespace and
    # the SHA-256 of its key, which the mirror checks and the pod approves.
    publisher: Optional[Dict[str, str]] = None
    # Command-line arguments the backend's server adds for this model, such as
    # what its publisher recommends for a GPU; passed to the pod as SERVER_ARGS.
    server_args: List[str] = Field(default_factory=list)
    tool_use: bool = False
    stop_tokens: List[str] = Field(default_factory=list)
    license: Optional[str] = None
    gated: bool = False
    # What the model is fit for: "agent" (Tandem offers it), "vision" (images).
    capabilities: List[str] = Field(default_factory=list)
    # Gateway names that answer as this model, such as thinkube-fast.
    aliases: List[str] = Field(default_factory=list)
    # The model a new Tandem chat starts with.
    default_agent: bool = False
    # The answer budget agents reserve inside the context.
    max_output: Optional[int] = None
    # The context the serving backend reports for the model (max_model_len).
    served_context_length: Optional[int] = None
    # Gateway role. "primary" = a normally loadable/servable model; "drafter" =
    # an auxiliary speculative-decoding drafter (e.g. DFlash) that only runs
    # inside a target model's vLLM via --speculative-config, never served or
    # loaded on its own. Non-"primary" roles are filtered out of the loadable list.
    role: str = "primary"
    artifact_path: Optional[str] = None
    state: ModelState = ModelState.registered
    backend_id: Optional[str] = None
    tier: Optional[ModelTier] = None
    is_finetuned: bool = False
    last_error: Optional[str] = None

    @model_validator(mode='after')
    def sync_context_fields(self):
        if self.context_length is not None and self.context_window is None:
            self.context_window = self.context_length
        elif self.context_window is not None and self.context_length is None:
            self.context_length = self.context_window
        return self


class ModelsListResponse(BaseModel):
    models: List[ModelEntry]
    total: int
    available: int
    deployable: int
    registered: int
    installed_backend_types: List[str] = Field(default_factory=list)


class ModelStatusResponse(BaseModel):
    model: ModelEntry
    backends: List[str] = Field(default_factory=list)


class BackendEntry(BaseModel):
    id: str
    name: str
    url: str
    type: str
    api_path: str = "/v1"
    status: str = "unknown"
    models: List[str] = Field(default_factory=list)
    # Served model -> the context it is served with (max_model_len), when reported.
    model_context: Dict[str, int] = Field(default_factory=dict)
    last_probe: Optional[str] = None
    node: Optional[str] = None


class BackendTypeEntry(BaseModel):
    """An installed backend type, as its component declares it."""

    type: str
    display_name: str
    namespace: str
    driver: str
    tier: str


class BackendTypesListResponse(BaseModel):
    backend_types: List[BackendTypeEntry]


class BackendsListResponse(BaseModel):
    backends: List[BackendEntry]
    total: int
    healthy: int


class ModelResolveResponse(BaseModel):
    backend_url: str
    api_path: str = "/v1"
    model_id: str
    serving_name: str = ""
    model_state: ModelState
    tier: ModelTier
    # The backend answers the Anthropic Messages API at <api_path>/messages.
    anthropic_messages: bool = False
    # The catalog aliases of the model, which the gateway also answers as it.
    aliases: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class GPUAllocation(BaseModel):
    model_id: str
    backend_id: str
    node_name: str = ""
    estimated_memory_gb: float
    slots: int = 1


class GPUMetricEntry(BaseModel):
    index: int = 0
    utilization: float = 0
    memory_used_mb: float = 0
    memory_total_mb: float = 0
    memory_free_mb: float = 0
    temp: float = 0
    power: float = 0


class GPUNode(BaseModel):
    name: str
    gpu_product: Optional[str] = None
    gpu_family: Optional[str] = None
    gpu_count: int = 1
    gpu_replicas: int = 1
    total_slots: int
    available_slots: int
    total_memory_gb: float
    per_gpu_memory_gb: float = 0.0
    used_memory_gb: float
    shared_memory: bool = False
    is_uma: bool = False
    arch: str = "uma"  # "uma" (GPU mem = host RAM, quota-charged) | "discrete"
    role: str = "platform-shared"  # "platform-shared" | "ai-dedicated"
    # CUDA compute capability of the node's GPUs, "major.minor" (e.g. "12.1").
    compute_capability: Optional[str] = None
    per_gpu_vram_gb: float = 0.0  # discrete: VRAM per physical GPU
    platform_reserved_gb: float = 0.0  # UMA shared: host RAM held by non-AI pods
    ai_budget_gb: float = 0.0  # memory the gateway may plan against on this node
    ai_remaining_gb: float = 0.0  # ai_budget minus memory already reserved here
    real_available_gb: Optional[float] = None
    metrics_available: bool = False
    per_gpu_metrics: List[GPUMetricEntry] = Field(default_factory=list)
    allocations: List[GPUAllocation] = Field(default_factory=list)


class GPUStatusResponse(BaseModel):
    nodes: List[GPUNode]
    total_memory_gb: float
    used_memory_gb: float
    memory_threshold: float
    can_accept_new_model: bool


class LoadOptionBackend(BaseModel):
    id: str
    name: str
    type: str
    status: str
    node: Optional[str] = None


class LoadOptionsResponse(BaseModel):
    model_id: str
    compatible_backends: List[LoadOptionBackend] = Field(default_factory=list)
    gpu_nodes: List[GPUNode] = Field(default_factory=list)
    estimated_memory_gb: float = 0.0
    context_length: Optional[int] = None


class ModelLoadRequest(BaseModel):
    tier: Optional[ModelTier] = None
    keep_alive: Optional[str] = None
    backend: Optional[str] = None
    node: Optional[str] = None
    max_context_length: Optional[int] = None
    # Per-load override of the speculative draft depth (k). Applies to a model
    # whose catalog speculative_config sets a method (e.g. DFlash); lets `k` be
    # tuned at load time without editing the catalog. None = use catalog default.
    num_speculative_tokens: Optional[int] = None


class ModelLoadResponse(BaseModel):
    model_id: str
    state: ModelState
    message: str
    backend_id: Optional[str] = None


class ModelUnloadRequest(BaseModel):
    force: bool = False


class RefreshResponse(BaseModel):
    models_refreshed: int
    backends_refreshed: int
    message: str = "ok"


def model_id_to_ollama_name(model_id: str) -> str:
    """Derive an Ollama serving name from a model ID.

    Only used as a fallback for dynamically registered models that lack an
    explicit serving_name.  Catalog models should always declare serving_name.
    """
    name = model_id.split("/")[-1].lower()
    for suffix in ["-gguf", "-ggml"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name
