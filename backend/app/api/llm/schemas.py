from enum import Enum
from typing import List, Optional
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
    tool_use: bool = False
    stop_tokens: List[str] = Field(default_factory=list)
    license: Optional[str] = None
    gated: bool = False
    capabilities: List[str] = Field(default_factory=list)
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
    last_probe: Optional[str] = None
    node: Optional[str] = None


class BackendsListResponse(BaseModel):
    backends: List[BackendEntry]
    total: int
    healthy: int


class ModelResolveResponse(BaseModel):
    backend_url: str
    api_path: str = "/v1"
    model_id: str
    model_state: ModelState
    tier: ModelTier
    error: Optional[str] = None


class GPUAllocation(BaseModel):
    model_id: str
    backend_id: str
    node_name: str = ""
    estimated_memory_gb: float
    slots: int = 1


class GPUNode(BaseModel):
    name: str
    gpu_product: Optional[str] = None
    gpu_family: Optional[str] = None
    gpu_count: int = 1
    gpu_replicas: int = 1
    total_slots: int
    available_slots: int
    total_memory_gb: float
    used_memory_gb: float
    shared_memory: bool = False
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


class ModelLoadRequest(BaseModel):
    tier: Optional[ModelTier] = None
    keep_alive: Optional[str] = None
    backend: Optional[str] = None
    node: Optional[str] = None


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
