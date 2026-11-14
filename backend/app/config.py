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
    photon_key: str = os.getenv('PHOTON_API_KEY', '')
    photon_project: str = os.getenv('PHOTON_PROJECT_ID', '')
    photon_poll_enabled: bool = truthy('PHOTON_POLLING_ENABLED')
    photon_groups: bool = truthy('PHOTON_ALLOW_GROUPS')
    photon_poll_seconds: int = max(3, int(os.getenv('PHOTON_POLL_SECONDS', '8')))
    photon_allowed_senders: tuple[str, ...] = tuple(x.strip() for x in os.getenv('PHOTON_ALLOWED_SENDERS', '').split(',') if x.strip())
    photon_enabled: bool = truthy('PHOTON_ENABLED')
    openalex_email: str = os.getenv('OPENALEX_EMAIL', '')
    cors_origins: tuple[str, ...] = tuple(x.strip() for x in os.getenv('CORS_ORIGINS', 'http://localhost:5173').split(','))

settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
(settings.data_dir / 'jobs').mkdir(parents=True, exist_ok=True)
