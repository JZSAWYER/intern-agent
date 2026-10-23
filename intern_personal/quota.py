"""Observed account quota and stable footers for completed Codex replies."""
import json
import math
import time


MAX_AGE_SECONDS = 15 * 60
UNAVAILABLE = 'Quota: unavailable (Codex did not report current account limits).'


def schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS conversation_quota (
            session TEXT PRIMARY KEY, limits TEXT NOT NULL, observed_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS reply_quota (
            seq INTEGER PRIMARY KEY, line TEXT NOT NULL);
    ''')


def number(value):
    try:
        return type(value) in {int, float} and math.isfinite(value)
    except OverflowError:
        return False


def normalize(data):
    """Keep only numeric quota windows; never persist account/credit identities."""
    if not isinstance(data, dict):
        return None
    if 'rateLimits' in data:
        limits = data['rateLimits']
    elif 'rate_limits' in data:
        limits = data['rate_limits']
    else:
        buckets = data.get('rateLimitsByLimitId')
        if not isinstance(buckets, dict):
            return None
        limits = buckets.get('codex')
        if limits is None and len(buckets) == 1:
            limits = next(iter(buckets.values()))
    if not isinstance(limits, dict):
        return {}
    result = {}
    for key in ['primary', 'secondary']:
        window = limits.get(key)
        if not isinstance(window, dict):
            continue
        used = window.get('usedPercent', window.get('used_percent'))
        if not number(used) or not 0 <= used <= 100:
            continue
        clean = {'usedPercent': used}
        duration = window.get('windowDurationMins', window.get('window_minutes'))
        if number(duration) and duration > 0:
            clean['windowDurationMins'] = duration
        reset = window.get('resetsAt', window.get('resets_at'))
        if number(reset) and reset > 0:
            clean['resetsAt'] = reset
        result[key] = clean
    return result


def snapshot(state, session):
    row = state.db.execute('SELECT limits,observed_at FROM conversation_quota WHERE session=?',
                           (session['id'],)).fetchone()
    return (json.loads(row['limits']), row['observed_at']) if row else ({}, 0)


def observe(state, session, data, observed_at=None):
    limits = normalize(data)
    if limits is None:
        return
    at = time.time() if observed_at is None else observed_at
    if not number(at) or at < 0:
        return
    state.db.execute('''INSERT INTO conversation_quota VALUES(?,?,?)
        ON CONFLICT(session) DO UPDATE SET limits=excluded.limits,observed_at=excluded.observed_at
        WHERE conversation_quota.observed_at<=excluded.observed_at''',
        (session['id'], json.dumps(limits, allow_nan=False), at))


def percent(value):
    return f'{value:.1f}'.rstrip('0').rstrip('.')


def duration(minutes, fallback):
    for unit, divisor in [('d', 1440), ('h', 60)]:
        if minutes and minutes % divisor == 0:
            return str(int(minutes / divisor)) + unit
    return f'{minutes:g}m' if minutes else fallback


def format_line(observed, now=None):
    limits, at = observed
    now = time.time() if now is None else now
    if not 0 <= now - at <= MAX_AGE_SECONDS:
        return UNAVAILABLE
    parts = []
    for key in ['primary', 'secondary']:
        window = limits.get(key)
        if not window or window.get('resetsAt', now + 1) <= now:
            continue
        used = window['usedPercent']
        label = duration(window.get('windowDurationMins'), key.capitalize())
        parts.append(f'{label}: {percent(used)}% used, {percent(100 - used)}% left')
    return 'Quota (account): ' + ' | '.join(parts) if parts else UNAVAILABLE


def freeze(state, seq, observed):
    """Freeze before sending so retries keep the same idempotent reply payload."""
    row = state.db.execute('SELECT line FROM reply_quota WHERE seq=?', (seq,)).fetchone()
    if row:
        return row['line']
    line = format_line(observed)
    state.db.execute('INSERT OR IGNORE INTO reply_quota VALUES(?,?)', (seq, line))
    return state.db.execute('SELECT line FROM reply_quota WHERE seq=?', (seq,)).fetchone()['line']


def completed(state, session, connection, seq=None):
    """Refresh on the owned turn connection; quota failure cannot fail inference."""
    try:
        observe(state, session, connection.request('account/rateLimits/read', {}, timeout=2))
    except Exception:
        pass
    if seq is not None:
        freeze(state, seq, snapshot(state, session))


def reply(state, item):
    if item['role'] != 'agent':
        return item['result']
    session = {'id': item['session']}
    line = freeze(state, item['seq'], snapshot(state, session))
    from .throughput import line as speed_line
    speed = speed_line(state, item)
    from .goals import footer as goal_footer
    goal=goal_footer(state,item)
    # An older receiver may have attempted this UUID before footers existed.
    # Keep that original body byte for byte when its delivery is reconciled.
    footers = [text for text in [goal,speed,line] if text]
    return item['result'].rstrip() + '\n\n' + '\n'.join(footers) if footers else item['result']
