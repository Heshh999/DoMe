"""The relay: bounded in-process connection manager and the two WebSocket endpoints.

Nothing here executes an action, stores a payload body, a result or a title. Signed envelopes and
challenge texts are forwarded byte-for-byte; the relay only adds sibling routing metadata.
"""
