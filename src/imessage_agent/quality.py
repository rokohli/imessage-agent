"""Known non-authored artifacts excluded from local drafting, not raw ingestion."""

import re
import unicodedata


# shortcut: fallback text detection covers observed English/Spanish forms;
# service metadata is preferred; add other locale renderings only with fixtures.
_REACTION = re.compile(
    r'^\s*(?:Liked|Loved|Disliked|Laughed at|Emphasized|Questioned|'
    r'Le gustó|Le encantó|No le gustó|Se rió de)\s+'
    r'(?:“[\s\S]*”|"[\s\S]*"|«[\s\S]*»)\s*$', re.I,
)
_AGENT_TEST = re.compile(r'^\s*(?:AGENT[ _-]+TEST\b|Testing\s+agent(?:[.!]|\s|$))', re.I)


def artifact_category(body):
    if not isinstance(body, str):
        return None
    body = unicodedata.normalize('NFC', body)
    if _REACTION.fullmatch(body):
        return 'reaction_notification'
    if _AGENT_TEST.search(body):
        return 'agent_test'
    return None


def message_artifact(message):
    return 'service_metadata' if message.is_service else artifact_category(message.body)
