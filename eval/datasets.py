"""Which folders are evaluated. One place to change: .env.

  ALTITUDE_GOLDEN_DATASET   the scored golden set (default data/golden/quotes-v1)
  ALTITUDE_EXTRA_DATASETS   comma list run alongside it (default: the synthetic edge cases and demo
                            quotes); set it empty to run the golden set alone

Used by the app's Run button, make demo-check, the parser metrics and `pytest -m eval`.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = 'data/golden/quotes-v1'
DEFAULT_EXTRA = 'data/synthetic/edge-cases,data/synthetic'

def golden_dataset() -> str:
    return os.getenv('ALTITUDE_GOLDEN_DATASET', '').strip() or DEFAULT_GOLDEN

def extra_datasets() -> list[str]:
    raw = os.getenv('ALTITUDE_EXTRA_DATASETS')
    raw = DEFAULT_EXTRA if raw is None else raw
    return [d.strip() for d in raw.split(',') if d.strip()]

def harness_datasets() -> list[str]:
    """Golden set first, then the extras (as relative paths, the way the harness command shows them)."""
    return [golden_dataset(), *[d for d in extra_datasets() if d != golden_dataset()]]

def path(dataset: str) -> Path:
    p = Path(dataset)
    return p if p.is_absolute() else ROOT / p
