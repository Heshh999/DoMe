"""Action registry: loads ``shared/protocol/actions.json`` and validates params/targets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .errors import ProtocolError
from .strict_regex import StrictValidator

CONTRACT_DIR_CANDIDATES = (
    Path(__file__).resolve().parent / "_contract",  # installed wheel
    Path(__file__).resolve().parents[2] / "protocol",  # monorepo checkout
)


def contract_dir() -> Path:
    for candidate in CONTRACT_DIR_CANDIDATES:
        if (candidate / "actions.json").exists():
            return candidate
    raise FileNotFoundError("shared/protocol contract directory not found")


def _load_json(name: str) -> Any:
    with (contract_dir() / name).open("r", encoding="utf-8") as fh:
        return json.load(fh)


@dataclass(frozen=True, slots=True)
class ActionSpec:
    name: str
    summary: str
    params_schema: dict[str, Any]
    target_schema: dict[str, Any] | None
    target_name: str | None
    result_name: str
    capability: str
    risk: str
    confirmation: str
    timeout_ms: int
    availability: tuple[str, ...]
    verification: str
    coalesce: str | None
    idempotent: bool
    routine_allowed: bool

    @property
    def requires_confirmation(self) -> bool:
        return self.confirmation == "challenge"


class Registry:
    def __init__(self, raw: dict[str, Any], errors: dict[str, Any], version: dict[str, Any], plans: dict[str, Any]):
        self.registry_version: str = raw["registry_version"]
        self.capabilities: dict[str, str] = dict(raw["capabilities"])
        self.availability_conditions: dict[str, str] = dict(raw["availability_conditions"])
        self.verification_strategies: dict[str, str] = dict(raw["verification_strategies"])
        self._target_schemas: dict[str, dict[str, Any]] = raw["target_schemas"]
        self._actions: dict[str, ActionSpec] = {}
        self._param_validators: dict[str, StrictValidator] = {}
        self._target_validators: dict[str, StrictValidator] = {
            name: StrictValidator(schema) for name, schema in self._target_schemas.items()
        }
        for name, spec in raw["actions"].items():
            target_name = spec.get("target")
            if target_name is not None and target_name not in self._target_schemas:
                raise ValueError(f"action {name} references unknown target schema {target_name}")
            if spec["capability"] not in self.capabilities:
                raise ValueError(f"action {name} references unknown capability {spec['capability']}")
            action = ActionSpec(
                name=name,
                summary=spec["summary"],
                params_schema=spec["params"],
                target_schema=self._target_schemas.get(target_name) if target_name else None,
                target_name=target_name,
                result_name=spec["result"],
                capability=spec["capability"],
                risk=spec["risk"],
                confirmation=spec["confirmation"],
                timeout_ms=int(spec["timeout_ms"]),
                availability=tuple(spec.get("availability", ())),
                verification=spec["verification"],
                coalesce=spec.get("coalesce"),
                idempotent=bool(spec["idempotent"]),
                routine_allowed=bool(spec["routine_allowed"]),
            )
            if action.risk == "disruptive" and action.confirmation != "challenge":
                raise ValueError(f"disruptive action {name} must require a challenge")
            if action.routine_allowed and action.confirmation != "none":
                raise ValueError(f"routine-allowed action {name} must not require confirmation")
            self._actions[name] = action
            Draft202012Validator.check_schema(action.params_schema)
            self._param_validators[name] = StrictValidator(action.params_schema)
        self.errors: dict[str, dict[str, Any]] = errors["errors"]
        self.protocol_version: str = version["protocol_version"]
        self.limits: dict[str, int] = dict(version["limits"])
        self.signing_algorithms: tuple[str, ...] = tuple(version["signing_algorithms"])
        self.plans: dict[str, Any] = plans

    # ----- lookups -------------------------------------------------------------------------
    @property
    def actions(self) -> dict[str, ActionSpec]:
        return dict(self._actions)

    def get(self, name: str) -> ActionSpec:
        spec = self._actions.get(name)
        if spec is None:
            raise ProtocolError("UNKNOWN_ACTION", f"unknown action {name!r}")
        return spec

    def error_defaults(self, code: str) -> dict[str, Any]:
        return self.errors.get(code) or self.errors["INTERNAL"]

    def make_error(self, code: str, message: str | None = None, *, retryable: bool | None = None, **detail: Any) -> ProtocolError:
        defaults = self.error_defaults(code)
        return ProtocolError(
            code if code in self.errors else "INTERNAL",
            message or defaults["user_message"],
            retryable=defaults["retryable"] if retryable is None else retryable,
            detail=detail,
        )

    # ----- validation ----------------------------------------------------------------------
    def validate_params(self, action: str, params: Any) -> dict[str, Any]:
        spec = self.get(action)
        if not isinstance(params, dict):
            raise ProtocolError("INVALID_PARAMETERS", "params must be an object")
        try:
            self._param_validators[action].validate(params)
        except ValidationError as exc:
            raise ProtocolError("INVALID_PARAMETERS", _short(exc)) from None
        # apply schema defaults so executors see complete params
        out = dict(params)
        for key, prop in spec.params_schema.get("properties", {}).items():
            if key not in out and "default" in prop:
                out[key] = prop["default"]
        return out

    def validate_target(self, action: str, target: Any) -> dict[str, Any] | None:
        spec = self.get(action)
        if spec.target_name is None:
            if target is not None:
                raise ProtocolError("INVALID_PARAMETERS", "this action does not take a target")
            return None
        if not isinstance(target, dict):
            raise ProtocolError("TARGET_REQUIRED", "this action requires a target")
        try:
            self._target_validators[spec.target_name].validate(target)
        except ValidationError as exc:
            raise ProtocolError("INVALID_PARAMETERS", f"target: {_short(exc)}") from None
        return dict(target)

    def validate_result(self, action: str, result: Any) -> dict[str, Any]:
        """Validate an action's result object against its declared result schema."""
        from .schemas import (
            load_schemas,  # local import: schemas depends on registry for contract_dir
        )

        spec = self.get(action)
        if not isinstance(result, dict):
            raise ProtocolError("MALFORMED_MESSAGE", "result must be an object")
        load_schemas().validate_result(spec.result_name, result)
        return dict(result)

    def plan(self, plan_id: str) -> dict[str, Any]:
        plans = self.plans["plans"]
        if plan_id not in plans:
            raise KeyError(plan_id)
        return dict(plans[plan_id])


def _short(exc: ValidationError) -> str:
    path = "/".join(str(p) for p in exc.absolute_path)
    return f"{path or '<root>'}: {exc.message}"[:300]


@lru_cache(maxsize=1)
def load_registry() -> Registry:
    return Registry(_load_json("actions.json"), _load_json("errors.json"), _load_json("version.json"), _load_json("plans.json"))
