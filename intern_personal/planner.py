"""Offline legacy inventory: never clone, fetch, push, checkout or edit a record."""
from collections import Counter
import json
import os
from pathlib import Path
import subprocess

from .core import Invalid, github_url, git_environment, valid_branch


def git_read(repo, args):
    result = subprocess.run(
        ["/usr/bin/git", "--no-optional-locks", "-c", "core.fsmonitor=false",
         "-c", "core.untrackedCache=false", "-c", "core.hooksPath=/dev/null",
         "-c", "credential.helper=", "-C", str(repo), *args],
        env=git_environment(), capture_output=True, text=True, timeout=20,
    )
    if result.returncode:
        return None  # Never include raw Git errors, URLs or credentials in reports.
    return result.stdout.strip()


def repository_summary(path, metadata_branch):
    try:
        head = git_read(path, ["rev-parse", "--verify", "HEAD"])
        status = git_read(path, ["status", "--porcelain=v1", "--untracked-files=normal",
                                 "--ignore-submodules=all"])
        refs = git_read(path, ["for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags"])
        shallow = git_read(path, ["rev-parse", "--is-shallow-repository"])
        metadata_local = False
        if valid_branch(metadata_branch):
            metadata_local = any(git_read(path, ["show-ref", "--verify", "--hash", prefix + metadata_branch])
                                 for prefix in ["refs/heads/", "refs/remotes/origin/"])
        return {
            "path": str(path), "head": head, "inspection_ok": status is not None and refs is not None,
            "has_local_changes": bool(status) if status is not None else None,
            "local_branch_count": sum(r.startswith("refs/heads/") for r in (refs or "").splitlines()),
            "tag_count": sum(r.startswith("refs/tags/") for r in (refs or "").splitlines()),
            "shallow": shallow == "true" if shallow is not None else None,
            "metadata_branch_available_locally": metadata_local,
            "repo_metadata_present": (path / ".intern_workspace").is_dir(),
            "submodules_declared": (path / ".gitmodules").is_file(),
            "lfs_completeness": "not_verified",
        }
    except (OSError, subprocess.TimeoutExpired):
        return {"path": str(path), "inspection_ok": False, "error": "local_git_inspection_failed"}


def local_repositories(workspace, record):
    """Bounded discovery of source, metadata checkout and immediate intern clones."""
    workspace = workspace.resolve()
    candidates = {workspace / "source", workspace / "metadata" / "branch"}
    for field in ["local_path", "metadata_cache_path"]:
        value = record.get(field)
        if isinstance(value, str):
            candidate = Path(value).resolve()
            if candidate.is_relative_to(workspace):
                candidates.add(candidate)
    interns = workspace / "interns"
    if interns.is_dir() and not interns.is_symlink():
        for intern in interns.iterdir():
            if intern.is_dir() and not intern.is_symlink():
                candidates.add(intern)
                candidates.update(p for p in intern.iterdir() if p.is_dir() and not p.is_symlink())
    return sorted(p for p in candidates if p.resolve().is_relative_to(workspace)
                  and not p.is_symlink() and (p / ".git").exists())


def migration_plan(legacy_root, destinations=None):
    root = Path(legacy_root).resolve()
    state = root / "state" / "v1"
    if not state.is_dir():
        raise Invalid("Legacy root must contain state/v1")
    destinations = {} if destinations is None else destinations
    if not isinstance(destinations, dict):
        raise Invalid("Destination mapping must be an object of workspace IDs to GitHub URLs")
    targets = {key: github_url(value) for key, value in destinations.items()}
    target_counts = Counter(value.lower() for value in targets.values())
    entries = []
    for file in sorted(state.glob("*/workspace.json")):
        if file.is_symlink() or not file.resolve().is_relative_to(state.resolve()):
            entries.append({"workspace_id": file.parent.name, "blockers": ["symlink_outside_inventory"], "ready": False})
            continue
        wid = file.parent.name
        try:
            record = json.loads(file.read_text())
            if not isinstance(record, dict):
                raise ValueError()
        except (OSError, ValueError):
            entries.append({"workspace_id": wid, "blockers": ["invalid_workspace_record"], "ready": False})
            continue
        provider = record.get("provider")
        provider = provider if provider in {"codeup", "github", "gitlab", "local"} else "unspecified"
        mode = record.get("metadata_mode")
        mode = mode if mode in {"repo_dotdir", "metadata_branch", "local_only"} else "unspecified"
        branch = record.get("metadata_branch")
        branch = branch if valid_branch(branch) else None
        target = targets.get(wid)
        repos = [repository_summary(p, branch) for p in local_repositories(file.parent, record)]
        blockers = []
        if record.get("deleted"):
            blockers.append("record_marked_deleted")
        if not target:
            blockers.append("explicit_github_destination_required")
        elif target_counts[target.lower()] > 1:
            blockers.append("duplicate_github_destination")
        if not repos:
            blockers.append("no_local_repository_found")
        if any(not p["inspection_ok"] for p in repos):
            blockers.append("local_repository_inspection_incomplete")
        if any(p.get("has_local_changes") for p in repos):
            blockers.append("preserve_uncommitted_work_before_migration")
        if any(p.get("shallow") for p in repos):
            blockers.append("local_history_is_shallow")
        if mode == "metadata_branch" and not any(p.get("metadata_branch_available_locally") for p in repos):
            blockers.append("metadata_branch_not_found_locally")
        if mode not in {"repo_dotdir", "metadata_branch"}:
            blockers.append("metadata_layout_requires_review")
        entries.append({
            "workspace_id": wid, "source_provider": provider,
            "metadata_mode": mode, "metadata_branch": branch,
            "github_destination": target, "local_repositories": repos,
            "blockers": blockers, "ready": False,
            "remaining_checks": ["all_worktrees_and_branches", "lfs_and_submodules",
                                 "secret_scan_before_upload", "github_destination_access",
                                 "migration_authorization"],
        })
    unknown = sorted(set(targets) - {e["workspace_id"] for e in entries})
    if unknown:
        raise Invalid("Destination map contains unknown workspace IDs")
    return {
        "schema": "intern-agent.migration-preview.v1", "dry_run": True,
        "network_used": False, "existing_records_changed": False,
        "discovery": "source, metadata checkout, and immediate intern clone/worktree directories; not exhaustive",
        "summary": {"workspace_records": len(entries),
                    "providers": dict(Counter(e.get("source_provider", "invalid") for e in entries)),
                    "local_repositories": sum(len(e.get("local_repositories", [])) for e in entries)},
        "workspaces": entries,
    }
