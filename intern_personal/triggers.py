"""Per-conversation group triggers, matched against this app's verified bot identity."""
from .core import Invalid


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS trigger_settings(session TEXT PRIMARY KEY,mode TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bot_identity(app_id TEXT PRIMARY KEY,open_id TEXT NOT NULL);''')


def identity(state):
    row=state.db.execute('SELECT open_id FROM bot_identity WHERE app_id=?',(state.identity()['app_id'],)).fetchone()
    return row[0] if row else None


def mentioned(state,message):
    own=identity(state)
    mentions=message.get('mentions',[])
    if not isinstance(mentions,list):return False
    return bool(own and any(isinstance(m,dict) and isinstance(m.get('id'),dict)
        and m['id'].get('open_id')==own and isinstance(m.get('key'),str) and m['key']
        and m['key'] in message.get('content','') for m in mentions))


def normalize(state,message,text):
    import re
    own=identity(state)
    if not own:return re.sub(r'^(?:@_user_\d+\s*)+','',text)  # Retain transport compatibility until identity is resolved.
    mentions=message.get('mentions',[])
    keys={m['key'] for m in mentions if isinstance(m,dict) and isinstance(m.get('id'),dict)
          and m['id'].get('open_id')==own and isinstance(m.get('key'),str)} if isinstance(mentions,list) else set()
    while True:
        match=re.match(r'^(@_user_\d+)\s*',text)
        if not match or match[1] not in keys:return text
        text=text[match.end():]


def accepts(state,session,message):
    row=state.db.execute('SELECT mode FROM trigger_settings WHERE session=?',(session['id'],)).fetchone()
    return not row or row[0]=='all' or mentioned(state,message)


def command(relay,session,mode):
    if not session:raise Invalid('Choose a workspace conversation')
    if mode not in {'all','mentions'}:raise Invalid('Use /trigger all|mentions')
    if mode=='mentions':
        from .live_state import identifier
        own=relay.api.bot_identity()
        if not identifier(own,'ou'):raise Invalid('This app bot identity could not be verified; trigger mode remains unchanged')
        relay.state.db.execute('INSERT OR REPLACE INTO bot_identity VALUES(?,?)',(relay.state.identity()['app_id'],own))
    relay.state.db.execute('INSERT OR REPLACE INTO trigger_settings VALUES(?,?)',(session['id'],mode))
    return 'Group trigger: '+mode+'. Slash controls including /stop remain available.'
