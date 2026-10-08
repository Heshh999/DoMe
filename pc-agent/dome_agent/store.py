"""Local SQLite state (``state.sqlite3``, WAL).

Tables (see docs/design/pc-agent.md → "Local state"):

* ``settings``       key/value; ``remote_enabled`` defaults to **false** until the user enables it
* ``grants``         locally approved controllers — the ONLY source of controller public keys —
                     plus the intersection data from the latest ``grants_snapshot``
* ``journal``        durable, bounded (5,000 rows) command journal keyed by command_id with the
                     digest of the exact signed payload bytes and the last emitted frame
* ``challenges``     confirmation challenges (exact challenge_text, pinned kid, single-use)
* ``approved_apps``  app_id → validated executable identity (never editable remotely)
* ``pending_power``  at most one armed power countdown
* ``security_events`` bounded local security log for diagnostics

All methods are synchronous and protected by one re-entrant lock; every call is an indexed point
operation (sub-millisecond), so they are invoked inline from the event loop (see DECISIONS.md).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from dome_protocol import format_rfc3339, now_utc, parse_rfc3339

from .logsetup import get_logger

log = get_logger(__name__)

JOURNAL_MAX_ROWS = 5_000
SECURITY_EVENTS_MAX_ROWS = 500
TERMINAL_STATES = frozenset({"succeeded", "failed", "expired", "canceled", "outcome_unknown"})

SETTING_REMOTE_ENABLED = "remote_enabled"
SETTING_MEDIA_WHILE_LOCKED = "media_while_locked"
SETTING_START_AT_LOGIN = "start_at_login"
SETTING_PC_NAME = "pc_name"
SETTING_PC_ENABLED = "snapshot_pc_enabled"
SETTING_LAST_SNAPSHOT_ID = "last_snapshot_id"
SETTING_ENTITLEMENT = "entitlement_last_verified"
PROVISIONAL_PREFIX = "pending-"  # controller_id placeholder until the first grants_snapshot names the real one

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS grants (
    controller_id         TEXT PRIMARY KEY,
    kid                   TEXT NOT NULL UNIQUE,
    public_jwk            TEXT NOT NULL,
    capabilities          TEXT NOT NULL,
    display_name          TEXT NOT NULL,
    granted_at            TEXT NOT NULL,
    revoked_at            TEXT,
    revoked_reason        TEXT,
    snapshot_id           TEXT,
    snapshot_capabilities TEXT,
    snapshot_status       TEXT
);
CREATE TABLE IF NOT EXISTS journal (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id    TEXT NOT NULL UNIQUE,
    digest        TEXT NOT NULL,
    action        TEXT NOT NULL,
    controller_id TEXT NOT NULL,
    state         TEXT NOT NULL,
    frame_json    TEXT,
    error_code    TEXT,
    created_at    TEXT NOT NULL,
    finished_at   TEXT,
    sent          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS journal_state_idx ON journal(state);
CREATE TABLE IF NOT EXISTS challenges (
    challenge_id        TEXT PRIMARY KEY,
    command_id          TEXT NOT NULL,
    controller_id       TEXT NOT NULL,
    kid                 TEXT NOT NULL,
    challenge_text      TEXT NOT NULL,
    digest              TEXT NOT NULL,
    target_state_digest TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    consumed_at         TEXT
);
CREATE INDEX IF NOT EXISTS challenges_command_idx ON challenges(command_id);
CREATE TABLE IF NOT EXISTS approved_apps (
    app_id       TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    exe_path     TEXT NOT NULL,
    exe_sha256   TEXT NOT NULL,
    added_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_power (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    command_id TEXT NOT NULL,
    action     TEXT NOT NULL,
    fires_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS security_events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     TEXT NOT NULL,
    kind   TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class GrantRow:
    controller_id: str
    kid: str
    public_jwk: dict[str, str]
    capabilities: tuple[str, ...]
    display_name: str
    granted_at: str
    revoked_at: str | None
    revoked_reason: str | None
    snapshot_id: str | None
    snapshot_capabilities: tuple[str, ...] | None
    snapshot_status: str | None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def effective_capabilities(self, current_snapshot_id: str | None) -> tuple[str, ...]:
        """local ∩ snapshot, or () when the controller is absent from the current snapshot."""
        if current_snapshot_id is None or self.snapshot_id != current_snapshot_id or self.snapshot_capabilities is None:
            return ()
        return tuple(c for c in self.capabilities if c in self.snapshot_capabilities)


@dataclass(frozen=True, slots=True)
class JournalRow:
    command_id: str
    digest: str
    action: str
    controller_id: str
    state: str
    frame: dict[str, Any] | None
    error_code: str | None
    created_at: str
    finished_at: str | None
    sent: bool

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES


@dataclass(frozen=True, slots=True)
class ChallengeRow:
    challenge_id: str
    command_id: str
    controller_id: str
    kid: str
    challenge_text: str
    digest: str
    target_state_digest: str
    expires_at: str
    consumed_at: str | None


@dataclass(frozen=True, slots=True)
class ApprovedAppRow:
    app_id: str
    display_name: str
    exe_path: str
    exe_sha256: str
    added_at: str


@dataclass(frozen=True, slots=True)
class PendingPowerRow:
    command_id: str
    action: str
    fires_at: str


@dataclass(frozen=True, slots=True)
class SnapshotApplyResult:
    snapshot_id: str
    revoked_controller_ids: tuple[str, ...]
    unknown_controllers: tuple[tuple[str, str], ...]  # (controller_id, kid) listed but never approved locally
    active_controller_ids: tuple[str, ...]


class Store:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)

    @property
    def path(self) -> str:
        return self._path

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----- transactions ------------------------------------------------------------------------
    def _tx(self) -> _Tx:
        return _Tx(self._conn, self._lock)

    # ----- settings ----------------------------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def delete_setting(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get_setting(key)
        if value is None:
            return default
        return value == "1"

    def set_bool(self, key: str, value: bool) -> None:
        self.set_setting(key, "1" if value else "0")

    @property
    def remote_enabled(self) -> bool:
        return self.get_bool(SETTING_REMOTE_ENABLED, False)

    def set_remote_enabled(self, enabled: bool) -> None:
        self.set_bool(SETTING_REMOTE_ENABLED, enabled)
        log.info("remote control %s locally", "enabled" if enabled else "DISABLED")

    @property
    def media_while_locked(self) -> bool:
        return self.get_bool(SETTING_MEDIA_WHILE_LOCKED, False)

    # ----- grants ------------------------------------------------------------------------------
    def add_grant(
        self,
        *,
        controller_id: str | None,
        kid: str,
        public_jwk: dict[str, str],
        capabilities: Iterable[str],
        display_name: str,
        snapshot_id: str | None = None,
    ) -> GrantRow:
        """Written only at local pairing approval. Re-approving a known kid replaces the grant.

        ``controller_id`` is None when the relay has not named the controller yet (``pairing_request``
        carries no controller_id); a provisional id is stored and replaced by the first snapshot that
        lists the kid. Until then no command can bind to the grant (CONTROLLER_MISMATCH)."""
        caps = tuple(dict.fromkeys(capabilities))
        now = format_rfc3339(now_utc())
        if controller_id is None:
            controller_id = PROVISIONAL_PREFIX + kid
        with self._tx():
            self._conn.execute("DELETE FROM grants WHERE kid = ? OR controller_id = ?", (kid, controller_id))
            self._conn.execute(
                "INSERT INTO grants(controller_id, kid, public_jwk, capabilities, display_name, granted_at, "
                "snapshot_id, snapshot_capabilities, snapshot_status) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    controller_id,
                    kid,
                    json.dumps(public_jwk, sort_keys=True),
                    json.dumps(list(caps)),
                    display_name[:64],
                    now,
                    snapshot_id,
                    json.dumps(list(caps)) if snapshot_id else None,
                    "active" if snapshot_id else None,
                ),
            )
        row = self.get_grant_by_kid(kid)
        assert row is not None
        return row

    def get_grant_by_kid(self, kid: str) -> GrantRow | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM grants WHERE kid = ?", (kid,)).fetchone()
        return _grant(row) if row else None

    def get_grant(self, controller_id: str) -> GrantRow | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM grants WHERE controller_id = ?", (controller_id,)).fetchone()
        return _grant(row) if row else None

    def list_grants(self, *, include_revoked: bool = False) -> list[GrantRow]:
        sql = "SELECT * FROM grants" + ("" if include_revoked else " WHERE revoked_at IS NULL") + " ORDER BY granted_at"  # noqa: S608 - fixed literal fragments
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [_grant(r) for r in rows]

    def revoke_grant(self, controller_id: str, reason: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE grants SET revoked_at = ?, revoked_reason = ? WHERE controller_id = ? AND revoked_at IS NULL",
                (format_rfc3339(now_utc()), reason, controller_id),
            )
        return cur.rowcount > 0

    def current_snapshot_id(self) -> str | None:
        return self.get_setting(SETTING_LAST_SNAPSHOT_ID)

    def snapshot_pc_enabled(self) -> bool:
        return self.get_bool(SETTING_PC_ENABLED, True)

    def apply_snapshot(
        self, snapshot_id: str, pc_enabled: bool, controllers: list[dict[str, Any]]
    ) -> SnapshotApplyResult:
        """Intersect the local store with a ``grants_snapshot`` atomically.

        * local, non-revoked controllers absent from the snapshot → revoked (``snapshot_revocation``)
        * listed controllers never approved locally → ignored, reported for a security event
        * listed + local → snapshot capabilities/status recorded (effective = local ∩ snapshot)
        A snapshot can never add a controller or widen a grant.
        """
        listed_by_controller = {c["controller_id"]: c for c in controllers}
        revoked: list[str] = []
        unknown: list[tuple[str, str]] = []
        active: list[str] = []
        now = format_rfc3339(now_utc())
        with self._tx():
            local = {r.controller_id: r for r in self.list_grants(include_revoked=True)}
            by_kid = {r.kid: r for r in local.values()}
            for cid, entry in listed_by_controller.items():
                row = local.get(cid)
                if row is None:
                    provisional = by_kid.get(entry["kid"])
                    if provisional is not None and provisional.controller_id.startswith(PROVISIONAL_PREFIX):
                        self._conn.execute(
                            "UPDATE grants SET controller_id = ? WHERE controller_id = ?",
                            (cid, provisional.controller_id),
                        )
                        del local[provisional.controller_id]
                        row = _grant(
                            self._conn.execute("SELECT * FROM grants WHERE controller_id = ?", (cid,)).fetchone()
                        )
                        local[cid] = row
                if row is None or row.kid != entry["kid"]:
                    unknown.append((cid, entry["kid"]))
                    continue
                if row.revoked:
                    continue  # locally revoked wins; the relay will learn via revoke_controller
                self._conn.execute(
                    "UPDATE grants SET snapshot_id = ?, snapshot_capabilities = ?, snapshot_status = ? WHERE controller_id = ?",
                    (snapshot_id, json.dumps(list(entry["capabilities"])), entry["status"], cid),
                )
                active.append(cid)
            for cid, row in local.items():
                if row.revoked or cid in listed_by_controller:
                    continue
                self._conn.execute(
                    "UPDATE grants SET revoked_at = ?, revoked_reason = 'snapshot_revocation', snapshot_id = ?, "
                    "snapshot_capabilities = NULL, snapshot_status = NULL WHERE controller_id = ?",
                    (now, snapshot_id, cid),
                )
                revoked.append(cid)
            self.set_setting(SETTING_LAST_SNAPSHOT_ID, snapshot_id)
            self.set_bool(SETTING_PC_ENABLED, pc_enabled)
        return SnapshotApplyResult(snapshot_id, tuple(revoked), tuple(unknown), tuple(active))

    # ----- journal -----------------------------------------------------------------------------
    def journal_get(self, command_id: str) -> JournalRow | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM journal WHERE command_id = ?", (command_id,)).fetchone()
        return _journal(row) if row else None

    def journal_insert(
        self, *, command_id: str, digest: str, action: str, controller_id: str, state: str = "created"
    ) -> bool:
        """Insert a new row; returns False when the command_id already exists."""
        with self._tx():
            try:
                self._conn.execute(
                    "INSERT INTO journal(command_id, digest, action, controller_id, state, created_at) VALUES (?,?,?,?,?,?)",
                    (command_id, digest, action, controller_id, state, format_rfc3339(now_utc())),
                )
            except sqlite3.IntegrityError:
                return False
            self._prune_journal()
        return True

    def journal_set_state(
        self,
        command_id: str,
        state: str,
        *,
        frame: dict[str, Any] | None = None,
        error_code: str | None = None,
        sent: bool | None = None,
    ) -> None:
        finished = format_rfc3339(now_utc()) if state in TERMINAL_STATES else None
        sets = ["state = ?"]
        args: list[Any] = [state]
        if frame is not None:
            sets.append("frame_json = ?")
            args.append(json.dumps(frame, separators=(",", ":")))
        if error_code is not None:
            sets.append("error_code = ?")
            args.append(error_code)
        if finished is not None:
            sets.append("finished_at = ?")
            args.append(finished)
        if sent is not None:
            sets.append("sent = ?")
            args.append(1 if sent else 0)
        args.append(command_id)
        with self._lock:
            self._conn.execute(f"UPDATE journal SET {', '.join(sets)} WHERE command_id = ?", args)  # noqa: S608 - column names are literals above

    def journal_mark_sent(self, command_id: str, sent: bool = True) -> None:
        with self._lock:
            self._conn.execute("UPDATE journal SET sent = ? WHERE command_id = ?", (1 if sent else 0, command_id))

    def journal_unsent_terminal(self) -> list[JournalRow]:
        placeholders = ",".join("?" for _ in TERMINAL_STATES)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM journal WHERE sent = 0 AND frame_json IS NOT NULL AND state IN ({placeholders}) ORDER BY seq",  # noqa: S608
                tuple(TERMINAL_STATES),
            ).fetchall()
        return [_journal(r) for r in rows]

    def journal_rows_in_states(self, states: Iterable[str]) -> list[JournalRow]:
        states = tuple(states)
        if not states:
            return []
        placeholders = ",".join("?" for _ in states)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM journal WHERE state IN ({placeholders}) ORDER BY seq",  # noqa: S608 - placeholders only
                states,
            ).fetchall()
        return [_journal(r) for r in rows]

    def journal_count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0])

    def _prune_journal(self) -> None:
        self._conn.execute(
            "DELETE FROM journal WHERE seq NOT IN (SELECT seq FROM journal ORDER BY seq DESC LIMIT ?)",
            (JOURNAL_MAX_ROWS,),
        )

    # ----- challenges --------------------------------------------------------------------------
    def insert_challenge(self, row: ChallengeRow) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO challenges(challenge_id, command_id, controller_id, kid, challenge_text, digest, "
                "target_state_digest, expires_at, consumed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    row.challenge_id,
                    row.command_id,
                    row.controller_id,
                    row.kid,
                    row.challenge_text,
                    row.digest,
                    row.target_state_digest,
                    row.expires_at,
                    row.consumed_at,
                ),
            )

    def get_challenge(self, challenge_id: str) -> ChallengeRow | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM challenges WHERE challenge_id = ?", (challenge_id,)).fetchone()
        return _challenge(row) if row else None

    def get_open_challenge_for_command(self, command_id: str) -> ChallengeRow | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM challenges WHERE command_id = ? AND consumed_at IS NULL ORDER BY expires_at DESC LIMIT 1",
                (command_id,),
            ).fetchone()
        return _challenge(row) if row else None

    def consume_challenge(self, challenge_id: str, *, new_command_state: str, error_code: str | None = None) -> bool:
        """Atomically consume a challenge and move its command to ``new_command_state``.

        Returns False if the challenge was already consumed (single-use rule), in which case the
        command row is left untouched.
        """
        now = format_rfc3339(now_utc())
        with self._tx():
            cur = self._conn.execute(
                "UPDATE challenges SET consumed_at = ? WHERE challenge_id = ? AND consumed_at IS NULL",
                (now, challenge_id),
            )
            if cur.rowcount != 1:
                return False
            row = self._conn.execute(
                "SELECT command_id FROM challenges WHERE challenge_id = ?", (challenge_id,)
            ).fetchone()
            finished = now if new_command_state in TERMINAL_STATES else None
            self._conn.execute(
                "UPDATE journal SET state = ?, error_code = COALESCE(?, error_code), finished_at = COALESCE(?, finished_at) WHERE command_id = ?",
                (new_command_state, error_code, finished, row["command_id"]),
            )
        return True

    def purge_expired_challenges(self, older_than_hours: int = 24) -> int:
        cutoff = format_rfc3339(now_utc() - timedelta(hours=older_than_hours))
        with self._lock:
            cur = self._conn.execute("DELETE FROM challenges WHERE expires_at < ?", (cutoff,))
        return cur.rowcount

    # ----- approved apps -----------------------------------------------------------------------
    def add_approved_app(self, app_id: str, display_name: str, exe_path: str, exe_sha256: str) -> ApprovedAppRow:
        now = format_rfc3339(now_utc())
        with self._lock:
            self._conn.execute(
                "INSERT INTO approved_apps(app_id, display_name, exe_path, exe_sha256, added_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(app_id) DO UPDATE SET display_name = excluded.display_name, exe_path = excluded.exe_path, "
                "exe_sha256 = excluded.exe_sha256, added_at = excluded.added_at",
                (app_id, display_name[:64], exe_path, exe_sha256, now),
            )
        return ApprovedAppRow(app_id, display_name[:64], exe_path, exe_sha256, now)

    def remove_approved_app(self, app_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM approved_apps WHERE app_id = ?", (app_id,))
        return cur.rowcount > 0

    def get_approved_app(self, app_id: str) -> ApprovedAppRow | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM approved_apps WHERE app_id = ?", (app_id,)).fetchone()
        return (
            ApprovedAppRow(row["app_id"], row["display_name"], row["exe_path"], row["exe_sha256"], row["added_at"])
            if row
            else None
        )

    def list_approved_apps(self) -> list[ApprovedAppRow]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM approved_apps ORDER BY app_id").fetchall()
        return [
            ApprovedAppRow(r["app_id"], r["display_name"], r["exe_path"], r["exe_sha256"], r["added_at"]) for r in rows
        ]

    # ----- pending power -----------------------------------------------------------------------
    def set_pending_power(self, command_id: str, action: str, fires_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO pending_power(id, command_id, action, fires_at) VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET command_id = excluded.command_id, action = excluded.action, fires_at = excluded.fires_at",
                (command_id, action, fires_at),
            )

    def get_pending_power(self) -> PendingPowerRow | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM pending_power WHERE id = 1").fetchone()
        return PendingPowerRow(row["command_id"], row["action"], row["fires_at"]) if row else None

    def clear_pending_power(self) -> PendingPowerRow | None:
        with self._tx():
            row = self.get_pending_power()
            self._conn.execute("DELETE FROM pending_power WHERE id = 1")
        return row

    # ----- security events ---------------------------------------------------------------------
    def add_security_event(self, kind: str, **detail: Any) -> None:
        with self._tx():
            self._conn.execute(
                "INSERT INTO security_events(at, kind, detail) VALUES (?,?,?)",
                (format_rfc3339(now_utc()), kind, json.dumps(detail, sort_keys=True, default=str)),
            )
            self._conn.execute(
                "DELETE FROM security_events WHERE id NOT IN (SELECT id FROM security_events ORDER BY id DESC LIMIT ?)",
                (SECURITY_EVENTS_MAX_ROWS,),
            )
        log.warning("security event", kind=kind, **detail)

    def list_security_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM security_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r["id"], "at": r["at"], "kind": r["kind"], "detail": json.loads(r["detail"])} for r in rows]

    # ----- start-up recovery -------------------------------------------------------------------
    def recover_after_restart(self) -> dict[str, list[str]]:
        """Resolve rows left non-terminal by a crash. Called once at start-up, before connecting.

        * ``executing`` → ``outcome_unknown`` (the OS call may or may not have happened); the result
          frame is journaled unsent so it is re-sent as a late correction on reconnect.
        * ``created``/``accepted`` (queued, never executed) → ``failed``/``PC_OFFLINE``; the relay
          already terminated them when the socket dropped, so nothing is re-sent.
        * ``awaiting_confirmation`` → ``expired``/``CONFIRMATION_EXPIRED`` and its challenge consumed
          (the in-memory pending command is gone; the customer starts again).
        """
        from .frames import result_frame  # local import: frames depends on nothing in store

        out: dict[str, list[str]] = {"outcome_unknown": [], "failed_offline": [], "expired_confirmation": []}
        with self._tx():
            for row in self.journal_rows_in_states(["executing"]):
                frame = result_frame(
                    row.command_id,
                    "outcome_unknown",
                    error=("OUTCOME_UNKNOWN", "The PC restarted while running this. It may or may not have happened."),
                    warning="The agent restarted during execution; check the PC's current state before retrying.",
                )
                self.journal_set_state(
                    row.command_id, "outcome_unknown", frame=frame, error_code="OUTCOME_UNKNOWN", sent=False
                )
                out["outcome_unknown"].append(row.command_id)
            for row in self.journal_rows_in_states(["created", "accepted"]):
                frame = result_frame(
                    row.command_id, "failed", error=("PC_OFFLINE", "The PC restarted before running this.")
                )
                self.journal_set_state(row.command_id, "failed", frame=frame, error_code="PC_OFFLINE", sent=True)
                out["failed_offline"].append(row.command_id)
            for row in self.journal_rows_in_states(["awaiting_confirmation"]):
                frame = result_frame(
                    row.command_id,
                    "expired",
                    error=("CONFIRMATION_EXPIRED", "The PC restarted before this was confirmed."),
                )
                self.journal_set_state(
                    row.command_id, "expired", frame=frame, error_code="CONFIRMATION_EXPIRED", sent=False
                )
                self._conn.execute(
                    "UPDATE challenges SET consumed_at = ? WHERE command_id = ? AND consumed_at IS NULL",
                    (format_rfc3339(now_utc()), row.command_id),
                )
                out["expired_confirmation"].append(row.command_id)
            self._conn.execute("DELETE FROM pending_power")
        if any(out.values()):
            log.warning("journal recovered after restart", **{k: len(v) for k, v in out.items()})
        return out

    def summary(self) -> dict[str, Any]:
        with self._lock:
            grants = self._conn.execute("SELECT COUNT(*) AS n FROM grants WHERE revoked_at IS NULL").fetchone()["n"]
            revoked = self._conn.execute("SELECT COUNT(*) AS n FROM grants WHERE revoked_at IS NOT NULL").fetchone()[
                "n"
            ]
            journal = self._conn.execute("SELECT COUNT(*) AS n FROM journal").fetchone()["n"]
            apps = self._conn.execute("SELECT COUNT(*) AS n FROM approved_apps").fetchone()["n"]
        return {
            "remote_enabled": self.remote_enabled,
            "media_while_locked": self.media_while_locked,
            "grants_active": grants,
            "grants_revoked": revoked,
            "journal_rows": journal,
            "approved_apps": apps,
            "snapshot_id": self.current_snapshot_id(),
            "pc_enabled": self.snapshot_pc_enabled(),
        }


class _Tx:
    """Re-entrant explicit transaction (BEGIN IMMEDIATE ... COMMIT/ROLLBACK) under the store lock."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock
        self._outer = False

    def __enter__(self) -> _Tx:
        self._lock.acquire()
        if self._conn.in_transaction:
            self._outer = True
        else:
            self._conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            if not self._outer:
                if exc_type is None:
                    self._conn.execute("COMMIT")
                else:
                    self._conn.execute("ROLLBACK")
        finally:
            self._lock.release()


