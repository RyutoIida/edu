from pathlib import Path
import os

BASE = Path(__file__).parent
DATA_DIR = BASE / "data"
OUT_DIR = BASE / "output"
TPL_DIR = BASE / "templates"

INPUT_FILE = str(DATA_DIR / "sample_input.json")
CLASSIFIED_FILE = str(DATA_DIR / "classified.json")
SPEC_MD = str(OUT_DIR / "spec.md")
SPEC_TPL = str(TPL_DIR / "spec.md.j2")

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))

INPUT = DATA_DIR / "classified.json"
NORMALIZE = DATA_DIR / "normalized.json"
NORMALIZE_OUTPUT = OUT_DIR / "requirements.md"