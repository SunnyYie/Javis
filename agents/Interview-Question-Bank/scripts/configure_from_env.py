"""Copy only authorized provider fields, never print their values."""

import argparse
import os
from pathlib import Path

from dotenv import dotenv_values


def configure(source: Path, destination: Path):
    values = dotenv_values(source)
    if not values.get("LLM_API_KEY"):
        raise ValueError("Source requires LLM_API_KEY.")
    names = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_TIMEOUT",
             "TAVILY_API_KEY", "GITHUB_TOKEN")
    selected = {name: values[name] for name in names if values.get(name)}
    selected["LLM_MODEL"] = "glm-5.3-flashx"
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        for name, value in selected.items():
            escaped = value.replace("\\", "\\\\").replace("'", "\\'")
            stream.write(f"{name}='{escaped}'\n")
    print("Configured fields: " + ", ".join(selected))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, default=Path(".env"))
    args = parser.parse_args()
    configure(args.source, args.destination)
