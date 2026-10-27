"""Loopback control plane. No endpoint dispatches messages or runs a scheduler."""
import hmac
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .account_selection import AccountSelectionError, load_account_id, save_account_id
from .applescript import diagnose_applescript, list_imessage_accounts
from .dashboard_models import (AccountChoice, AccountView, ApprovalAction, AttemptView, ContactPreference,
                               ContactView, ContactsRefresh, ConversationThresholds, DraftAction, DraftEdit, DraftView, Health, Occurrence,
                               Result, ScheduleInput, ScheduleView, Settings, Summary, WorkerHealth)
from .dashboard_service import DashboardError, DashboardService
from .macos import MacOSMessagesReader, MessagesAccessError
from .ollama import DraftError, ModelConfig, Ollama
from .scheduling import SchedulingError
from .smoke import _state_path
from .storage import Store
from .worker import worker_status

ORIGINS = ('http://127.0.0.1:5173', 'http://127.0.0.1:8000')


def create_app(state: Path, *, database: Path | None = None):
    state = _state_path(database or Path('/nonexistent/messages-source'), Path(state))
    app = FastAPI(title='iMessage Agent Local Dashboard', docs_url=None, redoc_url=None)
    token = secrets.token_urlsafe(32)
    app.add_middleware(CORSMiddleware, allow_origins=list(ORIGINS),
                       allow_methods=['GET', 'POST', 'PUT', 'DELETE'],
                       allow_headers=['Content-Type', 'X-Dashboard-Token'])

    @app.middleware('http')
    async def local_boundary(request: Request, call_next):
        origin = request.headers.get('origin')
        host = request.headers.get('host')
        if host != '127.0.0.1:8000' or (origin is not None and origin not in ORIGINS):
            return JSONResponse({'error': 'local_origin_required'}, status_code=403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if (origin not in ORIGINS or not hmac.compare_digest(request.headers.get('x-dashboard-token', ''), token)
                    or request.headers.get('content-type', '').split(';')[0] != 'application/json'):
                return JSONResponse({'error': 'valid_local_session_required'}, status_code=403)
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
                                 'Content-Security-Policy': "default-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'"})
        return response

    @contextmanager
    def service():
        store = Store(state)
        try:
            yield DashboardService(store, database)
        finally:
            store.close()

    @app.exception_handler(DashboardError)
    async def dashboard_error(request, error):
        return JSONResponse({'error': error.code}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def input_error(request, error):
        # FastAPI's default includes input values, including private message text.
        return JSONResponse({'error': 'invalid_input'}, status_code=422)

    async def unavailable(request, error):
        code = 'invalid_input_or_local_state_unavailable'
        if isinstance(error, (SchedulingError, AccountSelectionError, DraftError, MessagesAccessError)):
            code = str(error)
        return JSONResponse({'error': code}, status_code=422)

    for cls in (ValueError, KeyError, OSError, sqlite3.Error, SchedulingError,
                AccountSelectionError, DraftError, MessagesAccessError):
        app.add_exception_handler(cls, unavailable)

    @app.get('/api/session')
    def session():
        return {'token': token}

    @app.get('/api/summary', response_model=Summary)
    def summary():
        with service() as s:
            drafts = s.drafts()
            upcoming = [o for o in s.occurrences() if o['status'] == 'pending']
            return dict(upcoming_count=len(upcoming), upcoming=sorted(upcoming, key=lambda o: o['send_at'])[:6],
                        pending_approvals=sum(d['status'] == 'pending' for d in drafts),
                        saved_drafts=sum(d['status'] != 'discarded' for d in drafts),
                        recent_attempts=s.attempts()[:6], scheduler='authorized_schedules_ready')

    @app.get('/api/schedules', response_model=list[ScheduleView])
    def schedules():
        with service() as s:
            return [s.schedule_view(p['id']) for p in s.scheduler.plans()]

    @app.post('/api/schedules', response_model=ScheduleView)
    def create_schedule(data: ScheduleInput):
        with service() as s:
            return s.schedule(data)

    @app.get('/api/schedules/{plan_id}', response_model=ScheduleView)
    def schedule(plan_id: str):
        with service() as s:
            return s.schedule_view(plan_id)

    @app.put('/api/schedules/{plan_id}', response_model=ScheduleView)
    def edit_schedule(plan_id: str, data: ScheduleInput):
        with service() as s:
            return s.schedule(data, replaces=plan_id)

    @app.delete('/api/schedules/{plan_id}', response_model=Result)
    def cancel_schedule(plan_id: str):
        with service() as s:
            s.scheduler.details(plan_id)
            s.scheduler.cancel(plan_id)
            return Result()

    @app.get('/api/schedules/{plan_id}/occurrences', response_model=list[Occurrence])
    def occurrences(plan_id: str):
        with service() as s:
            s.scheduler.details(plan_id)
            return s.occurrences(plan_id)

    @app.get('/api/occurrences', response_model=list[Occurrence])
    def all_occurrences():
        with service() as s:
            return s.occurrences()

    @app.post('/api/contacts/refresh')
    def refresh_contacts(data: ContactsRefresh):
        with service() as s:
            return s.names.refresh(authorize=data.authorize, country_code=data.country_code)

    @app.delete('/api/contacts/cache', response_model=Result)
    def invalidate_contacts():
        with service() as s:
            s.names.invalidate()
            return Result()

    @app.get('/api/conversation-thresholds', response_model=ConversationThresholds)
    def conversation_thresholds():
        with service() as s:
            return s.preference('conversation:thresholds', {})

    @app.put('/api/conversation-thresholds', response_model=ConversationThresholds)
    def save_conversation_thresholds(data: ConversationThresholds):
        with service() as s:
            s.save_preference('conversation:thresholds', data.model_dump())
            return data

    @app.get('/api/contacts', response_model=list[ContactView])
    def contacts(q: Annotated[str, Query(max_length=254)] = ''):
        with service() as s:
            return s.contacts(q)

    @app.put('/api/contacts/{key}/preferences', response_model=Result)
    def contact_preference(key: str, data: ContactPreference):
        with service() as s:
            if not any(c['recipient_key'] == key for c in s.contacts()):
                raise DashboardError('contact_not_found', 404)
            s.save_preference('contact:' + key, data.future_auto_reply)
            return Result()

    @app.get('/api/drafts', response_model=list[DraftView])
    def drafts():
        with service() as s:
            return [d for d in s.drafts() if d['status'] != 'discarded']

    @app.put('/api/drafts/{draft_id}', response_model=DraftView)
    def edit_draft(draft_id: str, data: DraftEdit):
        with service() as s:
            return s.change_draft(draft_id, data.revision, 'edit', body=data.body)

    @app.post('/api/drafts/{draft_id}/{action}', response_model=DraftView)
    def draft_action(draft_id: str, action: str, data: DraftAction):
        if action not in ('submit', 'discard', 'regenerate'):
            raise HTTPException(404)
        with service() as s:
            if action == 'regenerate':
                return s.regenerate(draft_id, data.revision)
            return s.change_draft(draft_id, data.revision, action)

    @app.get('/api/approvals', response_model=list[DraftView])
    def approvals():
        with service() as s:
            return [d for d in s.drafts() if d['status'] == 'pending']

    @app.post('/api/approvals/{draft_id}/approve', response_model=DraftView)
    def approve(draft_id: str, data: ApprovalAction):
        with service() as s:
            return s.change_draft(draft_id, data.revision, 'approve', body=data.body, recipient=data.recipient)

    @app.post('/api/approvals/{draft_id}/reject', response_model=DraftView)
    def reject(draft_id: str, data: DraftAction):
        with service() as s:
            return s.change_draft(draft_id, data.revision, 'reject')

    @app.get('/api/attempts', response_model=list[AttemptView])
    def attempts():
        with service() as s:
            return s.attempts()

    @app.get('/api/settings', response_model=Settings)
    def settings():
        with service() as s:
            return s.settings()

    @app.put('/api/settings', response_model=Settings)
    def save_settings(data: Settings):
        with service() as s:
            s.save_preference('settings', data.model_dump())
            return data

    @app.get('/api/accounts', response_model=AccountView)
    def accounts():
        choices = list_imessage_accounts()
        try:
            selected = load_account_id()
        except AccountSelectionError:
            selected = None
        return dict(selected=selected, choices=choices, valid=selected in choices)

    @app.put('/api/accounts', response_model=Result)
    def select_account(data: AccountChoice):
        choices = list_imessage_accounts()
        if data.account_id not in choices:
            raise DashboardError('account_choice_changed')
        chosen = data.account_id
        report = diagnose_applescript(account_id=chosen)
        if report.get('accounts') != 1 or report.get('imessage_accounts') != 1 or 'error' in report:
            raise DashboardError('account_verification_failed')
        save_account_id(chosen, replace=True)
        return Result()

    @app.get('/api/worker', response_model=WorkerHealth)
    def worker():
        with service() as s:
            return worker_status(s.store, state)

    @app.get('/api/health', response_model=Health)
    def health():
        messages = 'not_configured'
        if database is not None:
            try:
                with MacOSMessagesReader(database):
                    messages = 'available'
            except MessagesAccessError:
                messages = 'unavailable'
        try:
            report = diagnose_applescript(account_id=load_account_id())
            account = 'valid' if ('error' not in report and report.get('imessage_accounts') == 1
                                  and report.get('accounts') == 1) else 'unavailable'
        except AccountSelectionError:
            account = 'not_configured'
        with service() as s:
            scheduler = 'ready' if account == 'valid' else 'mock_ready_live_account_required'
            try:
                Ollama(ModelConfig(model=s.settings().model)).health()
                ollama = 'ready'
            except DraftError:
                ollama = 'unavailable'
        with service() as s:
            background = worker_status(s.store, state)['status']
        return Health(messages=messages, account=account, ollama=ollama, scheduler=scheduler, worker=background)

    dist = Path(__file__).resolve().parents[2] / 'dashboard' / 'dist'
    if dist.is_dir():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

        @app.get('/')
        @app.get('/{page}')
        def frontend(page: str = ''):
            if page not in ('', 'schedules', 'contacts', 'drafts', 'approvals', 'activity', 'settings'):
                raise HTTPException(404)
            return FileResponse(dist / 'index.html')
    return app


def run_dashboard(args):
    import uvicorn
    # No host option: a dashboard launch never widens the listener to the LAN.
    uvicorn.run(create_app(args.state, database=args.database), host='127.0.0.1', port=8000,
                access_log=False, log_level='warning')
    return 0
