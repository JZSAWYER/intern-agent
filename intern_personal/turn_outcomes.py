"""Public notices for observed failures; private backend errors stay private."""


def failed_notice(error=None):
    message = error.get('message', '') if isinstance(error, dict) else error
    network = isinstance(message, str) and any(word in message.lower() for word in
        ('connection', 'network', 'stream disconnected', 'timed out', 'timeout'))
    cause = 'a connection error' if network else 'an error'
    return ('Codex reported '+cause+' before producing a final answer. '
            'This turn failed; the session and saved files are preserved. '
            'The request was not replayed. Use /recovery status to check automatic continuation, or send a new message to continue manually.')
