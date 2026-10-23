"""Validate future package inputs without building, uploading or publishing."""
import json
import ast
from pathlib import Path
import re

from .core import Invalid


PRIVATE_PARTS = {"runtime", "reference", "reports", ".git", ".ssh", "__pycache__", "node_modules"}
PRIVATE_NAMES = {"personal.json", "bindings.json", "STATUS.md", ".env"}
KEY_PATTERN = re.compile(r"(?<![A-Za-z0-9_])(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})")
SENSITIVE_NAME = re.compile(r"(?:^|_)(?:secret|token|password|api_key)(?:$|_)", re.I)


def linked_input(root,file):
    try:relative=file.relative_to(root)
    except ValueError:return True
    return any(root.joinpath(*relative.parts[:index]).is_symlink() for index in range(1,len(relative.parts)+1))


def credential_literals(file, text):
    if file.suffix == ".py":
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if (isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value
                        and any(isinstance(t, ast.Name) and SENSITIVE_NAME.search(t.id) for t in targets)):
                    return True
    if file.suffix == ".json":
        def contains(value):
            if isinstance(value, dict):
                return any((SENSITIVE_NAME.search(k) and bool(v)) or contains(v) for k, v in value.items())
            if isinstance(value, list):
                return any(contains(v) for v in value)
            return False
        return contains(json.loads(text))
    return False


def package_files(project, private_owner=None):
    root = Path(project).resolve()
    manifest = json.loads((root / "distribution.json").read_text())
    if manifest.get("schema") != "intern-agent.distribution-files.v1":
        raise Invalid("Unknown distribution schema")
    patterns = manifest.get("include")
    if not isinstance(patterns, list) or not patterns:
        raise Invalid("A package allowlist is required")
    result = set()
    for pattern in patterns:
        if (not isinstance(pattern, str) or Path(pattern).is_absolute()
                or ".." in Path(pattern).parts):
            raise Invalid("Invalid package pattern")
        for file in root.glob(pattern):
            if not file.is_file():
                continue
            relative = file.relative_to(root)
            if (linked_input(root,file) or not file.resolve().is_relative_to(root)
                    or any(p in PRIVATE_PARTS for p in relative.parts)
                    or file.name in PRIVATE_NAMES
                    or any(word in file.name.casefold() for word in ["secret", "credential"])
                    or file.suffix in {".env", ".sqlite3", ".pem", ".key", ".pyc"}):
                raise Invalid("Private or linked file in package allowlist")
            text = file.read_text()
            if KEY_PATTERN.search(text) or credential_literals(file, text):
                raise Invalid("Credential-like literal in package input")
            if private_owner and private_owner.casefold() in text.casefold():
                raise Invalid("Private GitHub username in package input")
            result.add(str(relative))
    if not result:
        raise Invalid("Package allowlist matched no files")
    return sorted(result)
