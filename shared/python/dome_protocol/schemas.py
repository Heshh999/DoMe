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
            registry = registry.with_resource(f"{BASE}{name}.schema.json", Resource.from_contents(doc, default_specification=DRAFT202012))
        self._registry = registry
        for doc in self._docs.values():
            Draft202012Validator.check_schema(doc)
        self._validators: dict[str, Draft202012Validator] = {}

    def _validator(self, doc_name: str, pointer: str | None) -> Draft202012Validator:
        key = f"{doc_name}#{pointer or ''}"
        v = self._validators.get(key)
        if v is None:
            schema: dict[str, Any] = {"$ref": f"{BASE}{doc_name}.schema.json" + (f"#{pointer}" if pointer else "")}
            v = Draft202012Validator(schema, registry=self._registry)
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

    def validate_def(self, doc_name: str, def_name: str, value: Any) -> None:
        self._validate(doc_name, f"/$defs/{def_name}", value)

    def document(self, name: str) -> dict[str, Any]:
        return self._docs[name]


@lru_cache(maxsize=1)
def load_schemas() -> Schemas:
    return Schemas()
