import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / '.env')

ROOT = Path(__file__).resolve().parents[2]

def truthy(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ('true', '1', 'yes', 'on')

@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv('DATA_DIR') or str(ROOT / 'backend' / 'data')).resolve()
    research_mode: str = os.getenv('RESEARCH_MODE', 'demo')
    openrouter_api_key: str = os.getenv('OPENROUTER_API_KEY', '')
    openrouter_model: str = os.getenv('OPENROUTER_MODEL', 'openai/gpt-4o-mini')
    openai_api_key: str = os.getenv('OPENAI_API_KEY', '')
    openai_model: str = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
    tavily_api_key: str = os.getenv('TAVILY_API_KEY', '')
    nvidia_api_key: str = os.getenv('NVIDIA_API_KEY', '')
    nvidia_base_url: str = os.getenv('NVIDIA_BASE_URL', 'https://integrate.api.nvidia.com/v1')
    groq_api_key: str = os.getenv('GROQ_API_KEY', '')
    groq_base_url: str = os.getenv('GROQ_BASE_URL', 'https://api.groq.com/openai/v1')
    ollama_base_url: str = os.getenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434/v1')
    research_model_provider: str = os.getenv('RESEARCH_MODEL_PROVIDER', 'auto')
    research_model: str = os.getenv('RESEARCH_MODEL', '')
    planning_model_provider: str = os.getenv('PLANNING_MODEL_PROVIDER', 'auto')
    planning_model: str = os.getenv('PLANNING_MODEL', '')
    coding_model_provider: str = os.getenv('CODING_MODEL_PROVIDER', 'auto')
    coding_model: str = os.getenv('CODING_MODEL', '')
    debug_model_provider: str = os.getenv('DEBUG_MODEL_PROVIDER', 'auto')
    debug_model: str = os.getenv('DEBUG_MODEL', '')
    report_model_provider: str = os.getenv('REPORT_MODEL_PROVIDER', 'auto')
    report_model: str = os.getenv('REPORT_MODEL', '')
    experiment_executor: str = os.getenv('EXPERIMENT_EXECUTOR', 'docker')
    experiment_image: str = os.getenv('EXPERIMENT_IMAGE', 'labthread-executor:py312')
    experiment_max_runtime: int = min(300,max(30,int(os.getenv('EXPERIMENT_MAX_RUNTIME','300'))))
    experiment_memory_mb: int = min(2048,max(256,int(os.getenv('EXPERIMENT_MEMORY_MB','2048'))))
    experiment_cpu_cores: float = min(2.0,max(0.25,float(os.getenv('EXPERIMENT_CPU_CORES','2'))))
    experiment_pids_limit: int = min(256,max(32,int(os.getenv('EXPERIMENT_PIDS_LIMIT','128'))))
    experiment_max_retries: int = min(2,max(0,int(os.getenv('EXPERIMENT_MAX_RETRIES','2'))))
    experiment_disk_mb: int = min(1024,max(32,int(os.getenv('EXPERIMENT_DISK_MB','256'))))
    experiment_poll_seconds: float = max(.2,float(os.getenv('EXPERIMENT_POLL_SECONDS','.75')))
    photon_key: str = os.getenv('PHOTON_API_KEY', '')
    photon_project: str = os.getenv('PHOTON_PROJECT_ID', '')
    photon_poll_enabled: bool = truthy('PHOTON_POLLING_ENABLED')
    photon_platform: str = os.getenv('PHOTON_PLATFORM', '').strip().lower().replace('-', '_')
    photon_groups: bool = truthy('PHOTON_ALLOW_GROUPS')
    photon_poll_seconds: int = max(3, int(os.getenv('PHOTON_POLL_SECONDS', '8')))
    photon_allowed_senders: tuple[str, ...] = tuple(x.strip() for x in os.getenv('PHOTON_ALLOWED_SENDERS', '').split(',') if x.strip())
    photon_enabled: bool = truthy('PHOTON_ENABLED')
    openalex_email: str = os.getenv('OPENALEX_EMAIL', '')
    cors_origins: tuple[str, ...] = tuple(x.strip() for x in os.getenv('CORS_ORIGINS', 'http://localhost:5173').split(','))

settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
(settings.data_dir / 'jobs').mkdir(parents=True, exist_ok=True)
(settings.data_dir / 'experiments').mkdir(parents=True, exist_ok=True)
