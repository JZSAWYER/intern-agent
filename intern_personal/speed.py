"""Per-conversation speed tiers and verified native terminal controls."""
import json
import time
from pathlib import Path

from .core import Invalid
from .live_state import private_json

TIERS = frozenset({'default', 'priority', 'fast', 'ultrafast'})


def known_tier(tier):
    return tier is None or isinstance(tier, str) and tier in TIERS


def tier_kind(tier):
    if tier is None or tier == 'default':
        return 'standard'
    if is_fast(tier):
        return 'fast'
    if tier == 'ultrafast':
        return 'ultrafast'
    return None


def same_tier(left, right):
    """Fast's legacy aliases match; an unknown or Ultrafast tier is not Standard."""
    kind = tier_kind(left)
    return kind is not None and kind == tier_kind(right)


def label(kind):
    return 'Ultrafast' if kind == 'ultrafast' else 'Fast'


def schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS session_service_tiers (
            session TEXT PRIMARY KEY, tier TEXT NOT NULL, updated_at REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS fast_controls (
            seq INTEGER PRIMARY KEY, session TEXT NOT NULL, tier TEXT NOT NULL,
            phase TEXT NOT NULL);
    ''')
    if 'updated_at' not in {row[1] for row in db.execute('PRAGMA table_info(session_service_tiers)')}:
        db.execute('ALTER TABLE session_service_tiers ADD COLUMN updated_at REAL NOT NULL DEFAULT 0')


def saved(state, session):
    row = state.db.execute('SELECT tier FROM session_service_tiers WHERE session=?', (session['id'],)).fetchone()
    return row['tier'] if row else None


def save(state, session, tier):
    if not state.session(session['id']) or not isinstance(tier, str) or tier not in TIERS:
        raise Invalid('Invalid conversation service tier')
    state.db.execute('INSERT OR REPLACE INTO session_service_tiers VALUES(?,?,?)', (session['id'], tier, time.time()))


def observation_time(timestamp):
    from datetime import datetime
    try:
        stamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        return stamp.timestamp() if stamp.tzinfo is not None else 0
    except (AttributeError, TypeError, ValueError):
        return 0


def save_observed(state, session, tier, timestamp):
    if not known_tier(tier):
        return
    at = observation_time(timestamp)
    # A late observer must not overwrite a newer Feishu preference with an old
    # console setting. Newer direct-console changes still take precedence.
    state.db.execute('''INSERT INTO session_service_tiers VALUES(?,?,?)
        ON CONFLICT(session) DO UPDATE SET tier=excluded.tier,updated_at=excluded.updated_at
        WHERE session_service_tiers.updated_at<=excluded.updated_at''',
        (session['id'], tier or 'default', at))


def fast_tier(model):
    return advertised_tier(model, 'fast')


def advertised_tier(model, kind):
    allowed = {'priority', 'fast'} if kind == 'fast' else {'ultrafast'}
    for entry in model.get('serviceTiers') or []:
        if isinstance(entry, dict) and isinstance(entry.get('id'), str) and entry['id'] in allowed:
            return entry['id']
    if kind in (model.get('additionalSpeedTiers') or []):
        return kind
    raise Invalid('This model does not advertise ' + label(kind) +
                  ' mode in the current account\'s Codex catalogue. No setting changed. ' +
                  ('Check Ultrafast account/workspace eligibility and refresh Codex before retrying.' if kind == 'ultrafast' else ''))


def validate_model_tier(model, tier):
    kind = tier_kind(tier)
    if kind in {'fast', 'ultrafast'}:
        advertised_tier(model, kind)


def observed(adapter, session):
    row = adapter.binding(session)
    if not row:
        return None, False
    config = private_json(row['config_path'])
    if config.get('thread_id') != session['thread_id']:
        raise Invalid('Service-tier transcript belongs to a different conversation')
    snapshot = adapter.poll(session)
    if 'service_tier' in snapshot:
        return snapshot['service_tier'], True
    # Older relay snapshots omitted this field. Read only typed settings from
    # the pinned transcript; never infer Fast mode from prose, a footer or config.
    latest, known, total = None, False, 0
    with Path(config['rollout']).open('rb') as stream:
        while line := stream.readline(2 * 1024 * 1024 + 1):
            total += len(line)
            if len(line) > 2 * 1024 * 1024 or total > 64 * 1024 * 1024:
                raise Invalid('Service-tier observation needs transcript inspection; no setting changed')
            if not line.endswith(b'\n'):
                break
            value = json.loads(line)
            payload = value.get('payload', {})
            if (value.get('type') == 'event_msg' and payload.get('type') == 'thread_settings_applied'
                    and payload.get('thread_id') == session['thread_id']):
                settings = payload.get('thread_settings', {})
                if 'service_tier' in settings:
                    latest, known = settings['service_tier'], True
    return latest, known


def is_fast(tier):
    return isinstance(tier,str) and tier in {'priority', 'fast'}


def status(relay, session, kind='fast'):
    native = bool(relay.terminal.binding(session))
    adapter = relay.terminal if native else getattr(relay.host, 'observer', None)
    tier, known = observed(adapter, session) if adapter else (None, False)
    preference = saved(relay.state, session)
    selected = label(kind)
    if known:
        result = selected + ' mode: ' + ('ON' if tier_kind(tier) == kind else 'OFF' if known_tier(tier) else 'other service tier')
        result += '\nObserved Codex service tier: ' + str(tier or 'default')
    else:
        result = selected + ' mode: not yet observed'
    if not native and preference is not None:
        result += '\nSaved for the next turn: ' + ('ON' if tier_kind(preference) == kind else 'OFF') + ' (' + preference + ')'
    if relay.state.db.execute("SELECT 1 FROM fast_controls WHERE session=? AND phase='dispatched'", (session['id'],)).fetchone():
        result += '\nA speed command has an unverified outcome; inspect its console or retry the explicit setting after review.'
    return result


def native_apply(relay, session, item, target, kind='fast'):
    from .terminal_bridge import verify_pane, empty_prompt, tmux
    adapter, state = relay.terminal, relay.state
    tier, known = observed(adapter, session)
    if not known:
        raise Invalid('Native Codex service tier is not observed. Inspect /fast in its original console; no setting changed.')
    if not known_tier(tier):
        raise Invalid('Native Codex uses another service tier. Inspect its /fast setting before changing it from Feishu.')
    pending = state.db.execute("SELECT * FROM fast_controls WHERE session=? AND phase='dispatched'", (session['id'],)).fetchall()
    for row in pending:
        if same_tier(tier, row['tier']):
            state.db.execute("UPDATE fast_controls SET phase='verified' WHERE seq=?", (row['seq'],))
        else:
            raise Invalid('A native Fast command has an uncertain outcome. Inspect its console before another change; it was not replayed.')
    enabled = tier_kind(target) == kind
    selected = label(kind)
    if same_tier(tier, target):
        config = private_json(adapter.binding(session)['config_path'])
        verify_pane(config, timeout=1)
        state.db.execute("UPDATE fast_controls SET phase='verified' WHERE seq=?", (item['seq'],))
        return selected + ' mode already ' + ('ON' if enabled else 'OFF') + '; verified in the native Codex terminal.'
    with adapter.input_lock, adapter.lock:
        snapshot = adapter.poll(session)
        if 'service_tier' in snapshot and not known_tier(snapshot['service_tier']):
            raise Invalid('Native service tier changed; inspect its console before toggling')
        if 'service_tier' in snapshot and same_tier(snapshot['service_tier'], target):
            config = private_json(adapter.binding(session)['config_path'])
            verify_pane(config, timeout=1)
            state.db.execute("UPDATE fast_controls SET phase='verified' WHERE seq=?", (item['seq'],))
            return selected + ' mode already ' + ('ON' if enabled else 'OFF') + '; verified in the native Codex terminal.'
        if snapshot.get('active') or snapshot.get('pending'):
            raise Invalid('Native Codex is busy. Wait for completion, then retry /' + kind + '; the current turn was not interrupted.')
        # CLI 0.162.0 exposes a Fast toggle, not an explicit arbitrary-tier
        # command. Never paste an invented /ultrafast prompt or use /fast to
        # turn Ultrafast into the wrong tier. Observe manual picker changes.
        if tier_kind(tier) == 'ultrafast' or tier_kind(target) == 'ultrafast':
            raise Invalid('This attached CLI has no verified Ultrafast command adapter. Its Fast toggle cannot safely select another service tier. '
                          'Choose the speed in the original Codex model picker, then use /ultrafast status here. '
                          'Managed project bots support /ultrafast on|off directly. No terminal keys were sent.')
        config = private_json(adapter.binding(session)['config_path'])
        if config.get('thread_id') != session['thread_id']:
            raise Invalid('Native Fast command belongs to a different conversation')
        verify_pane(config, timeout=1)
        empty_prompt(config)
        # Persist before sending a toggle. A disconnected acknowledgement must
        # never lead to a second toggle or silently reset the owner's preference.
        state.db.execute("UPDATE fast_controls SET phase='dispatched' WHERE seq=?", (item['seq'],))
        tmux(config, 'send-keys', '-l', '-t', config['pane'], '/fast', timeout=1)
        tmux(config, 'send-keys', '-t', config['pane'], 'Enter', timeout=1)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        tier, known = observed(adapter, session)
        if known and same_tier(tier, target):
            state.db.execute("UPDATE fast_controls SET phase='verified' WHERE seq=?", (item['seq'],))
            return selected + ' mode ' + ('ON' if enabled else 'OFF') + '; verified in the native Codex terminal.'
        if adapter.stop.wait(.1):
            break
    raise Invalid('Native Fast command was sent, but its result is not verified. Inspect the console; it will not be toggled again automatically.')


def command(relay, item, management):
    kind = 'ultrafast' if item['text'].split()[0].lower() == '/ultrafast' else 'fast'
    selected = label(kind)
    tokens = item['text'].split()[1:]
    session = relay.state.session(tokens.pop(0) if management and tokens else item.get('session'))
    if not session or len(tokens) > 1 or (tokens and tokens[0].lower() not in {'on', 'off', 'status'}):
        raise Invalid('Use /' + kind + ' [on|off|status], or /' + kind + ' SESSION [on|off|status] in Control')
    mode = tokens[0].lower() if tokens else 'toggle'
    if mode == 'status':
        return status(relay, session, kind)
    if item['sender'] != relay.state.identity()['owner']:
        raise Invalid('Only the owner can change ' + selected + ' mode')
    if session['state'] not in {'ready', 'paused'}:
        raise Invalid('Finish conversation setup before changing Fast mode')
    native = bool(relay.terminal.binding(session))
    adapter = relay.terminal if native else getattr(relay.host, 'observer', None)
    current, known = None, False
    if not native and adapter:
        try:current,known=observed(adapter,session)
        except Exception:pass  # Missing observation cannot certify a change.
    known=bool(known and known_tier(current))
    observed_current=current
    receipt = relay.state.db.execute('SELECT * FROM fast_controls WHERE seq=?', (item['seq'],)).fetchone()
    if receipt:
        if receipt['session'] != session['id']:
            raise Invalid('Fast command target changed; inspect its existing receipt')
        target = receipt['tier']
        if receipt['phase'] == 'verified':
            return 'This command already verified ' + selected + ' mode ' + ('ON' if tier_kind(target) == kind else 'OFF') + '; it was not executed again. Use /' + kind + ' status for the current setting.'
    else:
        preference = saved(relay.state, session)
        if native or preference is None:
            if native:current, known = observed(adapter, session)
            if native and not known:
                raise Invalid('Native Fast state is unknown; inspect the original console before toggling')
            if native:observed_current=current
        else:
            current = preference
        enabled = mode == 'on' or (mode == 'toggle' and tier_kind(current) != kind)
        already_selected = known and mode in {'on','off'} and (
            tier_kind(observed_current) == kind if enabled else tier_kind(observed_current) == 'standard')
        if already_selected:
            target=observed_current or 'default'
        elif enabled:
            try:
                selection = relay.terminal.selection(session) if native else relay.host.model_selection(session)
                model = next((row for row in relay.host.models(session=None if native else session) if row['model'] == selection['model']), None)
            except Invalid:raise
            except Exception as error:
                relay.last_error=type(error).__name__
                raise Invalid(selected + ' settings are unavailable; no setting change was sent. Use /' + kind + ' status or retry the explicit /' + kind + ' on|off command when connected.') from None
            if not model:
                raise Invalid('Current model is unavailable in the Codex catalogue; no Fast setting changed')
            target = advertised_tier(model, kind)
        else:
            target = 'default'
        relay.state.db.execute("INSERT INTO fast_controls VALUES(?,?,?,'prepared')", (item['seq'], session['id'], target))
    if native:
        result = native_apply(relay, session, item, target, kind)
    else:
        if known and same_tier(observed_current, target):
            actual=observed_current or 'default'
            result=selected+' mode already '+('ON' if tier_kind(target) == kind else 'OFF')+'; verified in Codex recorded settings. No settings request or Codex prompt was sent.'
        else:
            relay.state.db.execute("UPDATE fast_controls SET phase='dispatched' WHERE seq=?", (item['seq'],))
            try:
                actual = relay.host.set_service_tier(session, target)
                result = selected + ' mode ' + ('ON' if tier_kind(target) == kind else 'OFF') + ' verified in Codex and saved for this conversation. Applies to the next turn; the current turn is unchanged.'
            except Exception as error:
                relay.last_error=type(error).__name__
                try:actual,confirmed=observed(adapter,session) if adapter else (None,False)
                except Exception:actual,confirmed=None,False
                confirmed=bool(confirmed and known_tier(actual))
                if not confirmed or not same_tier(actual, target):
                    raise Invalid(selected + ' mode was not confirmed. No Codex prompt was submitted by this command; the current task continues. Use /' + kind + ' status to inspect the setting. The command will not be replayed automatically.') from None
                result=selected+' mode '+('ON' if tier_kind(target) == kind else 'OFF')+' confirmed from Codex recorded settings after its API acknowledgement was unavailable. No command was replayed.'
        if not same_tier(actual, target):
            raise Invalid('Codex service-tier acknowledgement did not match; inspect /' + kind + ' status')
        save(relay.state, session, actual or 'default')
        relay.state.db.execute("UPDATE fast_controls SET phase='verified' WHERE seq=?", (item['seq'],))
    from .turn_uploads import enqueue_status
    enqueue_status(relay.state, session)
    return result + (' Increased usage applies; speed depends on the model and account.' if tier_kind(target) in {'fast','ultrafast'} else '')
