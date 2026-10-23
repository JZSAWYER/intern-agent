"""Opt-in Feishu receiver, management commands and bounded live workers."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from .core import Invalid, WORKSPACE, valid_owner
from .live_state import LiveState, write_private, private_json, identifier
from .codex_bridge import CodexHost, BridgeError, TurnFailed, TurnBusy
from .workspace_tracking import WorkspaceTracking, TrackingError
from .terminal_bridge import TerminalBridge


TYPING_FINISHED = ('complete', 'cancelled', 'cancelled_policy_changed')


HELP = ("Intern Agent tutorial\n\n"
        "Control group\nOrdinary text goes unchanged to its bound controller Codex conversation. Slash commands remain available.\n"
        "With a controller bound, /model, /task, /stop and other conversation commands default to that controller. Specify SESSION to manage another bot.\n\n"
        "Getting started\n1. Register the project's GitHub repository locally, then in management send /new project (use its registered name). Each new bot gets one permanent branch for code and Markdown history.\n"
        "2. Open the workspace link in the bot's reply.\n"
        "3. Send ordinary text there, such as: Explain this project's structure. Codex replies in that same chat.\n"
        "4. Continue in that chat to keep the conversation history.\n\n"
        "Model commands in a workspace chat\n/model — current model, reasoning effort and model list\n"
        "/models — model list\n/model MODEL [EFFORT] — use a listed model for this conversation\n"
        "/model default — restore this conversation's configured defaults\n"
        "/reasoning [EFFORT] — inspect or change thinking effort\n/help model — model tutorial\n"
        "/fast — toggle Fast mode; /fast on|off|status — set or inspect it\n"
        "Model changes apply to the next turn; an already running turn continues with its original settings.\n\n"
        "During work\n/status — session status, blocked/running turns and recovery instructions (management also shows the full queue)\n"
        "/research start 10h \"Inspect the existing experiment\" — persistent research wake-up; /research status and /help research\n"
        "/console — SSH tmux attach command for this conversation; /consoles in management lists all\n"
        "/task start NAME — select a new named task; subsequent turns keep its ID\n"
        "/task use ID /task rename NAME /task pause /task finish [ACCEPTANCE_NOTE] /task cancel /task block\n"
        "/task criteria TEXT /task note TEXT — acceptance criteria and task knowledge\n"
        "New bots keep named tasks on their permanent branch. Existing bots retain their earlier task-branch layout. Finish accepts a verified, uploaded delivery; it does not create a PR or merge.\n"
        "/memory show /memory add TEXT /memory error TEXT /memory rule TEXT — persistent workspace knowledge\n"
        "/task list /task status — inspect tasks\n/chat — pause the selected task and return to idle conversation\n"
        "/cancel or /stop — interrupt this workspace's active turn\n"
        "/pause /resume — pause or resume this workspace\n"
        "/recovery status|on|off|inbox — inspect or control bounded automatic failure recovery\n"
        "/recovery retry INCIDENT — after repair, rearm one bounded cycle in the same conversation\n"
        "/mail list|ask TARGET QUESTION|note TARGET TEXT|status [ID]|read ID|ack ID|cancel ID — durable bot communication\n"
        "/help mail — queue, delivery and reply rules; /mail on|off — owner control\n"
        "/approve CODE /deny CODE — answer one approval request\n/answer CODE TEXT — answer one question\n\n"
        "Session tools (isolated app-server conversations)\n/compact — compact Codex context without deleting Git history\n"
        "/clear or /restart — start fresh Codex context; Git history and the selected task stay saved\n"
        "/archive /unarchive — retire or restore a conversation without deleting its group or branches\n"
        "/usage or /cost — observed token counts and account limits; no inferred price\n"
        "/goal OBJECTIVE /goal set [TOKENS] OBJECTIVE — pursue a persistent goal\n/goal pause|resume|clear /goal auto on|off — goal controls\n/questions /answer CODE TEXT — choices and custom answers; /help questions\n"
        "/progress summary|messages|off — one editable progress message, separate messages, or off; public prose only\n"
        "/queue TEXT — save a separate later task; ordinary owner text steers a running turn\n"
        "/trigger all|mentions — owner selects when group text starts Codex; controls always work\n"
        "/skills list|add KEY GITHUB_URL COMMIT DIRECTORY|inspect KEY|enable KEY|disable KEY|refresh KEY — owner-managed pinned reference skills\n"
        "/task brief background|goal|scope|verification|risks TEXT — task handoff details\n"
        "/task report COMMAND | RESULT | LIMITATIONS — attributed verification evidence\n/doctor — read-only setup checks\n"
        "Upload files first (PDF, Word, text or other ordinary files; 20 MiB each), then send your message. All waiting files and your message reach Codex together. Ask Codex to send a generated PDF/ZIP back here.\n"
        "/send-file [--committed] RELATIVE_PATH — send a workspace file\n/files [TRANSFER_ID] /files retry TRANSFER_ID — delivery and recovery\n/files pending /files clear — inspect or discard uploads waiting for your message\n/files permissions — verify live upload/download/send permissions\n/help files — file-transfer tutorial\n\n"
        "Management commands\n/list — workspace links and session IDs\n/new NAME — open or reuse a workspace chat\n"
        "/init WORKSPACE [REPOSITORY] — review creation of a new private GitHub project\n"
        "/init apply PLAN_ID — create the reviewed repository, empty initial main, bot branch and chat\n"
        "/init status [PLAN_ID] — initialization progress or blocker; never repeats a creation blindly\n"
        "/new-session NAME — separate conversation in a new chat for that workspace\n"
        "/reconnect SESSION [controller] — reconnect a Git-restored bot or Control conversation\n"
        "/model SESSION [MODEL [EFFORT]] — inspect/change a selected session\n"
        "/reasoning SESSION [EFFORT] — inspect/change its thinking effort\n"
        "/fast SESSION [on|off|status] — control Fast mode for another conversation\n"
        "/cancel SESSION /pause SESSION /resume SESSION — control a selected session\n"
        "/stop SESSION — immediately request interruption of a selected session\n"
        "/task SESSION ACTION [NAME_OR_ID] — manage its named tasks\n"
        "/resolve TURN cancel|deliver|retry — cancel uncertain work, deliver a saved uncertain reply, or retry repaired recording\n\n"
        "Owner team controls (management only)\n/team create \"NAME\" WORKSPACE LEAD_SESSION [1-3]\n"
        "/team add TEAM WORKER_SESSION\n/team assign TEAM WORKER_SESSION TASK_ID\n"
        "/team run TEAM WORKER_SESSION PROMPT — explicitly queue assigned work\n"
        "/team mail TEAM FROM_SESSION TO_SESSION TEXT /team read TEAM MAIL_ID\n"
        "/team accept TEAM TASK_ID NOTE /team changes TEAM TASK_ID NOTE\n"
        "/team archive TEAM /team list /team status\n"
        "Create worker conversations with /new-session; create/pause their named tasks before assignment. Workers use isolated checkouts and branches; the attached Debug Bot stays independent.\n"
        "/access grant OPEN_ID WORKSPACE operator|viewer /access revoke OPEN_ID WORKSPACE — local workspace access; Feishu visibility and membership remain separate owner actions.\n\n"
        "Send /help anytime. Commands use plain text; no terminal menu is needed. "
        "Only the commands above are implemented; other Codex terminal slash commands are not forwarded. "
        "A slash-prefixed file path should be written inside an ordinary sentence.")

MODEL_HELP = ("Model tutorial\n\nIn your workspace chat:\n"
              "1. Send /model to see the current selection and available model names.\n"
              "2. Copy a name from that list: /model MODEL\n"
              "3. Optionally include one of its listed efforts: /model MODEL EFFORT\n"
              "4. Change only effort with /reasoning EFFORT.\n"
              "5. Send /model default to restore the configured defaults.\n\n"
              "Selections are saved per conversation and survive relay restarts. Other chats keep their own selection. "
              "A running turn is unchanged; the new selection applies to the next turn. "
              "The model list is Codex's catalog; a completed reply confirms actual access.\n\n"
              "In the management group, use /list to find a session, then "
              "/model SESSION MODEL [EFFORT] or /reasoning SESSION EFFORT.")


class FeishuError(RuntimeError):
    def __init__(self, operation, code):
        self.operation, self.code = operation, code
        super().__init__("Feishu " + operation + " failed (code " + str(code) + ")")


def chat_link(chat_id):
    if not identifier(chat_id, "oc"):
        raise Invalid("Invalid chat identifier")
    # OpenAPI oc_ identifiers use openChatId; chatId expects an internal client ID.
    return "https://applink.feishu.cn/client/chat/open?openChatId=" + chat_id


def text_chunks(text, limit=8000):
    chunk, size = [], 0
    for char in text:
        length = len(char.encode())
        if size + length > limit:
            yield "".join(chunk)
            chunk, size = [], 0
        chunk.append(char)
        size += length
    if chunk:
        yield "".join(chunk)


class FeishuAPI:
    def __init__(self, credentials):
        import lark_oapi as lark
        self.lark = lark
        self.app_id = credentials["app_id"]
        self._redact = credentials["app_secret"]
        self.client = (lark.Client.builder().app_id(self.app_id).app_secret(self._redact)
                       .domain(lark.FEISHU_DOMAIN).timeout(30).log_level(lark.LogLevel.ERROR).build())
        self.question_client=(lark.Client.builder().app_id(self.app_id).app_secret(self._redact)
                              .domain(lark.FEISHU_DOMAIN).timeout(10).log_level(lark.LogLevel.ERROR).build())
        self.question_lock=threading.Lock()
        # SDK logs may contain signed connection URLs. Expose only our fixed status labels.
        lark.logger.disabled = True
        self.lock = threading.Lock()
        self.last_send = 0.0

    def create_chat(self, session, owner):
        from lark_oapi.api.im.v1 import CreateChatRequest, CreateChatRequestBody
        name = session.get('display_name') or ("Intern Agent · " + session["workspace"] + " · " + session["id"][-6:])[:80]
        body = (CreateChatRequestBody.builder().name(name).description("A dedicated Intern Agent workspace conversation")
                .owner_id(owner).user_id_list([owner]).bot_id_list([self.app_id])
                .chat_mode("group").chat_type("private").build())
        request = (CreateChatRequest.builder().user_id_type("open_id").set_bot_manager(True)
                   .uuid(session["creation_key"]).request_body(body).build())
        with self.lock:
            response = self.client.im.v1.chat.create(request)
        if not response.success() or not response.data or not response.data.chat_id:
            raise FeishuError("create_chat", response.code)
        return response.data.chat_id

    def send(self, chat_id, text, key):
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
        if not isinstance(text, str) or not text.strip():
            raise Invalid("Cannot send an empty response")
        text = text.replace(self._redact, "[redacted]")
        receipts = []
        for index, chunk in enumerate(text_chunks(text)):
            identity = hashlib.sha256((key + ":" + str(index)).encode()).hexdigest()[:32]
            body = (CreateMessageRequestBody.builder().receive_id(chat_id).msg_type("text")
                    .content(json.dumps({"text": chunk}, ensure_ascii=False)).uuid(identity).build())
            request = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
            with self.lock:
                delay = 0.25 - (time.monotonic() - self.last_send)
                if delay > 0:
                    time.sleep(delay)
                response = self.client.im.v1.message.create(request)
                self.last_send = time.monotonic()
            if not response.success() or not response.data or not response.data.message_id:
                raise FeishuError("send_message", response.code)
            receipts.append(response.data.message_id)
        return ",".join(receipts)

    def add_typing(self, message_id):
        from lark_oapi.api.im.v1 import CreateMessageReactionRequest, CreateMessageReactionRequestBody, Emoji
        body = CreateMessageReactionRequestBody.builder().reaction_type(
            Emoji.builder().emoji_type("Typing").build()).build()
        request = CreateMessageReactionRequest.builder().message_id(message_id).request_body(body).build()
        with self.lock:
            response = self.client.im.v1.message_reaction.create(request)
        if not response.success() or not response.data or not response.data.reaction_id:
            raise FeishuError("add_typing", response.code)
        return response.data.reaction_id

    def progress(self,chat,text,key):
        return self.send(chat,text,key)

    def send_question(self,chat,card,key):
        from lark_oapi.api.im.v1 import CreateMessageRequest,CreateMessageRequestBody
        content=json.dumps(card,ensure_ascii=False).replace(self._redact,'[redacted]')
        body=CreateMessageRequestBody.builder().receive_id(chat).msg_type('interactive').content(content).uuid(hashlib.md5(key.encode()).hexdigest()).build()
        with self.question_lock:response=self.question_client.im.v1.message.create(CreateMessageRequest.builder().receive_id_type('chat_id').request_body(body).build())
        if not response.success() or not response.data or not response.data.message_id:raise FeishuError('send_question',response.code)
        return response.data.message_id

    def update_question(self,message,card):
        from lark_oapi.api.im.v1 import PatchMessageRequest,PatchMessageRequestBody
        body=PatchMessageRequestBody.builder().content(json.dumps(card,ensure_ascii=False).replace(self._redact,'[redacted]')).build()
        with self.question_lock:response=self.question_client.im.v1.message.patch(PatchMessageRequest.builder().message_id(message).request_body(body).build())
        if not response.success():raise FeishuError('update_question',response.code)

    def edit_progress(self,receipt,text):
        from lark_oapi.api.im.v1 import UpdateMessageRequest,UpdateMessageRequestBody
        if not identifier(receipt,'om') or len(text.encode())>4000:raise Invalid('Invalid progress edit')
        body=UpdateMessageRequestBody.builder().msg_type('text').content(json.dumps({'text':text.replace(self._redact,'[redacted]')},ensure_ascii=False)).build()
        request=UpdateMessageRequest.builder().message_id(receipt).request_body(body).build()
        with self.lock:response=self.client.im.v1.message.update(request)
        if not response.success():raise FeishuError('edit_progress',response.code)

    def bot_identity(self):
        request=(self.lark.BaseRequest.builder().http_method(self.lark.HttpMethod.GET).uri('/open-apis/bot/v3/info/')
                 .token_types({self.lark.AccessTokenType.TENANT}).build())
        with self.lock:response=self.client.request(request)
        if not response.success():raise FeishuError('bot_identity',response.code)
        value=json.loads(response.raw.content)
        return value.get('bot',{}).get('open_id')

    def download(self,message,key,kind):
        # SDK's resource method buffers the complete response. Stream instead,
        # using its existing tenant-token cache; never log bearer tokens or URLs.
        import requests
        from lark_oapi.core.token.manager import TokenManager
        from .attachments import MAX_BYTES
        if not identifier(message,'om') or not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}',key) or kind not in {'image','file'}:
            raise Invalid('Invalid message resource')
        with self.lock:token=TokenManager.get_self_tenant_token(self.client._config)
        url=self.lark.FEISHU_DOMAIN+'/open-apis/im/v1/messages/'+message+'/resources/'+key
        # No redirects: tenant credentials must never follow an arbitrary host.
        with requests.get(url,params={'type':kind},headers={'Authorization':'Bearer '+token},
                          stream=True,timeout=(10,30),allow_redirects=False) as response:
            if response.status_code!=200:
                try:code=json.loads(response.raw.read(65536)).get('code')
                except (ValueError,TypeError):code=None
                raise FeishuError('download_resource',code)
            try:size=int(response.headers.get('Content-Length','0'))
            except ValueError:raise Invalid('Invalid attachment size response') from None
            if size>MAX_BYTES:raise Invalid('Attachment exceeds 20 MiB')
            data=bytearray();deadline=time.monotonic()+90
            for chunk in response.iter_content(chunk_size=65536):
                data.extend(chunk)
                if len(data)>MAX_BYTES:raise Invalid('Attachment exceeds 20 MiB')
                if time.monotonic()>deadline:raise FeishuError('download_resource',None)
            return bytes(data)

    def file_permissions(self):
        from lark_oapi.api.application.v6 import ListScopeRequest
        with self.lock:
            response=self.client.application.v6.scope.list(ListScopeRequest.builder().build())
        if not response.success() or response.data is None or response.data.scopes is None:
            raise FeishuError('file_permissions',response.code)
        granted={s.scope_name for s in response.data.scopes
                 if type(s.grant_status) is int and s.grant_status==1
                 and getattr(s,'scope_type',None) in {None,'tenant'}}
        def check(*names):return dict(granted=bool(granted.intersection(names)),requires_any=list(names))
        return dict(schema='intern-agent.file-permissions.v1',verified=True,
                    upload=check('im:resource:upload','im:resource'),
                    download=check('im:message:readonly'),
                    send=check('im:message:send_as_bot'),round_trip_verified=False,
                    next_action='For missing permissions, grant application/tenant scopes in Intern Agent and publish the change with the same owner-only availability. Then retry the saved transfer; no Codex prompt needs to be rerun.')

    def upload_file(self,name,data):
        import io
        from lark_oapi.api.im.v1 import CreateFileRequest,CreateFileRequestBody
        from .attachments import MAX_BYTES,filename
        filename(name)
        if not isinstance(data,bytes) or not 0<len(data)<=MAX_BYTES:raise Invalid('Upload must contain 1 byte to 20 MiB')
        request=CreateFileRequest.builder().request_body(CreateFileRequestBody.builder().file_type('pdf' if Path(name).suffix.lower()=='.pdf' else 'stream').file_name(name).file(io.BytesIO(data)).build()).build()
        with self.lock:response=self.client.im.v1.file.create(request)
        if not response.success() or not response.data or not response.data.file_key:raise FeishuError('upload_file',response.code)
        return response.data.file_key

    def send_file(self,chat,key,identity):
        from lark_oapi.api.im.v1 import CreateMessageRequest,CreateMessageRequestBody
        body=CreateMessageRequestBody.builder().receive_id(chat).msg_type('file').content(json.dumps({'file_key':key})).uuid(hashlib.md5(identity.encode()).hexdigest()).build()
        with self.lock:
            delay=0.25-(time.monotonic()-self.last_send)
            if delay>0:time.sleep(delay)
            response=self.client.im.v1.message.create(CreateMessageRequest.builder().receive_id_type('chat_id').request_body(body).build())
            self.last_send=time.monotonic()
        if not response.success() or not response.data or not response.data.message_id:raise FeishuError('send_file',response.code)
        return response.data.message_id

    def remove_typing(self, message_id, reaction_id):
        from lark_oapi.api.im.v1 import DeleteMessageReactionRequest
        request = DeleteMessageReactionRequest.builder().message_id(message_id).reaction_id(reaction_id).build()
        with self.lock:
            response = self.client.im.v1.message_reaction.delete(request)
        # Deleted/recalled messages and an already absent reaction need no cleanup.
        if not response.success() and response.code not in {230110, 231003, 231004, 231005, 231011, 232009}:
            raise FeishuError("remove_typing", response.code)


class Relay:
    def __init__(self, state, api, host=None, tracker=None):
        self.state, self.api = state, api
        self.tracker=tracker or WorkspaceTracking(state)
        self.host = host or CodexHost(state, self.notify,tracker=self.tracker)
        self.host.question_cards=hasattr(api,'send_question')
        self.terminal = TerminalBridge(state,self.tracker)
        self.stop = threading.Event()
        self.receiver = None
        self.threads = []
        self.agent_workers = {}
        self.agent_worker_lock = threading.Lock()
        self.last_error = None
        self.typing_error = None
        self.typing_retry_at = 0.0
        self.typing_lock = threading.Lock()
        self.context_refresh = None

    def notify(self, session, text):
        if session.get("chat_id"):
            self.api.send(session["chat_id"], text, "notice:" + session["id"] + ":" + hashlib.sha256(text.encode()).hexdigest())

    def receive(self, payload):
        result = self.state.ingest(payload)
        if result == "paired":
            identity = self.state.identity()
            self.api.send(identity["management_chat"], "Owner paired. This is your management chat.\n\n" + HELP, "pair:" + identity["owner"])
        elif result=='controller_unavailable':
            self.api.send(self.state.identity()['management_chat'],
                'The Control group has no bound Codex conversation yet. Attach a tracked Git workspace with the local attach-controller command; slash commands still work.',
                'controller-unavailable:'+payload['header']['app_id']+':'+payload['event']['message']['message_id'])
        return result

    def receive_card(self,payload):
        from .user_questions import callback
        try:return {'toast':{'type':'success','content':callback(self,payload)}}
        except Invalid as error:return {'toast':{'type':'error','content':str(error)}}

    def attach_controller(self,workspace,session_id=None):
        self.tracker.require_workspace(workspace)
        session,created=self.state.reserve_controller(workspace,**({'session_id':session_id} if session_id else {}))
        if not created:
            if session['state']!='ready' or not session['thread_id']:
                raise Invalid('Controller creation is unfinished or paused; inspect its saved state before continuing')
            self.tracker.code_directory(session)
            return session
        # Reuse the paired owner group. Never create a second Feishu group or
        # reuse another bot's thread, worktree or native terminal attachment.
        self.tracker.prepare_session(session)
        if isinstance(self.tracker,WorkspaceTracking) and self.state.ledger.selection(session['id']):
            from .task_branches import switch
            switch(self.tracker,session,self.state.ledger.selection(session['id']))
        thread=self.host.create_thread(session)
        self.state.set_session(session['id'],thread_id=thread,state='ready')
        from .portable_settings import lifecycle
        session=lifecycle(self.state,self.state.session(session['id']))
        self.tracker.queue_record(session,phase='Paused' if session['state']=='paused' else 'Ready')
        return session

    def open_session(self, workspace, new=False,session_id=None):
        if not WORKSPACE.fullmatch(workspace):
            raise Invalid("Invalid workspace name")
        self.tracker.require_workspace(workspace)
        session, created = self.state.reserve_session(workspace, new=new,**({'session_id':session_id} if session_id else {}))
        if not created and session['state']!='restored':
            if session["state"] == "creating":
                raise Invalid("An earlier creation is unfinished; inspect it before creating another chat")
            self.tracker.code_directory(session)
            return session
        self.tracker.prepare_session(session)
        if isinstance(self.tracker,WorkspaceTracking) and self.state.ledger.selection(session['id']):
            from .task_branches import switch
            switch(self.tracker,session,self.state.ledger.selection(session['id']))
        chat = self.api.create_chat(session, self.state.identity()["owner"])
        self.state.set_session(session["id"], chat_id=chat)
        session = self.state.session(session["id"])
        thread = self.host.create_thread(session)
        self.state.set_session(session["id"], thread_id=thread, state="ready")
        session = self.state.session(session["id"])
        from .portable_settings import activate_role
        activate_role(self.state,session)
        from .portable_settings import lifecycle
        session=lifecycle(self.state,session)
        self.tracker.queue_record(session,phase='Paused' if session['state']=='paused' else 'Ready')
        self.notify(session, self.tracker.describe(session)+"\nSession: " + session["id"]
                    + "\nSend a text message here to start. /help shows the tutorial. No PR without your explicit request.")
        return session

    def attach_terminal(self,workspace,config,code_branch,display_name='Debug Bot'):
        if not isinstance(display_name,str) or not display_name.strip() or len(display_name)>80:
            raise Invalid('Supply a short terminal chat name')
        if self.state.db.execute('SELECT 1 FROM sessions WHERE thread_id=?',(config['thread_id'],)).fetchone():
            raise Invalid('This exact conversation is already registered; reuse its existing chat')
        binding=self.tracker.require_workspace(workspace)
        session,created=self.state.reserve_session(workspace,new=True)
        self.state.set_session(session['id'],thread_id=config['thread_id'])
        session=self.state.session(session['id'])
        self.tracker.prepare_existing(session,binding['source_path'],code_branch)
        chat=self.api.create_chat(dict(session,display_name=display_name),self.state.identity()['owner'])
        self.state.set_session(session['id'],chat_id=chat)
        session=self.state.session(session['id'])
        from .terminal_bridge import attach
        attach(self.state,self.tracker,session,config)
        self.state.set_session(session['id'],state='ready')
        session=self.state.session(session['id'])
        self.terminal.poll(session)
        selection=self.terminal.selection(session)
        if selection['model']!='terminal-controlled' and selection['effort']!='terminal-controlled':
            self.state.set_model(session['id'],selection['model'],selection['effort'])
        session=self.state.session(session['id'])
        self.tracker.record(session,phase='Ready')
        self.notify(session,'Debug Bot is attached to the existing tmux conversation; no new Codex was launched.\n'+self.tracker.describe(session)
                    +'\nSend text here when the terminal is idle. Completed turns are tracked on GitHub before replies. /help shows controls. Model changes and approvals use the original terminal.')
        return session

    def control(self, item):
        parts = item["text"].strip().split(maxsplit=2)
        command = parts[0].lstrip("/").lower() if parts else "help"
        argument = parts[1] if len(parts) > 1 else None
        management = item["chat_id"] == self.state.identity()["management_chat"]
        # Keep group-wide commands on their existing administrative path. For
        # conversation controls, an explicit SESSION still selects another bot;
        # otherwise use the controller conversation bound to this group.
        controller=self.state.controller_session() if management else None
        local_commands={'model','models','reasoning','fast','compact','clear','restart','archive','unarchive',
            'cost','usage','goal','progress','trigger','skills','send-file','files','task','chat','memory',
            'stop','cancel','pause','resume','console','recovery','mail','research','questions'}
        if controller and item.get('session')==controller['id'] and command in local_commands:
            if not argument or not argument.startswith('s_'):management=False
        if command=='queue':
            from .steering import queue_later
            return queue_later(self.state,item)
        if command=='init':
            if not management or item['sender']!=self.state.identity()['owner']:raise Invalid('Only the owner can initialize projects in the Control group')
            from .project_init import command as init_command
            return init_command(self,item['text'].split()[1:],item=item)
        if command=='recovery':
            from .auto_recovery import command as recovery_command
            return recovery_command(self,item,parts[1:])
        if command=='mail':
            from .peer_mail import command as mail_command
            return mail_command(self,item)
        if command=='research':
            from .research import execute
            if item['sender']!=self.state.identity()['owner']:raise Invalid('Only the owner can manage research alarms')
            try:tokens=shlex.split(item['text'])[1:]
            except ValueError:raise Invalid('Close quoted research instructions') from None
            session=self.state.session(tokens.pop(0) if management and tokens and tokens[0].startswith('s_') else item.get('session'))
            return json.dumps(execute(self.state,session,tokens,source=item),ensure_ascii=False)
        if command=='access':
            if not management or item['sender']!=self.state.identity()['owner']:raise Invalid('Workspace access is managed only by the owner in management')
            from .access import command as access_command
            return access_command(self.state,item['text'].split()[1:])
        if command == "help":
            if argument:
                if argument.lower().lstrip("/") in {"model","models","reasoning"}:
                    return MODEL_HELP
                if argument.lower().lstrip('/') in {'files','file','send-file'}:
                    from .file_transfer import HELP as FILE_HELP
                    return FILE_HELP
                if argument.lower().lstrip('/') in {'mail','peer'}:
                    from .peer_mail import HELP as PEER_HELP
                    return PEER_HELP
                if argument.lower().lstrip('/') in {'research','watch'}:
                    from .research import HELP as RESEARCH_HELP
                    return RESEARCH_HELP
                if argument.lower().lstrip('/') in {'goal','goals'}:
                    from .goals import HELP as GOAL_HELP
                    return GOAL_HELP
                if argument.lower().lstrip('/') in {'question','questions','answer'}:
                    from .user_questions import HELP as QUESTION_HELP
                    return QUESTION_HELP
                raise Invalid("Unknown tutorial topic. Use /help, /help model or /help files.")
            context = "You are in the management group." if management else "You are in a workspace chat."
            extra=''
            session=self.state.session(item.get('session'))
            if controller:
                extra='\n\nThis Control group has its own Codex conversation. Send ordinary text to talk to it; /model, /task and /stop default to it. /console shows its tmux console. /list and /consoles show all bots. Use SESSION explicitly to control another bot.'
            if self.terminal.binding(session):
                extra='\n\nDebug Bot is attached to an existing tmux conversation. Text waits for its empty idle prompt. /stop interrupts that terminal turn. Model changes and terminal approvals must be handled in the original pane; /model shows its observed selection. Completed terminal turns are also tracked and mirrored here.'
            return context + "\n\n" + HELP + extra
        if command in {"model","models","reasoning"}:
            return self.model_command(item,command,management)
        if command == 'fast':
            from .speed import command as fast_command
            return fast_command(self,item,management)
        if command=='team':
            if not management:raise Invalid('Use the management chat for team changes and review')
            from .teams import TeamCommands
            try:tokens=shlex.split(item['text'])[1:]
            except ValueError:raise Invalid('Close quoted team names and prompts') from None
            try:return TeamCommands(self).command(tokens)
            except ValueError as error:
                if isinstance(error,Invalid):raise
                raise Invalid('Invalid team argument') from None
        if command in {'compact','clear','restart','archive','unarchive','cost','usage'}:
            session=self.state.session(argument if management else item['session'])
            from .operations import session_command,usage
            return usage(self,session) if command in {'cost','usage'} else session_command(self,session,command)
        if command=='doctor':
            from .operations import diagnose
            return json.dumps(diagnose(self.state),ensure_ascii=False)
        if command=='goal':
            if item['sender']!=self.state.identity()['owner']:raise Invalid('Only the owner can manage goals')
            from .operations import goal
            tokens=item['text'].split()[1:]
            session=self.state.session(tokens.pop(0) if management and tokens else item['session'])
            return goal(self,session,tokens)
        if command=='questions':
            from .user_questions import describe,demo
            tokens=item['text'].split()[1:]
            session=self.state.session(tokens.pop(0) if management and tokens and tokens[0].startswith('s_') else item['session'])
            if tokens==['test']:
                if item['sender']!=self.state.identity()['owner']:raise Invalid('Only the owner can send a question test')
                return demo(self.state,session)
            return describe(self.state,session)
        if command=='progress':
            session=self.state.session(argument if management else item['session'])
            mode=parts[2] if management and len(parts)>2 else argument if not management else None
            if not session or mode not in {'summary','messages','off'}:raise Invalid('Use /progress summary|messages|off in a workspace chat, or /progress SESSION MODE in management')
            from .progress import set_mode
            set_mode(self.state,session['id'],mode!='off','messages' if mode=='messages' else 'editable')
            return 'Progress '+mode+'. Assistant prose and execution milestones; no function calls or reasoning traces.'
        if command in {'trigger','skills'}:
            if item['sender']!=self.state.identity()['owner']:raise Invalid('Only the owner can configure triggers or skills')
            session=self.state.session(argument if management else item['session'])
            args=parts[2] if management and len(parts)>2 else item['text'].split(maxsplit=1)[1] if not management and argument else ''
            if command=='trigger':
                from .triggers import command as trigger_command
                return trigger_command(self,session,args)
            from .skills import command as skills_command
            try:tokens=shlex.split(args)
            except ValueError:raise Invalid('Close quoted skill arguments') from None
            return skills_command(self.tracker,session,tokens)
        if command=='unsupported':
            return ((item['text'].partition(' ')[2]+'. ') if argument else 'This message type is not supported yet. ')+ 'Upload ordinary files (20 MiB each), then send your instructions. No Codex turn was started.'
        if command=='send-file':
            from .attachments import export
            try:tokens=shlex.split(item['text'])[1:]
            except ValueError:raise Invalid('Close the quoted filename') from None
            session=self.state.session(tokens.pop(0) if management and tokens else item['session'])
            committed=bool(tokens and tokens[0]=='--committed')
            if committed:tokens.pop(0)
            if not tokens:raise Invalid('Use /send-file [--committed] RELATIVE_PATH in a workspace chat')
            relative=' '.join(tokens)
            return export(self,session,relative,committed=committed,request_key='message:'+str(item['seq']),requested_by=item['sender'],source_seq=item['seq'])
        if command=='files':
            from .file_transfer import listing,retry,permissions
            from .attachment_batches import describe,clear
            tokens=item['text'].split()[1:]
            if tokens==['permissions']:return json.dumps(permissions(self.api))
            target=tokens.pop(0) if management and tokens and self.state.session(tokens[0]) else item['session']
            session=self.state.session(target)
            if tokens==['permissions']:return json.dumps(permissions(self.api))
            if tokens==['pending']:return describe(self.state,session,item['sender'])
            if tokens==['clear']:return clear(self.state,session,item['sender'])
            if tokens and tokens[0]=='retry':
                if item['sender']!=self.state.identity()['owner']:raise Invalid('Only the owner may retry file transfers')
                if len(tokens)!=2:raise Invalid('Use /files retry TRANSFER_ID')
                return retry(self.state,session,tokens[1])
            if tokens and tokens[0]=='status':tokens.pop(0)
            if len(tokens)>1:raise Invalid('Use /files pending, /files clear, /files [TRANSFER_ID] or /files retry TRANSFER_ID')
            rows=listing(self.state,session,tokens[0] if tokens else None)
            outgoing='\n'.join(r['id']+' | '+r['name']+' | '+str(r['size'])+' bytes | '+r['state']+(' | API '+str(r['error_code']) if r['error_code'] else '')+(' | '+r['error_reason'] if r['error_reason'] else '') for r in rows) or 'No outgoing file transfers in this conversation. Use /help files.'
            return (describe(self.state,session,item['sender'])+'\n'+outgoing) if not tokens else outgoing
        if command in {'task','chat'}:
            try:tokens=shlex.split(item['text'])
            except ValueError:tokens=item['text'].split()
            if management:
                if len(tokens)<3:raise Invalid('Use /task SESSION ACTION [NAME_OR_ID] in management')
                session=self.state.session(tokens[1]);action=tokens[2];argument=' '.join(tokens[3:])
            else:
                session=self.state.session(item['session'])
                action='chat' if command=='chat' else (tokens[1] if len(tokens)>1 else 'status')
                argument=' '.join(tokens[2:])
            if isinstance(self.tracker,WorkspaceTracking):
                from .task_branches import command as task_command
                return task_command(self.tracker,session,action,argument)
            return self.state.ledger.task(session,action,argument)
        if command=='memory':
            try:tokens=shlex.split(item['text'])
            except ValueError:tokens=item['text'].split()
            if management:
                if len(tokens)<3:raise Invalid('Use /memory SESSION show|add|error|rule [TEXT]')
                session=self.state.session(tokens[1]);action=tokens[2];body=' '.join(tokens[3:])
            else:
                session=self.state.session(item['session']);action=tokens[1] if len(tokens)>1 else 'show';body=' '.join(tokens[2:])
            if not session or not isinstance(self.tracker,WorkspaceTracking):raise Invalid('Memory requires a tracked workspace')
            from .memory import remember,context
            if action=='show':return context(self.tracker,session)
            return remember(self.tracker,session,{'add':'note'}.get(action,action),body)
        if command == "stop":
            command = "cancel"
        if command in {"approve", "deny", "answer"}:
            if not argument:
                raise Invalid("Supply the request code")
            if command=='answer':
                if item['sender']!=self.state.identity()['owner']:raise Invalid('Only the owner can answer Codex questions')
                from .user_questions import answer
                row=self.state.db.execute('SELECT session FROM user_questions WHERE code=?',(argument,)).fetchone()
                if not row:raise Invalid('Question unavailable; use /questions')
                if not management and row['session']!=item['session']:raise Invalid('This question belongs to another conversation')
                return answer(self.state,self.state.session(row['session']),argument,parts[2] if len(parts)>2 else '')
            self.host.decide(argument, command, None if management else item["session"],
                             parts[2] if len(parts) > 2 else None)
            return "Decision sent to Codex."
        if command in {"cancel", "pause", "resume"}:
            target = argument or item["session"]
            if not management and target != item["session"]:
                raise Invalid("Use the management chat to control another session")
            session = self.state.session(target)
            if not session or session["state"] not in {"ready", "paused"}:
                raise Invalid("Unknown or unfinished session")
            if command == "cancel":
                from .goals import current,control as goal_control
                saved_goal=current(self.state,session);goal_paused=bool(saved_goal and saved_goal['status'] not in {'paused','complete'})
                if saved_goal:goal_control(self.state,session,'pause')
                from .user_questions import cancel as cancel_questions
                cancelled_questions=cancel_questions(self.state,session)
                from .auto_recovery import cancel as cancel_recovery
                cancelled_recovery=cancel_recovery(self.state,session['id'])
                from .research import cancel_session
                affected=self.state.ledger.stop(session)+cancelled_recovery+cancel_session(self.state,session)+cancelled_questions+int(goal_paused)
                try:
                    if self.terminal.binding(session):self.terminal.interrupt(session)
                    else:self.host.interrupt(target)
                except Invalid:
                    if not affected:raise
                # Intent is durable even if a turn is still starting. The agent
                # checks it before dispatch and immediately after turn/start.
                return "Cancellation requested. Queued turns cancelled; the current turn is being interrupted. The session and named task remain available."
            if command == "resume":self.tracker.code_directory(session)
            if command=='pause':
                from .goals import current,control as goal_control
                if current(self.state,session):goal_control(self.state,session,'pause')
                from .auto_recovery import cancel as cancel_recovery
                cancel_recovery(self.state,session['id'])
                from .research import cancel_session
                cancel_session(self.state,session,pause=True)
            self.state.set_session(target, state="paused" if command == "pause" else "ready")
            uploaded=self.tracker.queue_record(self.state.session(target),phase="Paused" if command=="pause" else "Ready")
            return "Session " + ("paused." if command == "pause" else "resumed.") + ("" if uploaded else " GitHub status upload is pending.")
        if command in {"console","consoles"}:
            from .session_hosts import console
            rows=self.state.sessions() if management else [self.state.session(item['session'])]
            lines=[]
            for s in rows:
                view=console(self.state,s)
                label='Intern Agent Control' if controller and s['id']==controller['id'] else s['workspace']
                lines.append(label+': '+(view['attach'] if view else 'Console not started yet'))
            return '\n'.join(lines) or 'No conversations yet.'
        if command in {"list", "status"}:
            status = self.state.status()
            rows = status["sessions"] if management else [s for s in status["sessions"] if s["id"] == item["session"]]
            from .session_hosts import console
            text = "\n".join(('Intern Agent Control' if controller and s['id']==controller['id'] else s["workspace"]) + " | " + s["id"] + " | " + s["state"]
                             + (" | " + chat_link(s["chat_id"]) if s["chat_id"] else "")
                             + ("\nConsole: " + console(self.state,s)["attach"] if console(self.state,s) else "") for s in rows)
            if command=="status" and not management and rows:
                text += "\n"+self.tracker.describe(rows[0])+'\n'+self.state.ledger.task(rows[0],'status')
            if command=='status':
                text += '\n'+self.state.turn_status(None if management else item['session'])
                from .auto_recovery import status as recovery_status
                recovery=recovery_status(self.state,None if management else item['session'])
                text+='\nRecovery: '+('enabled' if recovery['enabled'] else 'disabled')
                for ep in recovery['episodes']:
                    text+='\n'+ep['id']+': '+ep['state']+' | attempts '+str(ep['attempts'])+' | '+ep['category']
                from .research import listing as research_listing
                for s in rows:
                    for watch in research_listing(self.state,s):
                        if watch['state'] in {'waiting','queued','running','paused','review_needed'}:
                            text+='\nResearch '+watch['id']+': '+watch['state']+' | checks '+str(watch['checks'])+'/'+str(watch['max_checks'])+' | next '+watch['next_check_utc']
            return (text or "No sessions yet. Attach a workspace, then use /new NAME.") + ("\nQueue: " + json.dumps(status["events"])
                        + "\nUncertain tasks: " + json.dumps(status["uncertain"])
                        + "\nGitHub uploads pending: " + json.dumps(status["tracking_pending"]) if command == "status" and management else "")
        if command not in {"new","new-session","reconnect","resolve"}:
            return "Unsupported command. Send /help for the tutorial and supported commands. Codex terminal-only commands are not forwarded."
        if not management:
            raise Invalid("Use the management chat for workspace creation and reconciliation")
        if command=='reconnect':
            if item['sender']!=self.state.identity()['owner'] or len(parts) not in {2,3} or len(parts)==3 and parts[2]!='controller':raise Invalid('Use /reconnect SESSION [controller] in the owner Control group')
            saved=self.state.session(argument)
            if not saved:raise Invalid('Unknown restored conversation')
            hint=self.state.db.execute('SELECT role FROM restored_roles WHERE session=?',(saved['id'],)).fetchone()
            controller=bool(len(parts)==3 or hint and hint['role']=='controller')
            session=self.attach_controller(saved['workspace'],session_id=saved['id']) if controller else self.open_session(saved['workspace'],session_id=saved['id'])
            return 'Conversation reconnected: '+session['id']+'\n'+chat_link(session['chat_id'])+'\nUploaded history and task selection are preserved; old prompts were not replayed.'
        if command in {"new", "new-session"}:
            if not argument:
                raise Invalid("Supply a workspace name")
            session = self.open_session(argument, new=command == "new-session")
            return "Workspace chat ready: " + session["workspace"] + "\nSession: " + session["id"] + "\n" + chat_link(session["chat_id"])
        if command == "resolve":
            if len(parts) != 3:
                raise Invalid("Use /resolve TURN cancel|deliver|retry")
            self.state.resolve(int(argument), parts[2])
            saved=self.state.db.execute("SELECT * FROM events WHERE seq=?",(int(argument),)).fetchone()
            if saved["session"]:
                self.tracker.queue_record(self.state.session(saved["session"]),dict(saved),saved["state"])
            return "Task reconciled. No agent action was replayed."
        return HELP

    def model_command(self,item,command,management):
        arguments = item["text"].split()[1:]
        target = item.get("session")
        if management and arguments and arguments[0].startswith("s_"):
            target = arguments.pop(0)
        session = self.state.session(target) if target else None
        if target and (not session or session["state"] not in {"ready","paused"}):
            raise Invalid("Unknown or unfinished session. Use /list in the management group.")
        if management and arguments and not session:
            raise Invalid("Select a session: /model SESSION MODEL [EFFORT]. Find SESSION with /list, or use its workspace chat.")
        attached=self.terminal.binding(session)
        if attached and arguments:
            raise Invalid('This chat is attached to the running terminal. Change /model or reasoning in that pane; no relay setting was changed.')
        selection = self.terminal.selection(session) if attached else self.host.model_selection(session)
        try:
            catalog = self.host.models(None if attached else session)
        except Exception:
            raise Invalid("Codex's model list is unavailable. No settings changed; try /model again when Codex is connected.") from None
        choices = {row["model"]:row for row in catalog}
        def efforts(row):
            return [option["reasoningEffort"] for option in row.get("supportedReasoningEfforts",[])]
        if command == "models" and arguments:
            raise Invalid("Use /models without arguments, or /model MODEL [EFFORT] to change a workspace conversation.")
        if not arguments:
            current = "Current model: " + selection["model"] + "\nReasoning effort: " + selection["effort"]
            if not session:
                current = "Configured defaults (no session selected): " + selection["model"] + " / " + selection["effort"]
            elif not attached:
                current+='\nSaved selection for subsequent turns; an active turn keeps its current model.'
                observed=self.state.db.execute('SELECT snapshot FROM console_views WHERE session=?',(session['id'],)).fetchone()
                snapshot=json.loads(observed['snapshot']) if observed else {}
                if snapshot.get('model') and snapshot.get('effort'):
                    current+='\nLast observed console setting: '+snapshot['model']+' / '+snapshot['effort']
            if command == "reasoning":
                row = choices.get(selection["model"],{})
                return current + "\nSupported efforts: " + (", ".join(efforts(row)) or "not listed") + "\nUse /reasoning EFFORT in a workspace, or /reasoning SESSION EFFORT in management."
            rows = [row["model"] + " | efforts: " + (", ".join(efforts(row)) or "not listed") for row in catalog]
            return current + "\n\nModels listed by Codex:\n" + ("\n".join(rows) or "No models listed.") + "\n\nUse /model MODEL [EFFORT] in a workspace, or /model SESSION MODEL [EFFORT] in management. Send /help model for examples. Catalog listing does not guarantee account access."
        if not session:
            raise Invalid("Open a workspace chat or select a session from /list first.")
        if command == "reasoning":
            if len(arguments)!=1:
                raise Invalid("Use /reasoning EFFORT (management: /reasoning SESSION EFFORT).")
            model,effort = selection["model"],arguments[0]
        else:
            if len(arguments)>2:
                raise Invalid("Use /model MODEL [EFFORT] (management: /model SESSION MODEL [EFFORT]).")
            model,effort = arguments[0],arguments[1] if len(arguments)==2 else None
            if model=="default":
                if effort is not None:
                    raise Invalid("Use /model default without an effort.")
                default=self.host.model_selection()
                model,effort=default["model"],default["effort"]
        row = choices.get(model)
        if not row:
            raise Invalid("Model is not in Codex's current list. Send /model to see valid model names; no settings changed.")
        supported = efforts(row)
        if effort is None:
            effort = selection["effort"] if model==selection["model"] and selection["effort"] in supported else row.get("defaultReasoningEffort")
        if not effort or effort not in supported:
            raise Invalid("Unsupported reasoning effort for this model. Choose: " + (", ".join(supported) or "none listed") + ". No settings changed.")
        from .speed import saved,is_fast,fast_tier
        if is_fast(saved(self.state,session)):
            fast_tier(row)
        from .model_preferences import save
        preference=save(self.state,session['id'],model,effort,item['seq'])
        if preference['seq']!=item['seq']:
            return 'This older model command was superseded by a newer saved selection: '+preference['model']+' / '+preference['effort']+'. No setting changed.'
        acknowledgement=''
        if session['state']=='ready' and hasattr(self.host,'set_model_settings'):
            try:
                self.host.set_model_settings(session,model,effort)
                acknowledgement='\nCodex confirmed these settings for subsequent turns.'
            except Exception:
                acknowledgement='\nThe saved choice is protected, but Codex has not acknowledged it yet. The next relay turn will send it explicitly; /model shows the saved choice.'
        uploaded=self.tracker.queue_record(self.state.session(session["id"]),phase="Model updated")
        return "Model selected: " + model + "\nReasoning effort: " + effort + "\nSaved for this conversation. Applies to the next turn; the current turn is unchanged. Other chats keep their settings." + acknowledgement + ("" if uploaded else "\nGitHub status upload is pending.")

    def process_once(self, role, session_id=None):
        pending=getattr(self.state.local,'preflight_retry',None)
        if pending:
            # If saving a retry itself encountered a database lock, keep
            # repairing that exact claim before taking more input.
            return self.retry_preflight(*pending) or 'idle'
        item = self.state.claim(role,session_id=session_id) if session_id is not None else self.state.claim(role)
        if not item:
            return "idle"
        try:
            if not self.state.authorized(item):
                self.state.transition(item["seq"], "cancelled_policy_changed")
                return "cancelled_policy_changed"
            if role not in {'urgent','urgent_reply'}:self.begin_typing(item)
            return self.process_item(item, role)
        except Exception as error:
            self.last_error=type(error).__name__
            # Release an interrupted SQLite transaction before persisting the
            # failed preflight. The witness is committed with claim(), and is
            # closed before entering either Codex transport.
            outcome=self.retry_preflight(item,error) if role=='agent' else None
            if outcome:
                return outcome
            raise
        finally:
            # Returning from this worker can mean the terminal is still running.
            # Cleanup checks durable completion, rather than worker lifetime.
            if role not in {'urgent','urgent_reply'}:self.end_typing(item)
            if role in {'urgent','urgent_reply'}:
                workspace,_=self.state.ledger.workspace_for(item)
                if workspace:
                    from .turn_uploads import enqueue_status
                    enqueue_status(self.state,self.state.session(item.get('session')))
            else:
                saved=self.state.db.execute("SELECT * FROM events WHERE seq=?",(item["seq"],)).fetchone()
                from .turn_uploads import ready
                finalized=role=='agent' and saved['state']=='complete' and ready(self.state,item['seq'])
                if not finalized and (hasattr(self.tracker,'binding') or item['session']):
                    self.tracker.queue_record(self.state.session(item["session"]),dict(saved),saved["state"])

    def retry_preflight(self,item,error):
        self.state.local.preflight_retry=(item,error)
        self.state.close()
        from .dispatch import retry
        outcome=retry(self.state,item,error)
        self.state.local.preflight_retry=None
        if outcome=='record_blocked':
            from .flow_watchdog import preflight
            preflight(self,item,error)
        return outcome

    def begin_typing(self, item):
        # Local extension commands have no Feishu message to react to.
        if not identifier(item["message_id"], "om"):
            return
        with self.typing_lock:
            if self.state.db.execute('SELECT 1 FROM message_reactions WHERE seq=?', (item['seq'],)).fetchone():
                return  # Reuse the reaction through restart and delivery retries.
            if time.monotonic() < self.typing_retry_at:
                return
            try:
                reaction = self.api.add_typing(item["message_id"])
                if not reaction:
                    raise Invalid("Missing typing reaction receipt")
                try:
                    self.state.db.execute("INSERT OR REPLACE INTO message_reactions(seq,reaction_id) VALUES(?,?)",
                                          (item["seq"], reaction))
                except Exception:
                    self.api.remove_typing(item["message_id"], reaction)
                    raise
                self.typing_error = None
            except Exception as error:
                # Presentation failure must not change task execution or delivery.
                self.typing_error = type(error).__name__
                self.typing_retry_at = time.monotonic() + 60

    def end_typing(self, item):
        with self.typing_lock:
            self._end_typing(item)

    def _end_typing(self, item):
        try:
            from .auto_recovery import protected_typing
            if protected_typing(self.state,item['seq']):return
            saved = self.state.db.execute('SELECT * FROM events WHERE seq=?', (item['seq'],)).fetchone()
            activity=self.state.db.execute('SELECT activity FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()
            if not saved or saved['state'] not in TYPING_FINISHED:
                return
            # A cancel/resolve request does not itself prove that the actual
            # terminal turn stopped. Wait for its observed completion/abort.
            if saved['role'] == 'agent':
                for table in ['terminal_attachments','console_views']:
                    attachment=self.state.db.execute('SELECT snapshot FROM '+table+' WHERE session=?',(saved['session'],)).fetchone()
                    snapshot=json.loads(attachment['snapshot']) if attachment else {}
                    if ((snapshot.get('active') and snapshot.get('event_seq') == saved['seq'])
                            or (snapshot.get('pending') or {}).get('seq') == saved['seq']):
                        return
                if saved['session'] in self.host.active:
                    return
            row = self.state.db.execute("SELECT reaction_id FROM message_reactions WHERE seq=?", (item["seq"],)).fetchone()
            if not row:
                return
            self.api.remove_typing(saved["message_id"], row["reaction_id"])
            self.state.db.execute("DELETE FROM message_reactions WHERE seq=?", (item["seq"],))
        except Exception as error:
            self.typing_error = type(error).__name__
            try:
                self.state.db.execute("UPDATE message_reactions SET retry_at=? WHERE seq=?", (time.time()+60, item["seq"]))
            except Exception:
                pass

    def cleanup_typing(self):
        rows = self.state.db.execute('''SELECT e.* FROM message_reactions r JOIN events e ON e.seq=r.seq
            WHERE r.retry_at<=? AND (e.state IN ('complete','cancelled','cancelled_policy_changed')
            OR e.receipt IS NOT NULL) LIMIT 20''', (time.time(),)).fetchall()
        for row in rows:
            self.end_typing(dict(row))

    def typing_cleanup_loop(self):
        try:
            while not self.stop.is_set():
                try:
                    self.cleanup_typing()
                    self.tracker.retry_pending()
                    self.retry_sync()
                except Exception as error:
                    self.typing_error = type(error).__name__
                self.stop.wait(5)
        finally:
            self.state.close()

    def process_item(self, item, role):
        if item["state"] == "queued":
            if role=='agent':
                session=self.state.session(item['session'])
                try:
                    from .auto_recovery import guard
                    guard(self,item)
                    from .peer_mail import guard as peer_guard
                    peer_guard(self,item)
                    from .research import guard as research_guard
                    research_guard(self,item)
                    from .release_sync import guard as release_guard
                    release_guard(self,item)
                    from .goals import guard as goal_guard
                    goal_guard(self,item)
                    if hasattr(self.tracker,'begin_turn'):self.tracker.begin_turn(session,item)
                    if isinstance(self.tracker,WorkspaceTracking):
                        self.state.ledger.update(item,'running',activity='running')
                    else:self.tracker.record(session,item,'Running')
                except Exception as error:
                    from .goals import Deferred as GoalDeferred
                    if isinstance(error,GoalDeferred):
                        self.state.db.execute("UPDATE events SET state='queued' WHERE seq=? AND state='running'",(item['seq'],))
                        self.state.db.execute('UPDATE dispatch_attempts SET retry_at=? WHERE seq=? AND stage=\'preflight\'',(error.due,item['seq']))
                        self.state.ledger.update(item,'waiting',activity='waiting')
                        return 'deferred'
                    from .research import Deferred
                    if isinstance(error,Deferred):
                        watch=self.state.db.execute('SELECT w.state FROM research_event_scope r JOIN research_watches w ON w.id=r.watch WHERE r.seq=?',(item['seq'],)).fetchone()
                        retry=watch and watch['state'] in {'waiting','queued','running'} and not self.state.ledger.stopped(item['seq'])
                        self.state.db.execute("UPDATE events SET state=? WHERE seq=? AND state='running'",('queued' if retry else 'cancelled',item['seq']))
                        self.state.ledger.update(item,'waiting' if retry else 'cancelled',activity='waiting' if retry else 'stopped')
                        return 'deferred' if retry else 'cancelled'
                    from .auto_recovery import RecoverySkipped
                    if isinstance(error,RecoverySkipped):
                        self.state.transition(item['seq'],'cancelled',result=str(error))
                        self.state.ledger.update(item,'cancelled',activity='stopped')
                        return 'cancelled'
                    if isinstance(error,sqlite3.OperationalError):
                        raise  # The committed preflight witness permits bounded retry.
                    from .flow_watchdog import preflight
                    preflight(self,item,error)
                    return self.defer_sync(item,error)
                if self.state.ledger.stopped(item['seq']):
                    self.state.transition(item['seq'],'result_ready',result='Cancelled before execution.')
                    item['result']='Cancelled before execution.';item['state']='result_ready'
                else:
                    from .progress import mark
                    mark(self,item,'running')
        if item['state']=='queued':
            if role=='agent':
                from .dispatch import start
                start(self.state,item)
            try:
                if role=="agent":
                    session=self.state.session(item["session"])
                    from .attachments import prepare
                    prompt,inputs,resources=prepare(self,item)
                    if self.terminal.binding(session):result=self.terminal.run(session,prompt,item['seq'],resource_context=resources)
                    elif inputs or resources:result=self.host.run(session,prompt,inputs=inputs,resource_context=resources)
                    else:result=self.host.run(session,prompt)
                else:result=self.control(item)
            except TurnFailed as error:
                result=str(error)
            except TurnBusy as error:
                from .busy_inputs import defer
                return defer(self.state,item,error.turn_id)
            except Invalid as error:
                # Validation precedes external effects in ordinary commands. Other exceptions
                # are ambiguous and never become an automatic replay.
                result = str(error)
            except Exception as error:
                self.last_error = type(error).__name__
                if role=='agent':self.state.note_failure(item['seq'],error)
                if role=='agent':
                    attachment=self.terminal.binding(self.state.session(item['session']))
                    snapshot=json.loads(attachment['snapshot']) if attachment else {}
                    if snapshot.get('active') and snapshot.get('event_seq')==item['seq']:
                        self.state.transition(item['seq'],'awaiting_terminal')
                        self.state.ledger.update(item,'awaiting_terminal',activity='running')
                        return 'awaiting_terminal'
                self.state.transition(item["seq"], "uncertain_execution")
                from .progress import mark
                mark(self,item,'uncertain_execution')
                try:
                    self.api.send(item["chat_id"], "Turn " + str(item["seq"]) + " needs review (execution unconfirmed). Use /status in this chat for the saved state and recovery steps. The request will not be rerun automatically.", "error:" + str(item["seq"]))
                except Exception:
                    pass
                return "uncertain_execution"
            if not isinstance(result, str) or not result.strip() or len(result.encode()) > 1048576:
                self.state.transition(item["seq"], "uncertain_execution")
                return "uncertain_execution"
            self.state.transition(item["seq"], "result_ready" if role=='urgent' else "preparing_reply", result=result)
            if role=='agent':
                record=self.state.ledger.ensure_turn(item)
                evidence=json.loads(record['evidence']) if record else []
                activity=('failed' if any('task_failed' in e or 'turn/completed status failed' in e for e in evidence)
                    else 'stopped' if any('interrupted' in e or 'turn_aborted' in e for e in evidence) else 'completed')
                self.state.ledger.update(item,'result_ready',activity=activity)
            item["result"] = result
            if role=='urgent':
                # Keep incoming interrupts independent of a slow Feishu send.
                # A separate worker delivers the saved acknowledgement.
                return 'result_ready'
        if not self.state.authorized(item):
            self.state.transition(item["seq"], "cancelled_policy_changed")
            return "cancelled_policy_changed"
        if role=="agent":
            from .progress import mark
            mark(self,item,'uploading')
            try:
                session=self.state.session(item["session"])
                if self.terminal.binding(session):self.terminal.wait_idle(session)
                elif hasattr(self.host,'observer') and self.host.observer.binding(session):self.host.wait_idle(session)
                if isinstance(self.tracker,WorkspaceTracking):self.tracker.publish_code(session,item,push=False)
                else:self.tracker.publish_code(session)
                self.tracker.record(session,item,"Reply ready")
                if not isinstance(self.tracker,WorkspaceTracking):
                    # Explicit offline adapters have no network repository.
                    self.state.db.execute("INSERT OR REPLACE INTO turn_uploads VALUES(?,?,'offline','uploaded','{}',NULL,NULL,?,?,0)",
                        (item['seq'],session['id'],time.time(),time.time()))
            except Exception as error:
                return self.defer_sync(item,error)
        outcome=self.deliver_saved(item,'complete')
        if outcome!='complete':return outcome
        if role=='agent':
            self.state.ledger.observe(item,'complete')
            from .teams import complete
            complete(self.state,item)
        self.state.db.execute('DELETE FROM sync_retries WHERE seq=?',(item['seq'],))
        return "complete"

    def deliver_saved(self,item,after_state):
        from .delivery import send
        return send(self,item,after_state)

    def defer_sync(self,item,error):
        from .progress import mark
        mark(self,item,'sync_pending')
        # A failed Git operation is known, unlike an interrupted agent or send.
        # Persist first; retry only the recording/delivery stage, never Codex.
        self.last_error=type(error).__name__
        from .turn_uploads import failure
        retry_delay=failure(self.state,item,error)
        phase='record_blocked' if isinstance(error,Invalid) else 'sync_pending'
        self.state.db.execute('BEGIN IMMEDIATE')
        try:
            self.state.transition(item['seq'],phase)
            if phase=='sync_pending':
                self.state.db.execute('INSERT OR REPLACE INTO sync_retries(seq,retry_at) VALUES(?,?)',(item['seq'],time.time()+retry_delay))
            self.state.db.execute('COMMIT')
        except BaseException:self.state.db.execute('ROLLBACK');raise
        self.state.ledger.update(item,phase,sync='blocked' if phase=='record_blocked' else 'pending')
        saved=self.state.db.execute('SELECT * FROM events WHERE seq=?',(item['seq'],)).fetchone()
        activity=self.state.db.execute('SELECT activity FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()
        session=self.state.session(item.get('session'))
        active=False
        for bridge in [self.terminal,getattr(self.host,'observer',None)]:
            binding=bridge.binding(session) if bridge and session else None
            snapshot=json.loads(binding['snapshot']) if binding else {}
            active=active or bool(snapshot.get('active') or snapshot.get('pending'))
        active=active or bool(item.get('session') in self.host.active)
        if (saved['result'] and activity and activity['activity'] in {'completed','stopped','failed'}
                and not active and self.state.authorized(dict(saved))):
            return phase  # Final answers stay saved until Git publication succeeds.
        return phase

    def retry_sync(self):
        # Rejoin the ordinary queue and use its atomic claim and FIFO guards.
        # Cleanup must not become another unleased delivery worker or wait
        # indefinitely for a live Codex turn to finish.
        self.state.db.execute('BEGIN IMMEDIATE')
        try:
            self.state.schedule_sync_retries()
            rows=self.state.db.execute('''SELECT e.* FROM sync_retries r JOIN events e ON e.seq=r.seq
                WHERE e.state='sync_pending' AND r.retry_at<=? ORDER BY e.seq LIMIT 10''',(time.time(),)).fetchall()
            for row in rows:
                self.state.transition(row['seq'],'result_ready' if row['result'] else 'queued')
                self.state.db.execute('DELETE FROM sync_retries WHERE seq=?',(row['seq'],))
            self.state.db.execute('COMMIT')
        except BaseException:self.state.db.execute('ROLLBACK');raise

    def loop(self, role, session_id=None):
        try:
            while not self.stop.is_set():
                try:
                    outcome = self.process_once(role,session_id=session_id) if session_id is not None else self.process_once(role)
                    if outcome == "idle":
                        self.stop.wait(0.02 if role=='urgent' else 0.25)
                except Exception as error:
                    self.last_error = type(error).__name__
                    self.stop.wait(1)
        finally:
            self.state.close()

    def ensure_agent_workers(self):
        # A conversation owns one worker, regardless of how many messages arrive.
        # Three busy unrelated turns must not occupy every input lane.
        sessions=self.state.db.execute("SELECT DISTINCT session FROM events WHERE role='agent' AND state IN ('queued','result_ready') AND session IS NOT NULL").fetchall()
        with self.agent_worker_lock:
            for row in sessions:
                session_id=row['session'];worker=self.agent_workers.get(session_id)
                if worker and worker.is_alive():continue
                worker=threading.Thread(target=self.loop,args=('agent',session_id),daemon=True,
                                        name='intern-agent-conversation')
                self.agent_workers[session_id]=worker;self.threads.append(worker);worker.start()

    def agent_dispatch_loop(self):
        try:
            while not self.stop.is_set():
                try:self.ensure_agent_workers()
                except Exception as error:self.last_error=type(error).__name__
                self.stop.wait(.1)
        finally:self.state.close()

    def heartbeat(self):
        while not self.stop.is_set():
            connection = getattr(self.receiver, "_conn", None)
            connected = bool(connection and getattr(connection, "open", True))
            write_private(self.state.root / "health.json", {
                "updated_at": int(time.time()), "pid": os.getpid(), "receiver_connected": connected,
                "paired": bool(self.state.identity()["owner"]), "last_error_type": self.last_error,
                "terminal_error_type": self.terminal.last_error,
                "console_error_type": getattr(getattr(self.host,'observer',None),'last_error',None),
                "recovery_error_type": getattr(getattr(self,'recovery',None),'last_error',None),
                "research_error_type": getattr(getattr(self,'research',None),'last_error',None),
                "peer_worker_alive": bool(getattr(self,'peer_thread',None) and self.peer_thread.is_alive()),
                "peer_health": getattr(self,'peer_health',None),
                "release_worker_alive": bool(getattr(self,'release_thread',None) and self.release_thread.is_alive()),
                "release_error_type": getattr(self,'release_error_type',None),
                "goal_worker_alive": bool(getattr(self,'goal_thread',None) and self.goal_thread.is_alive()),
                "goal_error_type": getattr(self,'goal_error_type',None),
                "question_worker_alive": bool(getattr(self,'question_thread',None) and self.question_thread.is_alive()),
                "question_error_type": getattr(self,'question_error_type',None),
                "typing_error_type": self.typing_error,
                "context_refresh": self.context_refresh,
                "steering_worker_alive": bool(getattr(self,'steering_thread',None) and self.steering_thread.is_alive())})
            self.stop.wait(5)

    def start(self, credentials):
        import lark_oapi as lark
        lease = os.open(self.state.runtime / "relay.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Invalid("A relay already owns this live state") from None
            self.state.recover()
            from .prompt_context import refresh
            self.context_refresh = refresh(self.tracker)
            from .auto_recovery import Recovery
            self.recovery=Recovery(self)
            recovery_thread=threading.Thread(target=self.recovery.loop,daemon=True)
            recovery_thread.start();self.threads.append(recovery_thread)
            from .research import Research
            self.research=Research(self)
            research_thread=threading.Thread(target=self.research.loop,daemon=True,name='intern-agent-research-clock')
            research_thread.start();self.threads.append(research_thread)
            from .steering import loop as steering_loop
            steering_thread=threading.Thread(target=steering_loop,args=(self.state,self.stop),daemon=True,name='intern-agent-owner-steering')
            self.steering_thread=steering_thread
            steering_thread.start();self.threads.append(steering_thread)
            from .peer_mail import loop as peer_loop
            peer_thread=threading.Thread(target=peer_loop,args=(self,),daemon=True)
            self.peer_thread=peer_thread
            peer_thread.start();self.threads.append(peer_thread)
            from .release_sync import loop as release_loop
            releases=threading.Thread(target=release_loop,args=(self,),daemon=True);self.release_thread=releases
            releases.start();self.threads.append(releases)
            from .goals import loop as goal_loop
            goals=threading.Thread(target=goal_loop,args=(self,),daemon=True);self.goal_thread=goals
            goals.start();self.threads.append(goals)
            from .user_questions import loop as question_loop
            questions=threading.Thread(target=question_loop,args=(self,),daemon=True);self.question_thread=questions
            questions.start();self.threads.append(questions)
            from .delivery import loop as delivery_loop
            courier=threading.Thread(target=delivery_loop,args=(self,),daemon=True)
            courier.start();self.threads.append(courier)
            from .file_transfer import loop as file_loop
            file_courier=threading.Thread(target=file_loop,args=(self,),daemon=True)
            file_courier.start();self.threads.append(file_courier)
            from .progress import loop as progress_loop
            progress_thread=threading.Thread(target=progress_loop,args=(self,),daemon=True)
            progress_thread.start();self.threads.append(progress_thread)
            cleanup = threading.Thread(target=self.typing_cleanup_loop, daemon=True)
            cleanup.start()
            self.threads.append(cleanup)
            terminal=threading.Thread(target=self.terminal.loop,daemon=True)
            terminal.start();self.threads.append(terminal)
            if hasattr(self.host,'observer'):
                consoles=threading.Thread(target=self.host.observer.loop,daemon=True)
                consoles.start();self.threads.append(consoles)
            dispatcher=threading.Thread(target=self.agent_dispatch_loop,daemon=True)
            dispatcher.start();self.threads.append(dispatcher)
            for role in ['urgent','urgent_reply',"control"]:
                worker = threading.Thread(target=self.loop, args=(role,), daemon=True)
                worker.start()
                self.threads.append(worker)
            def callback(data):
                try:
                    result = self.receive(json.loads(lark.JSON.marshal(data)))
                    print(json.dumps({"event": result}), flush=True)
                except Exception as error:
                    self.last_error = type(error).__name__
                    # Queue persistence failures propagate: do not acknowledge durable acceptance.
                    raise
            def card_callback(data):
                from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
                return P2CardActionTriggerResponse(self.receive_card(json.loads(lark.JSON.marshal(data))))
            handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(callback).register_p2_card_action_trigger(card_callback).build()
            self.receiver = lark.ws.Client(credentials["app_id"], credentials["app_secret"],
                                            domain=lark.FEISHU_DOMAIN, event_handler=handler, log_level=lark.LogLevel.ERROR)
            # Public SDK hooks expose connection state without logging signed URLs.
            if hasattr(self.receiver, "on_reconnecting"):
                self.receiver.on_reconnecting = lambda: setattr(self, "last_error", "ReceiverReconnecting")
                self.receiver.on_reconnected = lambda: setattr(self, "last_error", None)
            lark.logger.disabled = True
            threading.Thread(target=self.heartbeat, daemon=True).start()
            print(json.dumps({"relay": "starting", "paired": bool(self.state.identity()["owner"])}), flush=True)
            self.receiver.start()
        finally:
            self.stop.set()
            self.terminal.stop.set()
            if hasattr(self.host,'observer'):self.host.observer.stop.set()
            for session_id in list(self.host.active):
                try:
                    self.host.interrupt(session_id)
                except Exception:
                    pass
            os.close(lease)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Intern Agent live Feishu relay (explicit opt-in)")
    parser.add_argument("--state", required=True, help="Private writable state directory; use a data volume")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--github-owner", required=True)
    init.add_argument("--model", required=True)
    init.add_argument("--codex-executable", required=True)
    init.add_argument("--auth-home", default=str(Path.home() / ".codex"))
    init.add_argument("--proxy", action="store_true")
    init.add_argument("--sandbox", choices=["read-only", "workspace-write", "danger-full-access"], default="workspace-write")
    init.add_argument('--yolo',action='store_true',help='Explicitly select full access with no execution approval prompts')
    bootstrap=commands.add_parser('bootstrap',help='Initialize a replacement host from a PAT supplied only through stdin')
    bootstrap.add_argument('--codex-executable');bootstrap.add_argument('--model',default='gpt-6.1-sol')
    bootstrap.add_argument('--proxy',action='store_true')
    commands.add_parser("status")
    commands.add_parser('dashboard')
    commands.add_parser('doctor')
    profiles=commands.add_parser('host-profile',help='Discover or register the existing relay for other SSH editor clients')
    profiles.add_argument('action',choices=['discover','register'])
    commands.add_parser('github-auth',help='Read a GitHub PAT only from stdin and save it privately after account verification')
    skill_install=commands.add_parser('install-skills',help='Copy bundled public Codex skills; preserve existing modified instructions')
    skill_install.add_argument('destination')
    releases=commands.add_parser('release-sync',help='Explicitly configure a separate source-to-release bot handoff policy')
    releases.add_argument('action',choices=['status','configure','off'])
    releases.add_argument('--source');releases.add_argument('--operator');releases.add_argument('--repo')
    releases.add_argument('--license',choices=['MIT','Apache-2.0'],default='MIT')
    recovering=commands.add_parser('recover-repository',help='Restore uploaded workspace records by repository name, without replaying prompts')
    recovering.add_argument('action',choices=['preview','apply','status'])
    recovering.add_argument('target',nargs='?')
    repair=commands.add_parser('repair',help='Preview local repairs; apply/rollback only an explicitly reviewed plan')
    repair.add_argument('action',choices=['preview','apply','rollback']);repair.add_argument('plan',nargs='?')
    control=commands.add_parser('control',help='Queue an owner action using the existing relay')
    control.add_argument('text');control.add_argument('--session')
    file_send=commands.add_parser('file-send',help='Queue an owner-requested file to this context\'s existing Feishu chat')
    file_send.add_argument('path');file_send.add_argument('--context',required=True)
    file_send.add_argument('--key');file_send.add_argument('--committed',action='store_true')
    file_status=commands.add_parser('file-status',help='Inspect file deliveries for this context, without exposing resource keys')
    file_status.add_argument('--context',required=True);file_status.add_argument('transfer',nargs='?')
    file_permissions=commands.add_parser('file-permissions',help='Read the current app file permission status; never changes scopes')
    file_permissions.add_argument('--context',required=True)
    goal_tool=commands.add_parser('goal-tool',help='Context-bound semantic goal selection and evidence checkpoints')
    goal_tool.add_argument('action',choices=['start','progress','complete','blocked','wait','pause','status'])
    goal_tool.add_argument('--context',required=True);goal_tool.add_argument('--objective');goal_tool.add_argument('--budget',type=int)
    goal_tool.add_argument('--note',default='');goal_tool.add_argument('--next',dest='next_action',default='');goal_tool.add_argument('--after')
    ask_tool=commands.add_parser('ask',help='Read structured questions from JSON stdin and wait for their exact Feishu answers')
    ask_tool.add_argument('--context',required=True);ask_tool.add_argument('--key')
    peer=commands.add_parser('peer',help='Context-bound durable bot communication')
    peer.add_argument('--context',required=True)
    peer.add_argument('action',choices=['list','ask','note','reply','status','read','ack','cancel'])
    peer.add_argument('target',nargs='?');peer.add_argument('body',nargs='?');peer.add_argument('--key')
    research=commands.add_parser('research',help='Persistent bounded research wake-ups in an existing conversation')
    route=research.add_mutually_exclusive_group(required=True)
    route.add_argument('--context');route.add_argument('--session')
    research.add_argument('research_args',nargs=argparse.REMAINDER)
    commands.add_parser("pair-code")
    commands.add_parser("start")
    commands.add_parser("launch")
    register = commands.add_parser("register")
    register.add_argument("name")
    register.add_argument("path")
    attach=commands.add_parser("attach",help="Register GitHub workspace; each new bot uses one code/history branch")
    attach.add_argument("name")
    attach.add_argument("path")
    attach.add_argument("--repo",required=True)
    attach.add_argument("--metadata-branch",help='Required only for explicit legacy metadata_branch layout')
    attach.add_argument('--tracking-mode',choices=['bot_branch','metadata_branch'],default='bot_branch')
    attach.add_argument("--base-branch",default="main")
    controller=commands.add_parser('attach-controller',help='Bind the paired management group to its own tracked Codex conversation')
    controller.add_argument('name',help='An already attached GitHub workspace')
    project=commands.add_parser('project-init',help='Preview a new private GitHub project; apply queues the reviewed owner plan')
    project.add_argument('action',choices=['preview','apply','status']);project.add_argument('name',nargs='?')
    project.add_argument('--repo',help='New repository name; defaults to the workspace name')
    archive=commands.add_parser('archive-management',help='Record management conversation in an explicitly selected private repository')
    archive.add_argument('name')
    archive.add_argument('--bot-session',help='Explicit destination bot for a workspace with combined bot branches')
    archive.add_argument('--confirm-private-repository',required=True,action='store_true')
    restore=commands.add_parser('restore',help='Preview or recover history from a fetched bot or legacy management branch into fresh state')
    restore.add_argument('path');restore.add_argument('--repo',required=True);restore.add_argument('--branch','--metadata-branch',dest='metadata_branch',required=True)
    restore.add_argument('--apply',action='store_true',help='Explicitly adopt the branch and recover saved records; never restores routing or replays work')
    terminal=commands.add_parser('attach-terminal',help='Bind this exact Codex tmux conversation to a tracked workspace')
    terminal.add_argument('name');terminal.add_argument('--code-branch',required=True)
    terminal.add_argument('--display-name',default='Debug Bot')
    opening = commands.add_parser("open")
    opening.add_argument("name")
    opening.add_argument("--new-session", action="store_true")
    resolve = commands.add_parser("resolve")
    resolve.add_argument("task", type=int)
    resolve.add_argument("action", choices=["cancel", "deliver",'retry'])
    smoke = commands.add_parser("agent-smoke")
    smoke.add_argument("--workspace",required=True,help="An already attached tracked workspace")
    smoke.add_argument('--session',help='Explicitly choose a ready conversation, including the controller')
    smoke.add_argument("--prompt", default="Reply with exactly INTERN_AGENT_OK. Do not run tools or change files.")
    args = parser.parse_args(argv)
    state = LiveState(args.state)
    try:
        if args.command == "init":
            if not valid_owner(args.github_owner):
                raise Invalid("Configure a real GitHub username")
            config = state.root / "live.json"
            if config.exists() or config.is_symlink():
                raise Invalid("Live configuration already exists")
            if not Path(args.codex_executable).is_file() or not os.access(args.codex_executable, os.X_OK):
                raise Invalid("Codex executable must be an executable file")
            if not args.model or any(c in args.model for c in "<> \n\0"):
                raise Invalid("Select a model available to your account")
            write_private(config, {"schema": "intern-agent.live.v1", "github_owner": args.github_owner,
                "model": args.model, "sandbox": 'danger-full-access' if args.yolo else args.sandbox, "reasoning_effort": "high",
                "approval_policy": 'never' if args.yolo else 'on-request',
                "approvals_reviewer": 'user',
                "codex_command": [str(Path(args.codex_executable).resolve())],
                "codex_auth_home": str(Path(args.auth_home).expanduser().resolve()),
                "launcher_home": str(Path.home()), "codex_proxy": "http://127.0.0.1:17890" if args.proxy else None,
                "turn_timeout_seconds": 0})
            print(json.dumps({"initialized": True, "state": str(state.root)}))
        elif args.command=='bootstrap':
            from .onboarding import bootstrap
            raw=sys.stdin.read(4097)
            if len(raw)>4096:raise Invalid('GitHub credential input is too large')
            value=json.loads(raw)
            if not isinstance(value,dict) or set(value)!={'pat'}:raise Invalid('Supply only the PAT through stdin')
            print(json.dumps(bootstrap(state,value['pat'],executable=args.codex_executable,model=args.model,proxy=args.proxy)))
        elif args.command=='host-profile':
            from .host_profile import discover,register
            print(json.dumps(discover() if args.action=='discover' else register(state)))
        elif args.command=='release-sync':
            from . import release_sync
            if args.action=='configure':
                if not all([args.source,args.operator,args.repo]):raise Invalid('Choose a source, separate operator and public repository')
                result=release_sync.configure(state,args.source,args.operator,args.repo,args.license)
            elif args.action=='off':
                current=release_sync.policy(state)
                if current:
                    current['enabled']=False;write_private(state.root/'release-policy.json',current)
                result=dict(enabled=False,existing_release_jobs_preserved=True)
            else:
                current=release_sync.policy(state)
                result=dict(enabled=bool(current and current['enabled']),jobs=release_sync.status(state))
            print(json.dumps(result))
        elif args.command=='install-skills':
            from .skill_bundle import install
            print(json.dumps(install(args.destination)))
        elif args.command=='github-auth':
            from .github_auth import configure
            raw=sys.stdin.read(4097)
            if len(raw)>4096:raise Invalid('GitHub credential input is too large')
            value=json.loads(raw)
            if not isinstance(value,dict) or set(value)!={'pat'}:raise Invalid('Supply only the PAT through stdin')
            print(json.dumps(configure(state,value['pat'])))
        elif args.command=='recover-repository':
            from . import repository_recovery as recovery_flow
            tracker=WorkspaceTracking(state)
            if args.action!='status' and not args.target:raise Invalid('Supply a repository name or recovery plan ID')
            print(json.dumps(recovery_flow.status(state) if args.action=='status' else
                recovery_flow.preview(tracker,args.target) if args.action=='preview' else recovery_flow.apply(tracker,args.target)))
        elif args.command=='repair':
            from . import repairs
            if args.action!='preview' and not args.plan:raise Invalid('Supply the exact reviewed repair plan ID')
            print(json.dumps(repairs.preview(state) if args.action=='preview' else getattr(repairs,args.action)(state,args.plan),indent=2))
        elif args.command in {'dashboard','doctor'}:
            from .operations import dashboard,diagnose
            print(json.dumps(dashboard(state) if args.command=='dashboard' else diagnose(state),indent=2))
        elif args.command=='ask':
            from .research import actor
            from .user_questions import ask
            raw=sys.stdin.read(65537)
            if len(raw)>65536:raise Invalid('Question input exceeds 64 KiB')
            value=json.loads(raw)
            if not isinstance(value,dict) or set(value)!={'questions'}:raise Invalid('Supply a questions array through JSON stdin')
            print(json.dumps(ask(state,actor(state,args.context),value['questions'],key=args.key),ensure_ascii=False))
        elif args.command=='goal-tool':
            from .research import actor
            from . import goals
            session=actor(state,args.context)
            if args.action=='start':
                result=goals.start(state,session,args.objective or '',budget=args.budget,auto=True)
            elif args.action=='status':result=goals.visible(goals.current(state,session))
            elif args.action=='pause':
                goals.owner_turn(state,session);result=goals.control(state,session,'pause')
            else:
                source=state.db.execute("SELECT seq FROM events WHERE session=? AND role='agent' AND state IN ('running','awaiting_terminal') ORDER BY seq DESC LIMIT 1",(session['id'],)).fetchone()
                if source:
                    from .peer_mail import binding as mail_binding
                    if mail_binding(state,source[0])[1] or state.db.execute('SELECT 1 FROM release_jobs WHERE event_seq=?',(source[0],)).fetchone():raise Invalid('Peer and release work cannot change this goal')
                else:goals.owner_turn(state,session)
                result=goals.checkpoint(state,session,args.action,note=args.note,next_action=args.next_action,after=args.after)
            if args.action in {'start','complete'}:
                from types import SimpleNamespace
                tracker=WorkspaceTracking(state)
                relay=SimpleNamespace(state=state,tracker=tracker,host=CodexHost(state,tracker=tracker),terminal=TerminalBridge(state,tracker))
                try:goals.sync_native(relay,session)
                except Invalid as error:
                    state.db.execute("UPDATE goal_plans SET status='yielded',note=? WHERE id=?",(str(error),goals.current(state,session)['id']));goals.persist(state,session)
                except Exception:pass
            print(json.dumps(result,ensure_ascii=False))
        elif args.command=='research':
            from .research import actor,execute
            session=actor(state,args.context) if args.context else state.session(args.session)
            if not session:raise Invalid('Choose a registered research conversation')
            if not args.context and os.environ.get('CODEX_THREAD_ID'):
                raise Invalid('Agents must use their own context-bound research tool')
            print(json.dumps(execute(state,session,args.research_args),ensure_ascii=False))
        elif args.command=='peer':
            from . import peer_mail
            session=peer_mail.actor(state,args.context)
            if args.action=='list':value=peer_mail.targets(state)
            elif args.action in {'ask','note'}:
                value=peer_mail.send(state,WorkspaceTracking(state),session,peer_mail.resolve(state,args.target),args.body,kind=args.action,key=args.key)
            elif args.action=='status':value=peer_mail.listing(state,session,args.target)
            elif args.action in {'read','ack'}:value=peer_mail.read(state,session,args.target,ack=args.action=='ack')
            elif args.action=='reply':value=peer_mail.reply(state,WorkspaceTracking(state),session,args.target,args.body)
            else:value=peer_mail.cancel(state,session,args.target)
            print(json.dumps(value,ensure_ascii=False))
        elif args.command in {'file-send','file-status','file-permissions'}:
            from .file_transfer import context_session,enqueue,listing,permissions
            session=context_session(state,args.context)
            if args.command=='file-permissions':value=permissions(FeishuAPI(state.credentials()))
            else:value=(enqueue(state,WorkspaceTracking(state),session,args.path,request_key=args.key,committed=args.committed)
                        if args.command=='file-send' else listing(state,session,args.transfer))
            print(json.dumps(value,ensure_ascii=False))
        elif args.command=='control':
            identity=state.identity()
            if not identity['owner']:raise Invalid('Pair the relay first')
            text=args.text.strip()
            if not text.startswith('/') or len(text)>16000:raise Invalid('Supply a bounded slash command')
            session=state.session(args.session) if args.session else state.controller_session()
            if args.session and not session:raise Invalid('Unknown conversation')
            role='urgent' if text.split()[0].lower() in {'/stop','/cancel'} else 'control'
            cursor=state.db.execute('''INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state)
                VALUES(?,?,?,?,?,'__control__',?,?,'queued')''',(identity['app_id'],'local_'+uuid.uuid4().hex,
                session['chat_id'] if session else identity['management_chat'],identity['owner'],role,session['id'] if session else None,text))
            item=dict(state.db.execute('SELECT * FROM events WHERE seq=?',(cursor.lastrowid,)).fetchone());state.ledger.ensure_turn(item,time.time(),'local')
            print(json.dumps({'queued':True}))
        elif args.command == "status":
            status = state.status()
            try:
                health = private_json(state.root / "health.json")
                status["receiver"] = {"connected": bool(health.get("receiver_connected")),
                    "fresh": 0 <= time.time() - health.get("updated_at", 0) < 20,
                    "last_error_type": health.get("last_error_type")}
            except (OSError,ValueError,Invalid):
                status["receiver"] = {"connected": False, "fresh": False}
            print(json.dumps(status, indent=2))
        elif args.command == "pair-code":
            state.credentials()
            path = state.pairing_code()
            print(json.dumps({"pairing_code_file": str(path), "expires_in_seconds": 900,
                              "instruction": "Read this file privately, then send /pair CODE in your management group containing the bot"}))
        elif args.command == "register":
            print(json.dumps(state.register_workspace(args.name, args.path)))
        elif args.command == "attach":
            binding=WorkspaceTracking(state).attach(args.name,args.path,args.repo,args.metadata_branch,args.base_branch,mode=args.tracking_mode)
            print(json.dumps({"attached":True,"workspace":args.name,"branch_layout":binding['metadata_mode'],"management_branch":binding["metadata_branch"] or None,"published":bool(binding["published"])}))
        elif args.command == "open":
            state.enqueue_open(args.name, args.new_session)
            print(json.dumps({"queued": True}))
        elif args.command=='attach-controller':
            relay=Relay(state,FeishuAPI(state.credentials()))
            session=relay.attach_controller(args.name)
            print(json.dumps({'controller_ready':session['state']=='ready','workspace':session['workspace'],
                'existing_management_group':True,'own_codex_conversation':True}))
        elif args.command=='project-init':
            from types import SimpleNamespace
            from .project_init import ProjectInit,jobs,summary
            manager=ProjectInit(SimpleNamespace(state=state,tracker=WorkspaceTracking(state)))
            try:
                if args.action=='preview':
                    if not args.name:raise Invalid('Supply a workspace name')
                    print(json.dumps(manager.preview(args.name,args.repo)))
                elif args.action=='status':
                    print(json.dumps(summary(state,manager.row(args.name)) if args.name else {'initializations':jobs(state)}))
                else:
                    row=manager.row(args.name)
                    identity=state.identity()
                    if not identity['owner']:raise Invalid('Pair the owner Control group first')
                    if row['phase']=='complete':print(json.dumps(summary(state,row)))
                    else:
                        controller=state.controller_session()
                        cursor=state.db.execute('''INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state)
                            VALUES(?,?,?,?,'control','__control__',?,?,'queued')''',
                            (identity['app_id'],'local_'+uuid.uuid4().hex,identity['management_chat'],identity['owner'],
                             controller['id'] if controller else None,'/init apply '+row['id']))
                        item=dict(state.db.execute('SELECT * FROM events WHERE seq=?',(cursor.lastrowid,)).fetchone())
                        state.ledger.ensure_turn(item,time.time(),'local')
                        print(json.dumps({'queued':True,'plan_id':row['id']}))
            finally:manager.close()
        elif args.command=='restore':
            from .recovery import restore
            print(json.dumps(restore(WorkspaceTracking(state),args.path,args.repo,args.metadata_branch,args.apply),indent=2))
        elif args.command=='archive-management':
            tracker=WorkspaceTracking(state);binding=tracker.require_workspace(args.name)
            config=state.config();config['records_workspace']=args.name
            if binding['metadata_mode']=='bot_branch':
                destination=state.session(args.bot_session)
                if not destination or destination['workspace']!=args.name or not tracker.intern(destination):raise Invalid('Choose --bot-session for this workspace\'s controller records')
                config['records_bot_session']=destination['id']
            write_private(state.root/'live.json',config)
            print(json.dumps({'management_recording_workspace':args.name,'private_repository_confirmed_by_operator':True}))
        elif args.command=='attach-terminal':
            from .terminal_bridge import current_terminal
            relay=Relay(state,FeishuAPI(state.credentials()))
            session=relay.attach_terminal(args.name,current_terminal(),args.code_branch,args.display_name)
            print(json.dumps({'attached':True,'workspace':session['workspace'],'name':args.display_name,'existing_conversation':True}))
        elif args.command == "resolve":
            state.resolve(args.task, args.action)
            print(json.dumps({"resolved": True}))
        elif args.command == "agent-smoke":
            WorkspaceTracking(state).require_workspace(args.workspace)
            controller=state.controller_session()
            session=state.session(args.session) if args.session else next((s for s in reversed(state.sessions())
                if s['workspace']==args.workspace and s['state']=='ready' and (not controller or s['id']!=controller['id'])),None)
            if session and (session['workspace']!=args.workspace or session['state']!='ready'):
                raise Invalid('Choose a ready conversation in the selected workspace')
            if not session:raise Invalid("Open a tracked workspace conversation before running its smoke test")
            if not args.prompt.strip() or len(args.prompt.encode())>32768:raise Invalid("Invalid smoke prompt")
            identity=state.identity()
            cursor=state.db.execute('''INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state)
                VALUES(?,?,?,?,'agent',?,?,?,'queued')''',
                (identity["app_id"],"local_"+uuid.uuid4().hex,session["chat_id"],identity["owner"],args.workspace,session["id"],args.prompt))
            print(json.dumps({"agent_smoke_queued":True,"task":cursor.lastrowid,"workspace":args.workspace}))
        elif args.command == "launch":
            state.credentials()
            state.config()
            socket = state.runtime / "tmux.sock"
            if len(os.fsencode(socket)) >= 104:
                raise Invalid("Choose a shorter live state path")
            env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
            exists = subprocess.run(["tmux", "-S", str(socket), "has-session", "-t", "ia-personal-relay"],
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            if exists:
                print(json.dumps({"launched": False, "already_running": True,
                                  "tmux_socket": str(socket), "session": "ia-personal-relay"}))
                return 0
            source = Path(__file__).resolve().parent.parent
            from .deployment import prepare_runtime
            source = prepare_runtime(state,source)
            command = ["/usr/bin/env", "-i", "HOME=" + env["HOME"], "PATH=" + os.environ.get("PATH", "/usr/bin:/bin"),
                       "LANG=C.UTF-8", "PYTHONPATH=" + str(source), sys.executable,
                       "-m", "intern_personal.feishu_live", "--state", str(state.root), "start"]
            subprocess.run(["tmux", "-S", str(socket), "new-session", "-d", "-s", "ia-personal-relay",
                            "-c", str(source), shlex.join(command)], env=env, check=True)
            print(json.dumps({"launched": True, "tmux_socket": str(socket), "session": "ia-personal-relay"}))
        else:
            credentials = state.credentials()
            state.config()
            relay = Relay(state, FeishuAPI(credentials))
            def stop_signal(*_):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, stop_signal)
            relay.start(credentials)
    except (Invalid,BridgeError,FeishuError) as error:
        print(json.dumps({"error": str(error)}))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"relay": "stopped"}))
    except Exception as error:
        # Transport exception strings can contain signed URLs. Never print them.
        print(json.dumps({"error_type": type(error).__name__, "error": "Live operation failed; check private state and network readiness"}))
        return 2
    finally:
        state.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
