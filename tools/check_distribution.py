"""Check package allowlist without creating an artifact or printing private data."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from intern_personal.core import Invalid
from intern_personal.distribution import package_files


def main():
    try:
        local = ROOT / "personal.json"
        owner = json.loads(local.read_text()).get("github_owner") if local.exists() else None
        files = package_files(ROOT, owner)
        print(json.dumps({"ok": True, "files": files, "artifact_created": False,
                          "private_state_included": False}, indent=2))
        return 0
    except (Invalid, ValueError, OSError):
        print(json.dumps({"ok": False, "error": "distribution_inputs_rejected"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
