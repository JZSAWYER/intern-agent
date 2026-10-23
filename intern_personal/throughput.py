"""Per-turn reported output throughput, not a server decoding benchmark."""
import json
import math

BOOTSTRAP_BYTES=8*1024*1024
LIMIT=2**63-1
UNAVAILABLE='Speed (turn avg): unavailable (missing per-turn token/timing data).'


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS throughput_counters(
        session TEXT NOT NULL,thread TEXT NOT NULL,counter TEXT NOT NULL,at REAL NOT NULL,PRIMARY KEY(session,thread));
        CREATE TABLE IF NOT EXISTS turn_throughput(
        session TEXT NOT NULL,thread TEXT NOT NULL,turn TEXT NOT NULL,seq INTEGER,
        data TEXT NOT NULL,quality INTEGER NOT NULL,PRIMARY KEY(session,thread,turn));
        CREATE TABLE IF NOT EXISTS reply_throughput(seq INTEGER PRIMARY KEY,line TEXT NOT NULL);''')
    db.execute('''CREATE TABLE IF NOT EXISTS throughput_views(session TEXT NOT NULL,thread TEXT NOT NULL,
        observer TEXT NOT NULL,cursor INTEGER NOT NULL,inode TEXT NOT NULL,data TEXT NOT NULL,
        PRIMARY KEY(session,thread,observer))''')


def number(value):
    try:return type(value) in {int,float} and math.isfinite(value) and 0<=value<=LIMIT
    except OverflowError:return False


def counter(data):
    if not isinstance(data,dict):return None
    output=data.get('output_tokens',data.get('outputTokens'))
    reasoning=data.get('reasoning_output_tokens',data.get('reasoningOutputTokens'))
    if type(output) is not int or not 0<=output<=LIMIT:return None
    if reasoning is not None and (type(reasoning) is not int or not 0<=reasoning<=output):return None
    return dict(output=output,reasoning=reasoning)


def cached(state,session):
    row=state.db.execute('SELECT counter FROM throughput_counters WHERE session=? AND thread=?',
                         (session['id'],session['thread_id'])).fetchone()
    return json.loads(row['counter']) if row else None


def cache(state,session,value,at=0):
    state.db.execute('''INSERT INTO throughput_counters VALUES(?,?,?,?) ON CONFLICT(session,thread)
        DO UPDATE SET counter=excluded.counter,at=excluded.at WHERE excluded.at>=throughput_counters.at
        AND (excluded.counter!=throughput_counters.counter OR excluded.at>throughput_counters.at)''',
        (session['id'],session['thread_id'],json.dumps(value,allow_nan=False),at or 0))


def empty(previous=None):return dict(version=1,previous=previous,active=None,at=0)


def load_view(state,session,observer,cursor,inode):
    row=state.db.execute('SELECT * FROM throughput_views WHERE session=? AND thread=? AND observer=?',
                         (session['id'],session['thread_id'],observer)).fetchone()
    return json.loads(row['data']) if row and row['cursor']==cursor and row['inode']==str(inode) else None


def save_view(state,session,observer,cursor,inode,data):
    state.db.execute('''INSERT INTO throughput_views VALUES(?,?,?,?,?,?) ON CONFLICT(session,thread,observer)
        DO UPDATE SET cursor=excluded.cursor,inode=excluded.inode,data=excluded.data
        WHERE throughput_views.cursor!=excluded.cursor OR throughput_views.inode!=excluded.inode OR throughput_views.data!=excluded.data''',
        (session['id'],session['thread_id'],observer,cursor,str(inode),json.dumps(data,allow_nan=False)))


def begin(view,turn,at=None):
    view['active']=dict(turn=turn,baseline=view['previous'],latest=None,started=at,error=None)


def usage(view,data):
    value=counter(data);active=view['active']
    if value is None:
        if active:active['error']='invalid usage'
        return
    before=view['previous']
    if active:
        if before and (value['output']<before['output'] or (value['reasoning'] is not None
                and before['reasoning'] is not None and value['reasoning']<before['reasoning'])):active['error']='counter reset'
        active['latest']=value
    view['previous']=value


def finish(view,turn,status,at=None,duration_ms=None):
    active=view['active']
    if not active or active['turn']!=turn:return None
    result=dict(turn=turn,status=status,output_tokens=None,duration_ms=None,reasoning_tokens=None)
    if not number(duration_ms) or duration_ms==0:
        start=active['started']
        duration_ms=(at-start)*1000 if number(at) and number(start) and at>start else None
    if number(duration_ms) and duration_ms>0:result['duration_ms']=duration_ms
    before=active['baseline'];after=active['latest']
    if status=='completed' and not active['error'] and before is not None and after is not None:
        output=after['output']-before['output']
        if 0<=output<=LIMIT:
            result['output_tokens']=output
            if before['reasoning'] is not None and after['reasoning'] is not None:
                reasoning=after['reasoning']-before['reasoning']
                if not 0<=reasoning<=output:result['output_tokens']=None
                else:result['reasoning_tokens']=reasoning
    if status!='completed' or after is None or active['error']=='invalid usage':view['previous']=None
    if number(at):view['at']=max(view['at'],at)
    view['active']=None
    return result


def timestamp(value):
    if number(value):return value
    from .speed import observation_time
    at=observation_time(value)
    return at if at>0 else None


def native(view,value):
    if not isinstance(value,dict) or value.get('type')!='event_msg':return None
    payload=value.get('payload') or {}
    if not isinstance(payload,dict):return None
    kind=payload.get('type');at=timestamp(value.get('timestamp'))
    if kind=='task_started':begin(view,payload.get('turn_id'),timestamp(payload.get('started_at')) or at)
    elif kind=='token_count':
        info=payload.get('info')
        if isinstance(info,dict) and 'total_token_usage' in info:
            usage(view,info['total_token_usage']);view['at']=at or view['at']
    elif kind in {'task_complete','turn_aborted'}:
        status='interrupted' if kind=='turn_aborted' else 'failed' if payload.get('error') else 'completed'
        return finish(view,payload.get('turn_id'),status,timestamp(payload.get('completed_at')) or at,payload.get('duration_ms'))
    return None


def bootstrap(path,cursor):
    """Seed numeric state behind the existing cursor; never replay any prompt.

    A bounded prefix may lack the starting counter. That turn stays unknown;
    a later fully observed boundary makes subsequent turns measurable.
    """
    offset=max(0,cursor-BOOTSTRAP_BYTES)
    view=empty(dict(output=0,reasoning=0) if offset==0 else None)
    with path.open('rb') as stream:
        stream.seek(offset)
        if offset:stream.readline(cursor-offset+1)
        while stream.tell()<cursor:
            line=stream.readline(min(BOOTSTRAP_BYTES,cursor-stream.tell())+1)
            if stream.tell()>cursor or not line.endswith(b'\n'):break
            try:native(view,json.loads(line))
            except (ValueError,TypeError,AttributeError):return empty()
    return view


def valid(data):
    return (data.get('status')=='completed' and type(data.get('output_tokens')) is int
            and 0<=data['output_tokens']<=LIMIT and number(data.get('duration_ms')) and data['duration_ms']>=1)


def save(state,session,seq,data,source):
    if data is None or not data.get('turn'):return
    quality=(10 if valid(data) else 0)+(2 if source=='transcript' else 1)
    state.db.execute('''INSERT INTO turn_throughput VALUES(?,?,?,?,?,?) ON CONFLICT(session,thread,turn)
        DO UPDATE SET seq=COALESCE(turn_throughput.seq,excluded.seq),data=excluded.data,quality=excluded.quality
        WHERE excluded.quality>=turn_throughput.quality''',
        (session['id'],session['thread_id'],data['turn'],seq,json.dumps(data,allow_nan=False),quality))


def render(data):
    if not valid(data):return UNAVAILABLE
    seconds=data['duration_ms']/1000;rate=data['output_tokens']/seconds
    duration=f'{seconds:.2f}' if seconds>=1 else f'{seconds:.3f}'
    return f"Speed (turn avg): {rate:.1f} output tok/s ({data['output_tokens']:,} tokens / {duration} s; includes tools)."


def line(state,item):
    frozen=state.db.execute('SELECT line FROM reply_throughput WHERE seq=?',(item['seq'],)).fetchone()
    if frozen:return frozen['line']
    row=state.db.execute('SELECT data FROM turn_throughput WHERE seq=? AND session=? ORDER BY quality DESC LIMIT 1',
                         (item['seq'],item['session'])).fetchone()
    # Explicit offline adapters have no observed Codex execution. Do not change
    # their payloads or backfill an older already-attempted delivery.
    if not row:return ''
    text=render(json.loads(row['data']))
    state.db.execute('INSERT OR IGNORE INTO reply_throughput VALUES(?,?)',(item['seq'],text))
    return state.db.execute('SELECT line FROM reply_throughput WHERE seq=?',(item['seq'],)).fetchone()['line']
