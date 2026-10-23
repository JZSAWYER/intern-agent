"""Useful record views without rewriting the source history or judging its truth."""
from .records import quote

MAX_INDEX_TURNS = 100

ARTIFACT_POLICY = (
    'Keep workspace artifacts reusable: save decisions, findings (including useful negative results), '
    'methods, evidence paths, verification and unresolved limits when they help future work. '
    'Update an existing relevant guide or result instead of creating duplicate notes. '
    'Do not create placeholder reports, copied tool logs, repeated progress/retry notices or a new '
    'summary file merely to mark a turn. Keep transient diagnostics in private runtime storage. '
    'The relay preserves each request and final reply; generated .intern_workspace files are not '
    'agent-authored notes. Knowledge indexes point to source turns, not independently verified facts. '
    'Use /memory add|error|rule TEXT or /task note TEXT for explicit durable owner knowledge; '
    'never infer permission from old notes. Read '
)


def distinct_notes(notes):
    """Keep the first exact note per kind/scope; never discard different evidence."""
    seen = set()
    for note in notes:
        key = (note.get('workspace'), note.get('task_id'), note['kind'], note['body'].strip())
        if key not in seen:
            seen.add(key)
            yield note


def turn_index(turns, path, clean):
    """Findable work results, with provenance and outcome rather than copied replies."""
    work = [turn for turn in turns if turn['role'] == 'agent' and turn['result']]
    lines = []
    if len(work) > MAX_INDEX_TURNS:
        lines.append('Showing the latest '+str(MAX_INDEX_TURNS)+' of '+str(len(work))+
                     ' work results. Older source records remain in [history](history_log.md).\n')
    for turn in work[-MAX_INDEX_TURNS:]:
        label = ('turn_' + str(turn['seq']) + ' | ' + turn['phase'] + ' / '
                 + turn['activity'] + ' | Git: ' + turn['sync'])
        if turn['task_id']:
            label += ' | task: ' + turn['task_id']
        lines.append('- [' + label + '](' + path(turn) + '#response)')
        # Redact complete values before shortening; a cut credential cannot be
        # recognized by the later whole-document sanitizer.
        request = ' '.join(clean(turn['text']).split())
        if len(request) > 160:
            request = request[:157] + '…'
        lines.append('  ' + quote(request))
    return '\n'.join(lines) or '(none)'
