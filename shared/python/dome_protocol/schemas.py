"""JSON Schema validators for payloads, relay frames and bridge frames, resolved across files."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry as RefRegistry
from referencing import Resource
from referencing.jsonschema import DRAFT202012

from .errors import ProtocolError
from .registry import contract_dir
from .strict_regex import StrictValidator

SCHEMA_FILES = ("envelope", "command", "confirmation", "relay-frames", "bridge")
BASE = "https://dome.app/schemas/"


class Schemas:
    def __init__(self) -> None:
        schema_dir = contract_dir() / "schemas"
        self._docs: dict[str, dict[str, Any]] = {}
        registry = RefRegistry()
        for name in SCHEMA_FILES:
            with (schema_dir / f"{name}.schema.json").open("r", encoding="utf-8") as fh:
                doc = json.load(fh)
            self._docs[name] = doc
            # jsonschema's `evolve()` re-selects the validator class from a subschema's `$schema`
            # keyword, which would silently swap our strict `pattern` implementation for the stock
            # one when following a `$ref` into another document. Register copies without `$schema`.
            resource_doc = {k: v for k, v in doc.items() if k != "$schema"}
            registry = registry.with_resource(f"{BASE}{name}.schema.json", Resource.from_contents(resource_doc, default_specification=DRAFT202012))
        self._registry = registry
        for doc in self._docs.values():
            Draft202012Validator.check_schema(doc)
        self._validators: dict[str, StrictValidator] = {}

    def _validator(self, doc_name: str, pointer: str | None) -> StrictValidator:
        key = f"{doc_name}#{pointer or ''}"
        v = self._validators.get(key)
        if v is None:
            schema: dict[str, Any] = {"$ref": f"{BASE}{doc_name}.schema.json" + (f"#{pointer}" if pointer else "")}
            v = StrictValidator(schema, registry=self._registry)
            self._validators[key] = v
        return v

    def _validate(self, doc_name: str, pointer: str | None, value: Any, code: str = "MALFORMED_MESSAGE") -> None:
        try:
            self._validator(doc_name, pointer).validate(value)
        except ValidationError as exc:
            path = "/".join(str(p) for p in exc.absolute_path)
            raise ProtocolError(code, f"{path or '<root>'}: {exc.message}"[:300]) from None

    # ----- payloads --------------------------------------------------------------------------
    def validate_envelope_shape(self, value: Any) -> None:
        self._validate("envelope", None, value)

    def validate_command_payload(self, value: Any) -> None:
        self._validate("command", None, value)

    def validate_confirmation_payload(self, value: Any) -> None:
        self._validate("confirmation", None, value)

    # ----- frames ----------------------------------------------------------------------------
    def validate_frame(self, direction: str, value: Any) -> None:
        if direction not in ("controller_to_relay", "relay_to_controller", "agent_to_relay", "relay_to_agent"):
            raise ValueError(direction)
        self._validate("relay-frames", f"/$defs/{direction}", value)

    def validate_bridge_frame(self, direction: str, value: Any) -> None:
        if direction not in ("extension_to_agent", "agent_to_extension"):
            raise ValueError(direction)
        self._validate("bridge", f"/$defs/{direction}", value)

    def validate_challenge_text(self, challenge_text: str, *, max_bytes: int = 4096) -> dict[str, Any]:
        """Strict-parse a copy of challenge_text and validate it against #/$defs/challenge.

        Callers must keep and forward the ORIGINAL string; the returned object is for display and
        checks only.
        """
        from .strict_json import loads_strict

        parsed = loads_strict(challenge_text, max_bytes=max_bytes, require_object=True)
        self._validate("relay-frames", "/$defs/challenge", parsed)
        return parsed

    def validate_def(self, doc_name: str, def_name: str, value: Any) -> None:
        self._validate(doc_name, f"/$defs/{def_name}", value)

    def document(self, name: str) -> dict[str, Any]:
        return self._docs[name]


@lru_cache(maxsize=1)
def load_schemas() -> Schemas:
    return Schemas()
