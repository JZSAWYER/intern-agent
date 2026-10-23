import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

from .core import Invalid, Store
from .planner import migration_plan


def main(argv=None):
    parser = argparse.ArgumentParser(description="Intern Agent personal preparation (offline only)")
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create isolated, secret-free personal configuration")
    init.add_argument("--github-owner")
    owner = sub.add_parser("set-owner", help="Set personal GitHub username without changing repository destinations")
    owner.add_argument("github_owner")
    sub.add_parser("doctor", help="Inspect personal configuration and local tools without network calls")
    sub.add_parser("simulate", help="Run a synthetic end-to-end pilot; no network, tmux or agent processes")
    add = sub.add_parser("workspace-add", help="Register a GitHub workspace locally; does not clone")
    add.add_argument("workspace_id")
    add.add_argument("repo_url")
    add.add_argument("--metadata-mode", choices=["repo_dotdir", "metadata_branch"], default="repo_dotdir")
    add.add_argument("--metadata-branch")
    plan = sub.add_parser("migration-plan", help="Read legacy records and local Git state; no remote access")
    plan.add_argument("--legacy-root", required=True, type=Path)
    plan.add_argument("--destinations", type=Path, help="Optional JSON mapping workspace IDs to GitHub URLs")
    args = parser.parse_args(argv)
    store = Store(args.project)
    try:
        if args.command == "init":
            store.initialize(args.github_owner)
            result = {"ok": True, "configuration": str(store.config)}
        elif args.command == "set-owner":
            store.set_owner(args.github_owner)
            result = {"ok": True, "github_owner_configured": True, "remotes_changed": False}
        elif args.command == "doctor":
            config = store.load()
            result = {"ok": True, "mode": "offline_preparation", "provider": "github",
                      "github_owner_configured": bool(config["github_owner"]),
                      "state_root": str(store.state),
                      "tools": {tool: bool(shutil.which(tool)) for tool in ["git", "gh", "tmux"]},
                      "deployment_ready": False, "offline_runtime_available": True,
                      "legacy_environment_loaded": False,
                      "pending": ([] if config["github_owner"] else ["github_owner"]) +
                                 ["github_access_and_destinations", "live_transport_integration",
                                  "uncertain_task_reconciliation", "credential_handoff",
                                  "deployment_authorization"]}
        elif args.command == "simulate":
            from .runtime import simulate
            with tempfile.TemporaryDirectory(prefix="ia-sim-") as directory:
                result = simulate(directory)
        elif args.command == "workspace-add":
            result = store.add_workspace(args.workspace_id, args.repo_url, args.metadata_mode, args.metadata_branch)
        else:
            store.load()
            targets = json.loads(args.destinations.read_text()) if args.destinations else None
            result = migration_plan(args.legacy_root, targets)
        print(json.dumps(result, indent=2))
        return 0
    except (Invalid, OSError, ValueError):
        # Do not echo malformed config/URL strings, which could include secrets.
        print(json.dumps({"ok": False, "error": "invalid_or_inaccessible_input",
                          "hint": "Use the documented schema, isolated paths and credential-free GitHub URLs."}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
