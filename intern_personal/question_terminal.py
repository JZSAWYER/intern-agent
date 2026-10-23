"""Verified answer delivery to an already-bound Codex question menu."""
import json
import re
import time

from .core import Invalid
from .terminal_bridge import tmux,verify_pane,prompt_is_empty

ANSI=re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
CUSTOM={'None of the above','Other','Something else','以上都不是','其他','其它'}


def flat(text):return ''.join(ANSI.sub('',text).split())


def selected(text,labels):
    for line in ANSI.sub('',text).splitlines():
        match=re.match(r'^\s*[›❯>]\s*(?:\d+[.)]\s*)?(.*)',line)
        if match:
            value=re.sub(r'^\[[ xX]\]\s*','',match[1]).strip()
            for label in sorted(labels,key=len,reverse=True):
                if value==label or value.startswith(label+' '):return label
    return None


def checked(text,labels):
    result=set()
    for line in ANSI.sub('',text).splitlines():
        match=re.match(r'^\s*(?:[›❯>*-]\s*)?(?:\d+[.)]\s*)?\[([ xX])\]\s*(.*)',line)
        if match and match[1].lower()=='x':
            for label in labels:
                if match[2].strip()==label or match[2].startswith(label+' '):result.add(label)
    return result


def submit(state,bridge,session,row):
    binding=bridge.binding(session)
    if not binding:raise Invalid('The original question console is unavailable')
    config=bridge.config_path(binding) if hasattr(bridge,'config_path') else bridge.config(session)
    # Console view configs intentionally contain no tmux identity: adopt only
    # the existing registered, same-thread host view, never a guessed pane.
    if 'pane' not in config:
        host=state.db.execute('SELECT * FROM session_hosts WHERE session=?',(session['id'],)).fetchone()
        if not host or host['view_thread']!=session['thread_id']:raise Invalid('The exact question console is unavailable')
        from .terminal_bridge import tmux as run
        from types import SimpleNamespace
        base=dict(tmux_socket=host['tmux_socket'],pane=host['tmux_name']+':Codex')
        # A managed view is pinned to the saved host + view thread; verify its
        # single live Codex pane on every read before sending a key.
        def verify():
            value=run(base,'display-message','-p','-t',base['pane'],'#{pane_current_command}|#{pane_dead}').strip().split('|')
            if len(value)!=2 or value[1]!='0' or 'codex' not in value[0].lower():raise Invalid('Question console identity changed')
        config=base
    else:
        def verify():verify_pane(config)
    snapshot=json.loads(binding['snapshot'])
    if snapshot.get('active')!=row['turn'] or session['thread_id']!=row['thread']:raise Invalid('This question turn ended or changed')
    from .user_questions import validate_answers
    questions=json.loads(row['questions']);answers=validate_answers(row,json.loads(row['answers']),partial=False)
    sent=False
    def actionable():
        current=state.db.execute('SELECT state FROM user_questions WHERE code=?',(row['code'],)).fetchone()
        if not current or current[0]!='sending' or state.ledger.session_stopped(session['id']) or state.session(session['id'])['thread_id']!=row['thread']:
            raise Invalid('Question ended, changed or was stopped; no more keys are sent')
    def capture():
        actionable();verify();return tmux(config,'capture-pane','-p','-t',config['pane'],'-S','-100')
    def key(value):
        nonlocal sent
        actionable();verify();sent=True;tmux(config,'send-keys','-t',config['pane'],value);time.sleep(.12)
    try:
        for q in questions:
            labels=[o['label'] for o in q['options']];values=answers[q['id']]['answers']
            pane=capture()
            if flat(q['question']) not in flat(pane):raise Invalid('The expected question is not visible; answer was preserved')
            if labels and prompt_is_empty(pane):raise Invalid('The console is at an ordinary prompt, not this question')
            if q['multiSelect'] and all(v in labels for v in values):
                for label in labels:
                    if (label in checked(pane,labels))==(label in values):continue
                    for _ in range(len(labels)+3):
                        if flat(q['question']) not in flat(pane):raise Invalid('Question changed while selecting an answer')
                        if selected(pane,labels)==label:break
                        if selected(pane,labels) is None:raise Invalid('Unsupported checkbox menu')
                        key('Down');pane=capture()
                    else:raise Invalid('Checkbox selection could not be verified')
                    key('Space');pane=capture()
                    if (label in checked(pane,labels))!=(label in values):raise Invalid('Checkbox state did not match the answer')
                if checked(pane,labels)!=set(values):raise Invalid('The checked choices differ from the submitted answer')
                key('Enter');continue
            target=values[0] if len(values)==1 and values[0] in labels else None
            custom=target is None
            allowed=labels+sorted(CUSTOM)
            if labels:
                for _ in range(len(labels)+3):
                    if flat(q['question']) not in flat(pane):raise Invalid('Question changed while selecting an answer')
                    chosen=selected(pane,allowed)
                    if chosen==target or custom and chosen in CUSTOM:break
                    if chosen is None:raise Invalid('Unsupported Codex question menu; answer was preserved')
                    key('Down');pane=capture()
                else:raise Invalid('Answer option could not be verified')
                if custom:key('Tab')
            if custom:
                text='; '.join(values)
                # Paste only literal data. Never shell interpolation, newlines
                # that submit input, Ctrl-U, or a replacement console.
                if any(c in text for c in ['\n','\r','\x1b']):raise Invalid('Use the original terminal for this multiline answer')
                actionable();verify();sent=True;tmux(config,'send-keys','-t',config['pane'],'-l','--',text);time.sleep(.12)
                if flat(text) not in flat(capture()):raise Invalid('Custom answer text could not be verified')
            key('Enter')
    except Exception:
        if sent:state.db.execute("UPDATE user_questions SET state='uncertain',error_type='TerminalAnswerNeedsReview' WHERE code=? AND state NOT IN ('cancelled','resolved','submitted')",(row['code'],))
        raise
