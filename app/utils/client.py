import os
from pathlib import Path
from google import genai

def load_env():
    """Find and load .env file from project root or parent directories."""
    current = Path(__file__).resolve().parent
    for parent in [current, *current.parents]:
        env_file = parent / ".env"
        if env_file.exists():
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
            break

def get_genai_client(api_version: str = "v1beta") -> genai.Client:
    """Return an authenticated Gemini Client."""
    load_env()
    return genai.Client(http_options={"api_version": api_version})
