"""Classify transport failures without copying Git's potentially private stderr."""
from .core import Invalid


def failure(operation,code,stderr):
    from .workspace_tracking import TrackingError
    text=stderr[:16384].lower()
    if 'intern agent git guard blocked' in text or 'hook declined' in text:
        return Invalid('Git '+operation+' was rejected by a local validation hook; check the format/privacy guard. A network retry cannot repair it.')
    if any(s in text for s in ['non-fast-forward','fetch first','stale info','cannot lock ref','is at ','but expected']):
        return Invalid('Git '+operation+' found a changed branch/ref; reconcile the saved commit without a force push.')
    if any(s in text for s in ['permission denied (publickey)','authentication failed','repository not found','could not read username','invalid username or token']):
        return Invalid('Git '+operation+' was denied by authentication or repository access; review access without changing stored credentials automatically.')
    if 'index.lock' in text or 'another git process' in text:
        return TrackingError('Git '+operation+' is waiting for a local repository lock; retry the saved operation without rerunning Codex.')
    if any(s in text for s in ['could not resolve','connection timed out','connection reset','connection refused','network is unreachable','remote end hung up','tls','502','503','504','429']):
        return TrackingError('Git '+operation+' encountered a network/server transport failure; its saved upload will retry.')
    return TrackingError('Git '+operation+' failed (exit '+str(code)+'); its result remains saved for diagnosis.')
