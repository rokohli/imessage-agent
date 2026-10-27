"""Claim review for local draft previews, never a sending authorization."""

import re

from .ollama import DraftError


# These catch clear risks; ambiguous language is reviewed locally, not approved by absence of a match.
_SECRET = re.compile(
    r'\b(?:password|passcode|verification\s+code|otp|access\s+token|recovery\s+code|secret\s+key)\b|'
    r'\b(?:pin|code)\s*(?:is|:|=)\s*\S+|^\s*\d{4,8}\s*$', re.I,
)
_META = re.compile(r'<\s*/?\s*(?:think|analysis)\b|\b(?:as an ai|system prompt|ignore (?:all|previous) instructions|the user (?:is|wants))\b|^\s*(?:analysis|reasoning)\s*:', re.I)
_RISKS = (
    ('payment', r'[$€£¥]|\b(?:payment|bank account|loan|lend|venmo|zelle|paypal|owe|send money)\b|\bpay(?:ing)?\s+(?!attention\b)\S|\btransfer\s+(?:money|funds)\b'),
    ('availability', r"\b(?:i(?:'m| am|m)|we(?:'re| are)|not|am|are)\s+(?:not\s+)?(?:free|available|busy)\b|\b(?:can(?:not|'t)|cant)\s+(?:come|make it|meet)\b"),
    ('location', r"\b(?:i|we)\s+(?:live|am at|are at)\b|\b(?:i(?:'m| am|m)|we(?:'re| are))\s+(?:at|in|home|here|there)\b|\b(?:at home|on my way|omw)\b"),
    ('activity', r"(?:^|\b(?:i(?:'m| am|m)|we(?:'re| are))\s+)(?:just\s+|currently\s+)?(?:checking emails?|working|driving|cooking|eating|studying|sleeping|heading|going to|watching|playing|traveling|wfh)\b"),
    ('commitment', r"\b(?:i(?:'ll| will)|we(?:'ll| will)|promise|count on me|leave it to me|i got you|(?:ill|imma|ima|gonna)\s+(?:come|go|meet|bring|drive|help|do|handle|pay|send|book|sign|cover)|i can (?:come|help|bring|drive|handle|meet|cover|sign|book|send|do that))\b"),
    ('personal_fact', r"\b(?:i (?:own|prefer|work|study)|my (?:name|age|job|birthday)|feeling (?:great|bad|sick|tired) today)\b"),
    ('decision', r"\b(?:i|we)\s+(?:accept|agree|approve|consent|quit|cancel|book|buy|decided)\b|\b(?:let's|lets|we should)\s+(?:meet|book|buy|cancel|pay|sign|accept|quit|visit|drive|bring)\b"),
)
_CONTEXT_RISK = re.compile(
    r'\b(?:are you free|are you available|can you (?:come|meet|join|bring|drive|cover|sign|approve|buy|book|cancel)|'
    r'will you (?:come|meet|join|bring|drive|cover|sign|approve|buy|book|cancel)|do you agree|should (?:i|we))\b', re.I,
)

_SENSITIVE_TOPIC = re.compile(
    r'https?://|www\.|@|\b(?:medical|diagnosis|prescription|hospital|therapy|insurance|'
    r'social security|routing|passport|license|birthdate|confidential|account number)\b', re.I,
)
_ASSENT = re.compile(r"(?:yes|yeah|yep|yup|sure|okay|ok|sounds good|absolutely|deal|👍|👌)[!.\s]*", re.I)


def review_reply(reply, *, needs_facts, context, classify):
    """Return a fixed status/category pair; useful risky text stays reviewable."""
    if len(reply) > 300:
        return 'rejected', 'length'
    if any(ord(c) < 32 and c not in '\n\t' for c in reply):
        return 'rejected', 'control_characters'
    if _SECRET.search(reply):
        return 'rejected', 'credentials_or_code'
    if _META.search(reply):
        return 'rejected', 'reasoning_or_instructions'
    if needs_facts:
        return 'approval_required', 'unknown_personal_facts'
    if not reply:
        return 'no_reply', 'none'
    normalized = reply.replace('’', "'")
    for category, pattern in _RISKS:
        if re.search(pattern, normalized, re.I):
            return 'approval_required', category
    if _SENSITIVE_TOPIC.search(reply):
        return 'approval_required', 'sensitive'
    if _ASSENT.fullmatch(reply) and any(row['body'] == '[content excluded]' for row in context):
        return 'approval_required', 'redacted_context'
    latest = next((row['body'] for row in reversed(context) if row['direction'] == 'inbound'), '')
    if not reply.endswith('?') and _CONTEXT_RISK.search(latest):
        return 'approval_required', 'consequential_context'
    # Emoji/punctuation alone asserts no facts, but risky conversation context was checked first.
    if not re.search(r'\w', reply):
        return 'accepted', 'harmless'
    try:
        category = classify(context, reply)
    except DraftError as error:
        unavailable = str(error) in (
            'ollama_unavailable_start_loopback_server_and_install_model',
            'ollama_request_failed_check_local_server_and_installed_model',
            'installed_local_model_required_cloud_models_forbidden',
        )
        return 'approval_required', 'claim_check_unavailable' if unavailable else 'claim_check_invalid_response'
    if category == 'harmless':
        return 'accepted', category
    return 'approval_required', category
