"""Explicitly import selected provider settings without printing secrets."""

import argparse
import os
from pathlib import Path

from dotenv import dotenv_values


def configure(source: Path, destination: Path, model: str):
    values = dotenv_values(source)
    key = values.get("LLM_API_KEY") or values.get("ANTHROPIC_AUTH_TOKEN")
    base = values.get("LLM_BASE_URL")
    if not base and values.get("ANTHROPIC_BASE_URL"):
        base = values["ANTHROPIC_BASE_URL"].rstrip("/") + "/v1"
    if not key or not base:
        raise ValueError("Source must contain a model key and gateway URL.")
    selected = {
        "LLM_API_KEY": key,
        "LLM_BASE_URL": base,
        "LLM_MODEL": values.get("LLM_MODEL") or model,
        "LLM_TIMEOUT": values.get("LLM_TIMEOUT") or "120",
    }
    for name in ("TAVILY_API_KEY", "GITHUB_TOKEN"):
        if values.get(name):
            selected[name] = values[name]
    # Exclusive creation prevents accidentally overwriting existing local configuration.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        for name, value in selected.items():
            escaped = value.replace("\\", "\\\\").replace("'", "\\'")
            stream.write(f"{name}='{escaped}'\n")
    print("Configured fields: " + ", ".join(selected))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, default=Path(".env"))
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    configure(args.source, args.destination, args.model)
