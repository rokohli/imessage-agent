"""Dashboard contracts. Extra input fields and implicit type coercions are refused."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from zoneinfo import ZoneInfo
from .models import Recipient, payload_hash
from .account_selection import validate_account_id
from .applescript import _literal
from .ollama import ModelConfig


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    @field_validator('confirmed', mode='before', check_fields=False)
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError('explicit_confirmation_required')
        return value


class ScheduleInput(Input):
    recipient: str = Field(min_length=1, max_length=254)
    body: str = Field(min_length=1, max_length=4000)
    start_date: str
    start_time: str
    timezone: str = Field(max_length=100)
    end_time: str | None = None
    recurrence: Literal['once', 'daily', 'weekly', 'weekdays'] = 'once'
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    until: str | None = None
    fold: Literal['reject', 'earlier', 'later'] = 'reject'
    grace: int = Field(default=300, ge=0, le=86400)
    transport: Literal['mock', 'live'] = 'mock'
    account_id: str | None = None
    confirmed: Literal[True]
    authorize_recipient: bool = False
    recipient_name: str | None = Field(default=None, min_length=1, max_length=200)


class ScheduleSpec(BaseModel):
    recipient: str
    body: str
    start_date: str
    start_time: str
    end_time: str
    window: bool
    timezone: str
    recurrence: str
    weekdays: list[int]
    until: str | None
    fold: str
    grace: int
    transport: str
    account_id: str | None


class ScheduleView(BaseModel):
    display_name: str = ''
    id: str
    status: str
    spec: ScheduleSpec
    replaces: str | None


class Occurrence(BaseModel):
    id: str
    plan_id: str
    local_date: str
    window_start: float
    window_end: float
    send_at: float
    deadline: float
    status: str
    detail: str
    claimed_at: float | None
    transport_status: str | None
    recipient: str
    display_name: str = ''


class Context(BaseModel):
    direction: str
    body: str | None
    occurred_at: float | None = None
    timestamp: str | None = None
    timezone: str | None = None
    session_start: bool = False
    topic_boundary: bool = False
    historical: bool = False


class DraftView(BaseModel):
    id: str
    recipient: str
    recipient_key: str
    display_name: str = ''
    conversation_state: str = 'UNCERTAIN'
    body: str
    reason: str
    status: str
    revision: str
    created_at: float
    context: list[Context]
    approval_id: str | None


class DraftEdit(Input):
    body: str = Field(min_length=1, max_length=4000)
    revision: str

    @field_validator('body')
    @classmethod
    def text(cls, value):
        payload_hash(Recipient.parse('+12025550101'), value)
        _literal(value)
        return value


class DraftAction(Input):
    revision: str


class ApprovalAction(DraftAction):
    confirmed: Literal[True]
    recipient: str
    body: str


class Settings(Input):
    model: str = 'qwen2.5:3b'
    timezone: str = 'America/Chicago'
    grace: int = Field(default=300, ge=0, le=86400)
    notifications: bool = False
    style_mode: Literal['single_pass', 'two_pass'] = 'single_pass'

    @field_validator('model')
    @classmethod
    def model_name(cls, value):
        ModelConfig(model=value)
        return value

    @field_validator('timezone')
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except (ValueError, KeyError):
            raise ValueError('invalid_timezone') from None
        return value


class AccountChoice(Input):
    account_id: str = Field(min_length=1, max_length=1024)
    confirmed: Literal[True]

    @field_validator('account_id')
    @classmethod
    def account(cls, value):
        return validate_account_id(value)


class AccountView(BaseModel):
    selected: str | None
    choices: list[str]
    valid: bool


class ContactPreference(Input):
    future_auto_reply: Literal['disabled', 'ask_first'] = 'disabled'


class Profile(BaseModel):
    sample_count: int = 0
    median_words: float = 0
    lowercase_fraction: float = 0
    emoji_fraction: float = 0


class ContactView(BaseModel):
    recipient_key: str
    display_name: str
    address: str
    kind: str
    outbound_allowed: bool
    handles: list[str]
    profiles: list[Profile]
    future_auto_reply: str


class AttemptView(BaseModel):
    id: str
    display_name: str = ''
    recipient: str
    status: str
    created_at: float


class Health(BaseModel):
    messages: str
    account: str
    ollama: str
    scheduler: str
    worker: str = 'not_managed_by_dashboard'
    auto_reply: Literal[False] = False


class Summary(BaseModel):
    upcoming_count: int
    upcoming: list[Occurrence]
    pending_approvals: int
    saved_drafts: int
    recent_attempts: list[AttemptView]
    scheduler: str


class Result(BaseModel):
    ok: bool = True


class WorkerError(BaseModel):
    timestamp: float
    category: str


class WorkerHealth(BaseModel):
    status: str
    heartbeat: float | None
    last_poll: float | None
    pending_count: int
    next_execution: float | None
    recent_errors: list[WorkerError]
    last_outcome: str | None
    transport: str | None


class ContactsAccess(Input):
    country_code: str | None = Field(default=None, pattern=r'^[1-9][0-9]{0,2}$')


class ConversationThresholds(Input):
    gap_seconds: float = Field(default=14400, gt=0, le=604800)
    day_gap_seconds: float = Field(default=1800, gt=0, le=86400)
    burst_seconds: float = Field(default=15, gt=0, le=300)


class RecipientSearch(Input):
    query: str = Field(min_length=2, max_length=100)


class RecipientMatch(BaseModel):
    display_name: str
    recipient: str
    recipient_key: str


class RecipientMatches(BaseModel):
    status: str
    matches: list[RecipientMatch]


class StyleEditView(BaseModel):
    id: str
    draft_id: str
    recipient_key: str
    original: str
    edited: str
    created_at: float
    accepted_at: float | None
