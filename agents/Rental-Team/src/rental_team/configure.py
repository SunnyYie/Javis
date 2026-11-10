import argparse
import getpass
import json
import os
import re
import tempfile

from filelock import FileLock

from .config import ROOT


def save_key(path, provider, key):
    valid = (
        bool(re.fullmatch(r"[0-9a-fA-F]{32}", key)) if provider == "amap"
        else provider == "vigo" and key.startswith("vgk_live_") and len(key) <= 200
    )
    if not valid:
        raise ValueError("Invalid key format")
    with FileLock(str(path) + ".lock"):
        if path.is_symlink():
            raise ValueError("Credential symlinks are not supported")
        data = {}
        if path.exists():
            if path.stat().st_mode & 0o077:
                raise ValueError("credentials permissions must be 0600")
            data = json.loads(path.read_text())
        data[provider + "_api_key"] = key
        descriptor, temporary = tempfile.mkstemp(
            prefix="credentials.tmp-", suffix=".json", dir=path.parent,
        )
        try:
            with os.fdopen(descriptor, "w") as file:
                json.dump(data, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("vigo", "amap"), default="vigo")
    args = parser.parse_args()
    key = getpass.getpass(f"{args.provider} API Key (hidden): ").strip()
    try:
        save_key(ROOT / "credentials.json", args.provider, key)
    except (ValueError, OSError):
        raise SystemExit("Could not save credentials; check key format and protected file") from None
    print(f"{args.provider} key saved locally with mode 0600; no network requests.")


if __name__ == "__main__":
    main()