def _grant(row: sqlite3.Row) -> GrantRow:
    snap_caps = row["snapshot_capabilities"]
    return GrantRow(
        controller_id=row["controller_id"],
        kid=row["kid"],
        public_jwk=json.loads(row["public_jwk"]),
        capabilities=tuple(json.loads(row["capabilities"])),
        display_name=row["display_name"],
        granted_at=row["granted_at"],
        revoked_at=row["revoked_at"],
        revoked_reason=row["revoked_reason"],
        snapshot_id=row["snapshot_id"],
        snapshot_capabilities=tuple(json.loads(snap_caps)) if snap_caps else None,
        snapshot_status=row["snapshot_status"],
    )


def _journal(row: sqlite3.Row) -> JournalRow:
    return JournalRow(
        command_id=row["command_id"],
        digest=row["digest"],
        action=row["action"],
        controller_id=row["controller_id"],
        state=row["state"],
        frame=json.loads(row["frame_json"]) if row["frame_json"] else None,
        error_code=row["error_code"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        sent=bool(row["sent"]),
    )


def _challenge(row: sqlite3.Row) -> ChallengeRow:
    return ChallengeRow(
        challenge_id=row["challenge_id"],
        command_id=row["command_id"],
        controller_id=row["controller_id"],
        kid=row["kid"],
        challenge_text=row["challenge_text"],
        digest=row["digest"],
        target_state_digest=row["target_state_digest"],
        expires_at=row["expires_at"],
        consumed_at=row["consumed_at"],
    )


def is_expired(timestamp: str, *, skew_seconds: int = 0) -> bool:
    return parse_rfc3339(timestamp) < now_utc() - timedelta(seconds=skew_seconds)
