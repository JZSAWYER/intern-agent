"""Explicit per-session development reminders; no prompt or history rewriting."""
from .core import Invalid
from .live_state import private_json, write_private


POLICY_FILE = 'tutorial-policy.json'
REMINDER = (
    'Feature/tutorial workflow enabled for this conversation only. '
    'For feature implementation or debugging, follow repository AGENTS.md and '
    'docs/DEVELOPMENT_WORKFLOW.md when present. Inspect the existing implementation, '
    'reproduce the issue or define expected behavior, state acceptance criteria, '
    'make a focused change, and verify it with relevant checks. '
    'Before reporting completion, update the user-facing Markdown tutorial and '
    'change record (docs/FEATURE_GUIDE.md and docs/CHANGELOG.md when present) with '
    'usage, expected results, actual verification and remaining limitations. '
    'Reuse existing feature guides; distinguish implemented behavior from live '
    'acceptance and planned work. Document fixes with their symptom and cause. '
    'Conversation history is recorded separately by the relay and does not '
    'replace a tutorial. Ordinary conversation and read-only questions do not '
    'require a new change entry. This reminder grants no new authorization.'
)


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'schema', 'sessions'}
            or type(value['schema']) is not int or value['schema'] != 1
            or not isinstance(value['sessions'], list) or len(value['sessions']) > 2
            or any(not isinstance(item, str) or not item or len(item) > 100
                   or any(c in item for c in ['\0', '\n', '\r']) for item in value['sessions'])
            or len(set(value['sessions'])) != len(value['sessions'])):
        raise Invalid('Tutorial reminders require an explicit allowlist of at most two sessions')
    return value


def configure(state, sessions):
    """Trusted host setup only: select existing sessions, without touching their turns."""
    value = validate({'schema': 1, 'sessions': sessions})
    if any(state.session(item) is None for item in sessions):
        raise Invalid('Tutorial reminders can select only registered sessions')
    write_private(state.root / POLICY_FILE, value)


def enabled(state, session):
    try:
        value = private_json(state.root / POLICY_FILE)
    except FileNotFoundError:
        return False
    return session['id'] in validate(value)['sessions']
