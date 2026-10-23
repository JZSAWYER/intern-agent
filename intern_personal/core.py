from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path
import re
import tempfile
from contextlib import contextmanager
from urllib.parse import urlsplit


class Invalid(ValueError):
    pass


SCOPES = [
    "im:message:send_as_bot", "im:message.group_msg", "im:chat:create",
    "im:chat:read", "im:chat:update", "im:chat.members:read",
    "im:chat.members:write_only",
]
OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}")
WORKSPACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}")


def valid_owner(value):
    return (isinstance(value, str) and bool(OWNER.fullmatch(value)) and "--" not in value
            and value.casefold() not in {"your-login", "your-github-username", "change-me"})


def github_url(value: str) -> str:
    """Accept only credential-free github.com repository URLs; never access one."""
    if not isinstance(value, str) or any(c.isspace() for c in value):
        raise Invalid("Expected a credential-free GitHub repository URL")
    if value.startswith("git@github.com:"):
        path = value[len("git@github.com:"):]
    else:
        try:
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or parsed.netloc != "github.com"
                    or parsed.query or parsed.fragment):
                raise Invalid("Only github.com HTTPS or git@github.com SSH URLs are allowed")
            path = parsed.path.removeprefix("/")
        except ValueError:
            raise Invalid("Invalid GitHub URL") from None
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if (len(parts) != 2 or not valid_owner(parts[0])
            or not REPO.fullmatch(parts[1]) or parts[1] in {".", ".."}):
        raise Invalid("Expected GitHub owner/repository; credentials and URL options are forbidden")
    return "https://github.com/" + "/".join(parts) + ".git"


def default_config(owner=None):
    if owner is not None and not valid_owner(owner):
        raise Invalid("Invalid GitHub owner")
    return {
        "schema": "intern-agent.personal.v1",
        "github_owner": owner,
        "provider": "github",
        "feishu": {"domain": "https://open.feishu.cn", "transport": "websocket",
                   "events": ["im.message.receive_v1"], "tenant_scopes": SCOPES.copy()},
        "execution": "offline_preparation",
    }


def validate_config(value):
    if not isinstance(value, dict):
        raise Invalid("Configuration must be an object")
    expected = default_config(value.get("github_owner"))
    if value != expected:
        raise Invalid("Configuration must match the personal schema; legacy settings and secrets are forbidden")
    return value


class Store:
    def __init__(self, project: Path):
        self.project = project.resolve()
        self.config = self.project / "personal.json"
        self.state = self.project / "runtime"

    def contained(self, path: Path):
        if not path.resolve().is_relative_to(self.project):
            raise Invalid("Personal files must remain inside the isolated project")
        return path

    def write(self, path: Path, value):
        self.contained(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.contained(path)
        fd, name = tempfile.mkstemp(prefix=".personal-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream, indent=2)
                stream.write("\n")
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    def initialize(self, owner=None):
        if self.config.exists() or self.config.is_symlink():
            raise Invalid("Personal configuration already exists; refusing to overwrite")
        self.write(self.config, default_config(owner))

    def load(self):
        self.contained(self.config)
        return validate_config(json.loads(self.config.read_text()))

    def set_owner(self, owner):
        with self.registry_lock():
            return self._set_owner_locked(owner)

    def _set_owner_locked(self, owner):
        if not valid_owner(owner):
            raise Invalid("A real GitHub username is required; placeholders are not accepted")
        config = self.load()
        registry_path = self.contained(self.state / "workspaces.json")
        registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
        if not isinstance(registry, dict):
            raise Invalid("Invalid personal registry")
        for item in registry.values():
            if not isinstance(item, dict) or item.get("provider") != "github":
                raise Invalid("Invalid personal registry")
            if github_url(item.get("repo_url")).split("/")[3].casefold() != owner.casefold():
                raise Invalid("Existing workspaces require an explicit destination migration before changing owner")
        config["github_owner"] = owner
        self.write(self.config, validate_config(config))

    @contextmanager
    def registry_lock(self):
        self.contained(self.state)
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self.contained(self.state / "registry.lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def add_workspace(self, workspace_id, repo_url, metadata_mode="repo_dotdir", metadata_branch=None):
        with self.registry_lock():
            return self._add_workspace_locked(workspace_id, repo_url, metadata_mode, metadata_branch)

    def _add_workspace_locked(self, workspace_id, repo_url, metadata_mode, metadata_branch):
        config = self.load()
        if not WORKSPACE.fullmatch(workspace_id):
            raise Invalid("Invalid workspace identifier")
        url = github_url(repo_url)
        owner = config["github_owner"]
        if owner and url.split("/")[3].lower() != owner.lower():
            raise Invalid("Repository owner differs from configured personal owner")
        if metadata_mode not in {"repo_dotdir", "metadata_branch"}:
            raise Invalid("Unsupported metadata mode")
        if metadata_mode == "metadata_branch" and not valid_branch(metadata_branch):
            raise Invalid("A valid metadata branch is required")
        registry_path = self.contained(self.state / "workspaces.json")
        registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
        if not isinstance(registry, dict) or any(
            not isinstance(item, dict) or item.get("provider") != "github"
            or not isinstance(item.get("repo_url"), str) for item in registry.values()
        ):
            raise Invalid("Invalid personal registry; refusing to overwrite")
        for item in registry.values():
            github_url(item["repo_url"])
        if workspace_id in registry:
            raise Invalid("Workspace already exists; refusing to replace it")
        if any(item["repo_url"].lower() == url.lower() for item in registry.values()):
            raise Invalid("Repository is already registered")
        registry[workspace_id] = {
            "provider": "github", "repo_url": url, "metadata_mode": metadata_mode,
            "metadata_branch": metadata_branch if metadata_mode == "metadata_branch" else None,
        }
        self.write(registry_path, registry)
        return registry[workspace_id]


def valid_branch(value):
    return (isinstance(value, str) and bool(value) and value != "@"
            and not value.startswith(("-", "/")) and not value.endswith(("/", "."))
            and not any(x in value for x in ["..", "@{", "//", "\\", "~", "^", ":", "?", "*", "["])
            and not any(ord(c) <= 32 or ord(c) == 127 for c in value)
            and all(not p.startswith(".") and not p.endswith(".lock") for p in value.split("/")))


def git_environment():
    """No inherited Git overrides, credential helpers, proxies or enterprise env."""
    return {
        "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": "",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
