from datetime import date
from pathlib import Path


def get_project_root():
    return Path(__file__).resolve().parents[1]


def get_output_dir():
    output_dir = get_project_root() / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def get_dated_output_file(prefix):
    today = date.today().isoformat()
    return get_output_dir() / f"{prefix}_{today}.csv"