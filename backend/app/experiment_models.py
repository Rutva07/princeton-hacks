"""Typed contracts shared by the autonomous experiment graph and executor."""
from __future__ import annotations

from typing import Any, Literal, TypedDict
from pydantic import BaseModel, Field, field_validator


ExperimentStatus = Literal[
    'queued','researching','planning','generating','validating','running',
    'debugging','evaluating','reporting','completed','failed','cancelled'
]


class DatasetSpec(BaseModel):
    name: str = Field(min_length=2,max_length=120)
    source: str = Field(min_length=2,max_length=500)
    version: str = Field(default='built-in',max_length=100)
    description: str = Field(default='',max_length=1200)


class ResourceBudget(BaseModel):
    runtime_seconds: int = Field(default=180,ge=30,le=300)
    memory_mb: int = Field(default=1024,ge=256,le=2048)
    cpu_cores: float = Field(default=1.0,ge=.25,le=2.0)
    network_access: bool = False


class ExperimentPlan(BaseModel):
    title: str = Field(min_length=4,max_length=180)
    objective: str = Field(min_length=8,max_length=2000)
    experiment_type: Literal[
        'classification_comparison','traffic_simulation','flow_matching',
        'paper_reproduction','custom_python'
    ]
    dataset: DatasetSpec
    methods: list[str] = Field(min_length=1,max_length=8)
    metrics: list[str] = Field(min_length=1,max_length=12)
    random_seed: int = Field(default=42,ge=0,le=2_147_483_647)
    random_seeds: list[int] = Field(default_factory=list,max_length=100)
    expected_training_runs: int | None = Field(default=None,ge=0,le=100_000)
    expected_simulations: int | None = Field(default=None,ge=0,le=10_000_000)
    parameters: dict[str,Any] = Field(default_factory=dict)
    preprocessing: list[str] = Field(default_factory=list,max_length=12)
    split_strategy: str = Field(default='',max_length=600)
    evaluation_procedure: str = Field(default='',max_length=1200)
    required_libraries: list[str] = Field(default_factory=list,max_length=20)
    expected_artifacts: list[str] = Field(default_factory=list,max_length=20)
    resource_budget: ResourceBudget = Field(default_factory=ResourceBudget)
    execution_mode: Literal['local_cpu'] = 'local_cpu'
    reproduction_level: Literal['original','adapted','reduced_scale','not_applicable']='not_applicable'
    limitations: list[str] = Field(default_factory=list,max_length=20)
    source_urls: list[str] = Field(default_factory=list,max_length=20)

    @field_validator('source_urls')
    @classmethod
    def only_http_sources(cls,values):
        return [value for value in values if value.startswith(('https://','http://'))]


class CodeBundle(BaseModel):
    experiment_py: str = Field(min_length=80,max_length=80_000)
    config: dict[str,Any]
    requirements: list[str] = Field(default_factory=list,max_length=30)
    readme: str = Field(min_length=20,max_length=20_000)
    template: str = Field(default='generated',max_length=80)


class ValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    code_hash: str = ''


class ExecutionResult(BaseModel):
    status: Literal['completed','failed','cancelled','timed_out','unavailable']
    exit_code: int | None = None
    runtime_seconds: float = 0
    logs: list[str] = Field(default_factory=list)
    error: str | None = None
    outputs: list[dict[str,Any]] = Field(default_factory=list)
    container_name: str | None = None
    executor_image: str | None = None
    command: list[str] = Field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None


class ResearchState(TypedDict,total=False):
    room_id: str
    user_id: str
    experiment_id: str
    user_request: str
    intent: str
    parent_experiment_id: str | None
    research_sources: list[dict[str,Any]]
    experiment_plan: dict[str,Any]
    code_bundle: dict[str,Any]
    validation_result: dict[str,Any]
    execution_result: dict[str,Any]
    metrics: dict[str,Any]
    artifacts: list[dict[str,Any]]
    retry_count: int
    errors: list[str]
    report: dict[str,Any]
    next_step: str

