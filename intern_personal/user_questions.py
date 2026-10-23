"""Durable structured questions, Feishu forms and exact native/RPC answers."""
import hashlib
import json
import re
import secrets
import time

from .core import Invalid

HELP=('Question tutorial\nCodex questions appear as Feishu cards with choices and a custom-answer field. '
      'Submit all questions together, or answer them in order with /answer CODE TEXT. '
      '/answer CODE {"question_id":"answer"} answers named questions; arrays select multiple options when allowed. '
      '/questions shows pending questions. An ordinary text reply answers the sole blocking question request in this chat. '
      'Commands such as /stop retain their usual meaning. Non-blocking questions require a card or /answer. '
      'Cards require the published card.action.trigger long-connection callback. Secrets stay in a secure local input.')
OPEN=('new','pending','answered','sending','delivery_failed')


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS user_questions(
        code TEXT PRIMARY KEY,session TEXT NOT NULL,thread TEXT NOT NULL,turn TEXT NOT NULL,
        request TEXT NOT NULL,source TEXT NOT NULL,questions TEXT NOT NULL,blocking INTEGER NOT NULL,item TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL,answers TEXT NOT NULL DEFAULT '{}',receipt TEXT,error_type TEXT,
        delivery_state TEXT NOT NULL DEFAULT 'queued',delivery_attempts INTEGER NOT NULL DEFAULT 0,retry_at REAL NOT NULL DEFAULT 0,ui_closed INTEGER NOT NULL DEFAULT 0,
        task_id TEXT,decision_saved INTEGER NOT NULL DEFAULT 0,answered_at REAL,
        created_at REAL NOT NULL,expires_at REAL NOT NULL,UNIQUE(session,thread,turn,request));
        CREATE TABLE IF NOT EXISTS question_callbacks(key TEXT PRIMARY KEY,code TEXT NOT NULL,received_at REAL NOT NULL);''')


def normalize(questions):
    if not isinstance(questions,list) or not 1<=len(questions)<=10:raise Invalid('Expected one to ten structured questions')
    out=[];ids=set()
    for q in questions:
        if not isinstance(q,dict):raise Invalid('Invalid question')
        ident=q.get('id');text=q.get('question');header=q.get('header','')
        if (not isinstance(ident,str) or not 1<=len(ident)<=128 or ident in ids or not isinstance(text,str) or not 1<=len(text)<=4000
                or not isinstance(header,str) or len(header)>200):raise Invalid('Invalid question identity or text')
        options=[];labels=set()
        for o in q.get('options') or []:
            if not isinstance(o,dict) or not isinstance(o.get('label'),str) or not 1<=len(o['label'])<=200 or o['label'] in labels:raise Invalid('Invalid question option')
            description=o.get('description','')
            if not isinstance(description,str) or len(description)>2000:raise Invalid('Invalid option description')
            labels.add(o['label']);options.append(dict(label=o['label'],description=description))
        if len(options)>30:raise Invalid('Too many question options')
        out.append(dict(id=ident,header=header,question=text,options=options,multiSelect=q.get('multiSelect',False) is True,isSecret=q.get('isSecret',False) is True))
        ids.add(ident)
    return out


def native_params(args,asynchronous=False):
    if not asynchronous:return args
    if not isinstance(args,dict):raise Invalid('Invalid asynchronous question')
    questions=[]
    for i,q in enumerate(args.get('questions') or []):
        if not isinstance(q,dict):raise Invalid('Invalid asynchronous question')
        options=q.get('options') or []
        questions.append(dict(id=q.get('id') or 'question_'+str(i+1),header=q.get('header') or 'Question '+str(i+1),
            question=q.get('question') or q.get('title'),options=[dict(label=o,description='') if isinstance(o,str) else o for o in options],
            multiSelect=q.get('multiSelect',False),isSecret=q.get('isSecret',False)))
    return dict(args,questions=normalize(questions),isBlocking=False)


def register(state,session,params,request,source='rpc'):
    if params.get('threadId') not in {None,session['thread_id']}:raise Invalid('Question belongs to another thread')
    questions=normalize(params.get('questions'));turn=params.get('turnId') or ''
    if not isinstance(turn,str) or len(turn)>150:raise Invalid('Invalid question turn')
    body=json.dumps(questions,ensure_ascii=False);item=params.get('itemId') or (str(request) if source=='tui' else '')
    prior=state.db.execute("SELECT * FROM user_questions WHERE session=? AND thread=? AND turn=? AND questions=? AND state IN ('new','pending','answered','sending','submitted') ORDER BY created_at DESC LIMIT 1",(session['id'],session['thread_id'],turn,body)).fetchone()
    if prior and (bool(item) and prior['item']==item or prior['source']!=source and (not item or not prior['item'])):
        if source=='rpc' and prior['source']=='tui':
            state.db.execute("UPDATE user_questions SET source='rpc',request=?,item=? WHERE code=?",(json.dumps(request),item,prior['code']))
        return dict(state.db.execute('SELECT * FROM user_questions WHERE code=?',(prior['code'],)).fetchone())
    code=secrets.token_hex(8);now=time.time()
    task=state.ledger.selection(session['id'])
    state.db.execute("INSERT OR IGNORE INTO user_questions(code,session,thread,turn,request,source,questions,blocking,item,task_id,state,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?, 'new',?,?)",
        (code,session['id'],session['thread_id'],turn,json.dumps(request),source,body,int(params.get('isBlocking',True)),item,task['id'] if task else None,now,now+6*3600))
    row=state.db.execute('SELECT * FROM user_questions WHERE session=? AND thread=? AND turn=? AND request=?',(session['id'],session['thread_id'],turn,json.dumps(request))).fetchone()
    return dict(row)


def pending(state,session,blocking=False):
    if not session:return []
    return [dict(r) for r in state.db.execute("SELECT * FROM user_questions WHERE session=? AND state IN ('new','pending','answered','sending','delivery_failed','uncertain') AND expires_at>?"+(' AND blocking=1' if blocking else '')+' ORDER BY created_at',(session['id'],time.time()))]


def card(row):
    questions=json.loads(row['questions']);elements=[];form=[]
    for i,q in enumerate(questions):
        elements.append(dict(tag='div',text=dict(tag='plain_text',content=str(i+1)+'. '+q['question'])))
        if q['options']:
            elements.append(dict(tag='div',text=dict(tag='plain_text',content='\n'.join(o['label']+(' — '+o['description'] if o['description'] else '') for o in q['options']))))
        if q['isSecret']:
            elements.append(dict(tag='div',text=dict(tag='plain_text',content='This question needs a secret. Use secure local input; do not send the value in Feishu.')));continue
        if q['options']:
            form.append(dict(tag='multi_select_static' if q['multiSelect'] else 'select_static',name='q'+str(i)+'_select',
                placeholder=dict(tag='plain_text',content=q['header'] or 'Choose an answer'),options=[dict(text=dict(tag='plain_text',content=o['label']),value=str(n)) for n,o in enumerate(q['options'])]))
        form.append(dict(tag='input',name='q'+str(i)+'_text',placeholder=dict(tag='plain_text',content='Custom answer (takes priority over choices)'),
            label=dict(tag='plain_text',content=q['header'] or 'Question '+str(i+1)),label_position='top'))
    if not any(q['isSecret'] for q in questions):
        form.append(dict(tag='button',text=dict(tag='plain_text',content='Submit answers'),type='primary',action_type='form_submit',name='submit',value=dict(intern_agent_question=row['code'])))
        elements.append(dict(tag='form',name='answers',elements=form))
    elements.append(dict(tag='note',elements=[dict(tag='plain_text',content='/answer '+row['code']+' YOUR ANSWER · /questions · /stop')]))
    return dict(config=dict(wide_screen_mode=True),header=dict(title=dict(tag='plain_text',content='Codex needs your answer'),template='purple'),elements=elements)


def validate_answers(row,answers,partial=True):
    questions=json.loads(row['questions']);expected={q['id']:q for q in questions}
    if not isinstance(answers,dict) or set(answers)-set(expected):raise Invalid('Choose question IDs from this request')
    result={}
    for ident,value in answers.items():
        if isinstance(value,dict) and set(value)=={'answers'}:value=value['answers']
        value=[value] if isinstance(value,str) else value
        q=expected[ident]
        if q['isSecret']:raise Invalid('Secret answers require secure local input')
        if not isinstance(value,list) or not value or len(value)>30 or any(not isinstance(v,str) or not v.strip() or len(v.encode())>8000 or '\x00' in v for v in value):raise Invalid('Supply a bounded answer for every question')
        if not q['multiSelect'] and len(value)!=1:raise Invalid('Select one answer for this question')
        if len(value)!=len(set(value)):raise Invalid('Repeated answer options')
        if len(value)>1 and any(v not in {o['label'] for o in q['options']} for v in value):raise Invalid('Multiple selections must use offered options')
        result[ident]={'answers':value}
    if not partial and set(result)!=set(expected):raise Invalid('Answer every question before submitting')
    return result


def answer(state,session,code,value):
    row=state.db.execute('SELECT * FROM user_questions WHERE code=? AND session=?',(code,session['id'])).fetchone()
    if not row or row['state'] not in OPEN or row['expires_at']<time.time() or row['thread']!=session['thread_id']:raise Invalid('Question expired, resolved or belongs to another conversation')
    if row['state'] in {'answered','sending'}:return 'Answer already saved; delivery is pending.'
    if state.ledger.session_stopped(session['id']):raise Invalid('This conversation is stopping; the question is no longer actionable')
    prior=json.loads(row['answers']);questions=json.loads(row['questions'])
    if isinstance(value,str):
        try:parsed=json.loads(value)
        except (ValueError,TypeError):parsed=None
        if isinstance(parsed,dict):value=parsed
        else:
            q=next((q for q in questions if q['id'] not in prior),None)
            if not q:raise Invalid('All questions already have answers')
            text=value
            if text.isdecimal() and q['options'] and 1<=int(text)<=len(q['options']):text=q['options'][int(text)-1]['label']
            value={q['id']:text}
    supplied=validate_answers(row,value);prior.update(supplied)
    complete=len(prior)==len(questions)
    state.db.execute("UPDATE user_questions SET answers=?,state=?,answered_at=COALESCE(answered_at,?) WHERE code=? AND state IN ('new','pending','delivery_failed')",(json.dumps(prior,ensure_ascii=False),'answered' if complete else 'pending',time.time() if complete else None,code))
    return 'Answer saved for Codex.' if complete else 'Answer saved. Next: '+next(q['question'] for q in questions if q['id'] not in prior)+'\n/answer '+code+' YOUR ANSWER'


def callback(relay,payload):
    state=relay.state;header=payload.get('header',{});event=payload.get('event',{})
    if header.get('app_id')!=state.identity()['app_id'] or header.get('event_type')!='card.action.trigger':raise Invalid('Unexpected card application or callback')
    if (event.get('operator') or {}).get('open_id')!=state.identity()['owner']:raise Invalid('Only the owner can answer this card')
    action=event.get('action') or {};code=(action.get('value') or {}).get('intern_agent_question')
    if not isinstance(code,str) or not re.fullmatch('[0-9a-f]{16}',code):raise Invalid('Unknown question card')
    row=state.db.execute('SELECT * FROM user_questions WHERE code=?',(code,)).fetchone()
    if not row:raise Invalid('Unknown question card')
    session=state.session(row['session']);context=event.get('context') or {}
    if (not session or context.get('open_chat_id')!=session['chat_id'] or not row['receipt'] or context.get('open_message_id')!=row['receipt']):raise Invalid('Card belongs to another conversation or message')
    values=action.get('form_value') or {};answers={}
    for i,q in enumerate(json.loads(row['questions'])):
        custom=values.get('q'+str(i)+'_text');selected=values.get('q'+str(i)+'_select')
        if isinstance(custom,str) and custom.strip():answers[q['id']]=custom
        elif selected is not None:
            selected=[selected] if isinstance(selected,str) else selected
            if not isinstance(selected,list) or any(not isinstance(v,str) or not v.isdecimal() or int(v)>=len(q['options']) for v in selected):raise Invalid('Invalid card selection')
            answers[q['id']]=[q['options'][int(v)]['label'] for v in selected]
    validate_answers(row,answers,partial=False)
    key=header.get('event_id') or hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    state.db.execute('BEGIN IMMEDIATE')
    try:
        if state.db.execute('SELECT 1 FROM question_callbacks WHERE key=?',(key,)).fetchone():state.db.execute('COMMIT');return 'Answer already received.'
        result=answer(state,session,code,answers)
        state.db.execute('INSERT INTO question_callbacks VALUES(?,?,?)',(key,code,time.time()));state.db.execute('COMMIT');return result
    except BaseException:state.db.execute('ROLLBACK');raise


def resolved(state,session,request):
    state.db.execute("UPDATE user_questions SET state='resolved' WHERE session=? AND (request=? OR item=?) AND source!='native_async' AND state NOT IN ('submitted','cancelled','resolved')",(session['id'],json.dumps(request),str(request)))


def cancel(state,session):
    return state.db.execute("UPDATE user_questions SET state='cancelled' WHERE session=? AND state IN ('new','pending','answered','sending','delivery_failed','uncertain')",(session['id'],)).rowcount


def describe(state,session):
    from .workspace_tracking import WorkspaceTracking
    rows=[dict(r) for r in state.db.execute("SELECT * FROM user_questions WHERE session=? ORDER BY created_at DESC LIMIT 10",(session['id'],))] if session else []
    text='\n\n'.join(r['code']+' | '+r['state']+' | card '+r['delivery_state']+(' | '+r['error_type'] if r['error_type'] else '')+'\n'+'\n'.join(str(i+1)+'. '+q['question'] for i,q in enumerate(json.loads(r['questions']))) for r in rows) or 'No Codex questions in this conversation.'
    return WorkspaceTracking(state).clean(text)


def validate_decision(data):
    from .records import ID
    if (not isinstance(data,dict) or set(data)!={'conversation','task','questions','at'} or not isinstance(data['conversation'],str) or not ID.fullmatch(data['conversation'])
            or data['task'] is not None and (not isinstance(data['task'],str) or not ID.fullmatch(data['task']))
            or not isinstance(data['questions'],list) or not 1<=len(data['questions'])<=10
            or type(data['at']) not in {int,float} or not 0<=data['at']<10**12):raise Invalid('Invalid recorded owner decision')
    for q in data['questions']:
        if not isinstance(q,dict) or set(q)!={'question','answers'} or not isinstance(q['question'],str) or not 1<=len(q['question'])<=4000 or not isinstance(q['answers'],list) or not 1<=len(q['answers'])<=30 or any(not isinstance(a,str) or not a or len(a.encode())>8000 for a in q['answers']):raise Invalid('Invalid recorded question answer')


def save_decision(relay,row):
    session=relay.state.session(row['session']);answers=json.loads(row['answers']);qs=json.loads(row['questions'])
    if row['source']=='demo' or not answers or any(q['isSecret'] for q in qs):return
    key='decision_'+hashlib.sha256((row['session']+row['code']).encode()).hexdigest()[:16]
    # Line-based credential redaction must operate on text fields, never on
    # serialized JSON whose enclosing structure could be removed with a field.
    clean=relay.tracker.clean
    data=dict(conversation=session['id'],task=row['task_id'],questions=[dict(question=clean(q['question']),
        answers=[clean(answer) for answer in answers[q['id']]['answers']]) for q in qs],at=row['answered_at'] or row['created_at'])
    validate_decision(data);body=json.dumps(data,sort_keys=True)
    state=relay.state;state.db.execute('BEGIN IMMEDIATE')
    try:
        if state.db.execute('UPDATE user_questions SET decision_saved=1 WHERE code=? AND decision_saved=0',(row['code'],)).rowcount:
            revision=state.ledger.revision(session['workspace'],'memory',key,'memory',conversation=session['id'],task=row['task_id'])
            state.db.execute("INSERT OR IGNORE INTO feature_documents VALUES(?,'question_decision',?,?,?)",(session['workspace'],key,body,revision))
        state.db.execute('COMMIT')
    except BaseException:state.db.execute('ROLLBACK');raise
    relay.tracker.queue_record(session,phase='Ready')


def demo(state,session):
    request='demo-'+secrets.token_hex(6)
    row=register(state,session,dict(threadId=session['thread_id'],turnId='question-card-demo',itemId=request,isBlocking=False,
        questions=[dict(id='test',header='Card test',question='Question card delivery test: select an option or enter your own text. No Codex work will run.',
        options=[dict(label='The choices work',description='Verify the published callback'),dict(label='Use my text',description='Type an answer in the custom field')])]),request,source='demo')
    return 'Question test card queued. '+row['code']


def ask(state,session,questions,key=None,timeout=6*3600):
    """Fallback for native/older clients: a real tool waits for correlated input."""
    body=normalize(questions)
    if any(q['isSecret'] for q in body):raise Invalid('Use secure local input for secret questions')
    event=state.db.execute("SELECT seq FROM events WHERE session=? AND role='agent' AND state IN ('running','awaiting_terminal') ORDER BY seq DESC LIMIT 1",(session['id'],)).fetchone()
    if not event:
        from .goals import owner_turn
        event=owner_turn(state,session)
    seq=event['seq'];turn=''
    for table in ['terminal_attachments','console_views']:
        view=state.db.execute('SELECT snapshot FROM '+table+' WHERE session=?',(session['id'],)).fetchone()
        if view:
            snap=json.loads(view[0]);turn=snap.get('active') or turn
    if not turn:
        observed=state.db.execute('SELECT turn_id FROM codex_outcomes WHERE session=? AND status=\'inProgress\'',(session['id'],)).fetchone();turn=observed[0] if observed else 'event-'+str(seq)
    request='relay:'+str(seq)+':'+hashlib.sha256((key or json.dumps(body,sort_keys=True)).encode()).hexdigest()
    row=register(state,session,dict(threadId=session['thread_id'],turnId=turn,itemId=request,questions=body,isBlocking=True),request,source='relay')
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        current=state.db.execute('SELECT * FROM user_questions WHERE code=?',(row['code'],)).fetchone()
        if not current or session['thread_id']!=state.session(session['id'])['thread_id']:raise Invalid('The question conversation changed')
        if current['state']=='answered':
            result=validate_answers(current,json.loads(current['answers']),partial=False)
            if state.db.execute("UPDATE user_questions SET state='submitted' WHERE code=? AND state='answered'",(row['code'],)).rowcount:return {'answers':result}
        if current['state'] in {'cancelled','expired','resolved','uncertain'}:raise Invalid('The question is no longer awaiting an answer; inspect /questions')
        if current['state']=='submitted':return {'answers':validate_answers(current,json.loads(current['answers']),partial=False)}
        time.sleep(.25)
    state.db.execute("UPDATE user_questions SET state='expired' WHERE code=? AND state IN ('new','pending')",(row['code'],))
    raise Invalid('Question expired; no default answer was selected')


def close_card(relay,row):
    if not row['receipt'] or not hasattr(relay.api,'update_question'):return
    try:
        current=relay.state.db.execute('SELECT state FROM user_questions WHERE code=?',(row['code'],)).fetchone()[0]
        text=('Your answer was submitted to the exact Codex request.' if row['source']!='demo' else 'Test answer received. No Codex work was started.') if current=='submitted' else 'This question is '+current+'. No automatic answer was selected.'
        if current=='submitted' and row['source']=='native_async':text='Your answer was queued in its original Codex conversation and can steer existing work.'
        relay.api.update_question(row['receipt'],dict(config=dict(wide_screen_mode=True),header=dict(title=dict(tag='plain_text',content='Answer received' if current=='submitted' else 'Question '+current),template='green' if current=='submitted' else 'grey'),
            elements=[dict(tag='div',text=dict(tag='plain_text',content=text))]))
        relay.state.db.execute('UPDATE user_questions SET ui_closed=1 WHERE code=?',(row['code'],))
    except Exception:relay.state.db.execute('UPDATE user_questions SET ui_closed=-1 WHERE code=?',(row['code'],))


def deliver_async(state,session,row):
    """An asynchronous native tool returns immediately; reply as an owner follow-up."""
    if session['state']!='ready' or session['thread_id']!=row['thread'] or state.ledger.session_stopped(session['id']):raise Invalid('The original question conversation is unavailable')
    answers=validate_answers(row,json.loads(row['answers']),partial=False)
    body='Owner answers to the asynchronous questions in this conversation:\n\n'+'\n\n'.join(q['question']+'\nAnswer: '+'; '.join(answers[q['id']]['answers']) for q in json.loads(row['questions']))
    if len(body.encode())>32768:raise Invalid('Use shorter answers for the asynchronous follow-up')
    identity=state.identity();state.db.execute('BEGIN IMMEDIATE')
    try:
        if state.db.execute("UPDATE user_questions SET state='submitted' WHERE code=? AND state='answered'",(row['code'],)).rowcount:
            state.db.execute("INSERT OR IGNORE INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state) VALUES(?,?,?,?,'agent',?,?,?,'queued')",
                (identity['app_id'],'om_question_'+row['code'],session['chat_id'],identity['owner'],session['workspace'],session['id'],body))
        state.db.execute('COMMIT')
    except BaseException:state.db.execute('ROLLBACK');raise


def pump(relay):
    state=relay.state
    state.db.execute("UPDATE user_questions SET state='expired' WHERE expires_at<? AND state IN ('new','pending','answered','delivery_failed')",(time.time(),))
    for raw in state.db.execute("SELECT * FROM user_questions WHERE state IN ('new','pending') AND delivery_state='queued' AND retry_at<=? ORDER BY created_at LIMIT 10",(time.time(),)).fetchall():
        row=dict(raw);session=state.session(row['session'])
        if not session or session['thread_id']!=row['thread']:state.db.execute("UPDATE user_questions SET state='cancelled' WHERE code=?",(row['code'],));continue
        if not state.db.execute("UPDATE user_questions SET state='pending',delivery_state='sending',delivery_attempts=delivery_attempts+1 WHERE code=? AND delivery_state='queued'",(row['code'],)).rowcount:continue
        try:
            content=json.loads(relay.tracker.clean(json.dumps(card(row),ensure_ascii=False)))
            receipt=relay.api.send_question(session['chat_id'],content,'question:'+row['code'])
            state.db.execute("UPDATE user_questions SET receipt=?,error_type=NULL,delivery_state='delivered' WHERE code=?",(receipt,row['code']))
        except Exception as error:
            text='Codex needs your answer:\n'+'\n'.join(q['question']+'\n'+'\n'.join(str(i+1)+'. '+o['label']+' — '+o['description'] for i,o in enumerate(q['options'])) for q in json.loads(row['questions']))+'\n/answer '+row['code']+' YOUR ANSWER'
            try:
                receipt=relay.api.send(session['chat_id'],relay.tracker.clean(text),'question-fallback:'+row['code'])
                state.db.execute("UPDATE user_questions SET receipt=?,error_type=?,delivery_state='text_fallback' WHERE code=?",(receipt,type(error).__name__,row['code']))
            except Exception as fallback:
                known=type(getattr(fallback,'code',None)) is int and type(getattr(error,'code',None)) is int
                attempts=row['delivery_attempts']+1;phase='queued' if known and attempts<4 else 'failed' if known else 'uncertain'
                state.db.execute('UPDATE user_questions SET delivery_state=?,error_type=?,retry_at=? WHERE code=?',(phase,type(fallback).__name__,time.time()+[10,30,120,300][min(attempts-1,3)],row['code']))
    for raw in state.db.execute("SELECT * FROM user_questions WHERE state='answered' ORDER BY created_at LIMIT 10").fetchall():
        row=dict(raw);session=state.session(row['session'])
        if not session or session['thread_id']!=row['thread']:state.db.execute("UPDATE user_questions SET state='cancelled' WHERE code=?",(row['code'],));continue
        if row['source']=='demo':
            state.db.execute("UPDATE user_questions SET state='submitted' WHERE code=?",(row['code'],));close_card(relay,row);continue
        if row['source']=='relay':continue  # The exact waiting tool consumes this answer.
        if row['source']=='native_async':
            try:deliver_async(state,session,row)
            except Exception as error:state.db.execute("UPDATE user_questions SET state='delivery_failed',error_type=? WHERE code=? AND state='answered'",(type(error).__name__,row['code']))
            else:close_card(relay,row)
            continue
        if row['source']=='rpc' and row['code'] in relay.host.approvals:
            try:relay.host.decide(row['code'],'answer',session['id'],json.loads(row['answers']))
            except Exception as error:
                state.db.execute("UPDATE user_questions SET state='uncertain',error_type=? WHERE code=?",(type(error).__name__,row['code']))
            else:close_card(relay,row)
        else:
            from .question_terminal import submit
            bridge=relay.terminal if relay.terminal.binding(session) else relay.host.observer
            if not state.db.execute("UPDATE user_questions SET state='sending' WHERE code=? AND state='answered'",(row['code'],)).rowcount:continue
            try:submit(state,bridge,session,row)
            except Exception as error:state.db.execute("UPDATE user_questions SET state='delivery_failed',error_type=? WHERE code=? AND state='sending'",(type(error).__name__,row['code']));continue
            if state.db.execute("UPDATE user_questions SET state='submitted',error_type=NULL WHERE code=? AND state IN ('sending','resolved')",(row['code'],)).rowcount:close_card(relay,row)
    for raw in state.db.execute("SELECT * FROM user_questions WHERE state IN ('submitted','resolved','expired','cancelled') AND receipt IS NOT NULL AND ui_closed=0 ORDER BY created_at LIMIT 10").fetchall():close_card(relay,dict(raw))
    for raw in state.db.execute("SELECT * FROM user_questions WHERE state='submitted' AND source!='demo' AND decision_saved=0 ORDER BY created_at LIMIT 10").fetchall():save_decision(relay,dict(raw))


def loop(relay):
    try:
        while not relay.stop.is_set():
            try:pump(relay);relay.question_error_type=None
            except Exception as error:relay.question_error_type=type(error).__name__
            if relay.stop.wait(.5):break
    finally:relay.state.close()
