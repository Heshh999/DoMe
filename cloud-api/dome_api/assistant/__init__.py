"""The AI assistant, step 1 (docs/design/assistant.md): an optional, consented, metered interpreter that turns what
the owner said into at most three AI-eligible steps or a short spoken butler reply. The phone resolves, shows, signs
and sends every resulting command itself; nothing here can run anything on a PC."""

from dome_api.assistant.catalogue import CONSENT_VERSION, OUTPUT_SCHEMA, SYSTEM_TEXT
from dome_api.assistant.service import AssistantConfig, AssistantService, build_provider

__all__ = ["CONSENT_VERSION", "OUTPUT_SCHEMA", "SYSTEM_TEXT", "AssistantConfig", "AssistantService", "build_provider"]
