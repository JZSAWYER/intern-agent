"""Task briefs and attributed verification reports for bounded team handoffs."""
import json
import time
from .core import Invalid
from .feature_state import get,save


def document(state,task):
    return get(state,task['workspace'],'task_brief',task['id'],{'fields':{},'reports':[]})


def command(tracker,session,action,argument):
    task=tracker.state.ledger.selection(session['id'])
    if not task:raise Invalid('Select a named task first')
    data=document(tracker.state,task)
    if action=='brief':
        if not argument:return json.dumps(dict(task=task['name'],criteria=task['criteria'],**data),ensure_ascii=False)
        parts=argument.split(maxsplit=1)
        if len(parts)!=2 or parts[0] not in {'background','goal','scope','verification','risks'}:raise Invalid('Use /task brief background|goal|scope|verification|risks TEXT')
        data['fields'][parts[0]]=tracker.clean(parts[1])
    elif action=='report':
        if not argument:return json.dumps(data['reports'],ensure_ascii=False)
        fields=[tracker.clean(p.strip()) for p in argument.split('|',2)]
        if len(fields)!=3 or any(not f for f in fields):raise Invalid('Use /task report COMMAND | RESULT | LIMITATIONS')
        data['reports'].append(dict(zip(['command','result','limits'],fields),conversation=session['id'],at=time.time()))
    else:raise Invalid('Unknown task document command')
    saved=save(tracker,session,'task_brief',task['id'],data)
    return 'Task document saved.'+('' if saved else ' GitHub upload pending.')


def ready(state,task):
    return bool(task.get('criteria','').strip() and document(state,task)['fields'].get('verification','').strip())


def context(state,task):
    return json.dumps(document(state,task),ensure_ascii=False) if task else ''
