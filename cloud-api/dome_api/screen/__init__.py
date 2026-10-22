"""Screen view, Phase 1 (docs/design/screen-view.md §10, §12, §13; protocol 1.4).

The relay's half of Screen view: the Free-minutes ledger settled on the relay's own clock (``metering``), the phone's
TURN credentials with their caps and revocation paths (``turn``), and the service that ties both to the relay
(``service``). Nothing here ever sees media; no SDP, address or credential is logged or stored.
"""
