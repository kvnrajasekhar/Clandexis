import os
from pathlib import Path

class Settings:
    PROJECT_NAME: str = "Clandexis AI Engine"
    VERSION: str = "1.1.0"
    API_V1_STR: str = "/api/v1"
    
    # Model preferences
    DEFAULT_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    FALLBACK_MODEL: str = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
    
    # Project paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent

settings = Settings()
