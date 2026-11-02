"""The wire contract: the API's OpenAPI description, with the server-sent events added, and
the TypeScript types the UI is built against, generated from it.

After changing a response or an event, regenerate the UI's types with `make api-types`;
a test fails until they match.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic.json_schema import models_json_schema

from creeparr import __version__
from creeparr.core.server_events import EVENTS

_REF = "#/components/schemas/"


def schema_ts() -> Path:
    """Where the UI's generated types live, in a checkout of the repo."""
    return Path(__file__).resolve().parents[3] / "frontend" / "src" / "api" / "schema.ts"


def openapi() -> dict[str, Any]:
    """The API's description, as `/openapi.json` serves it."""
    from creeparr.api.router import api_router

    app = FastAPI(title="Creeparr", version=__version__)
    app.include_router(api_router)
    return add_events(app.openapi())


def add_events(spec: dict[str, Any]) -> dict[str, Any]:
    """Add each server-sent event's payload to `spec`'s schemas, and `x-server-events`: each
    event's name and the schema of its payload."""
    refs, defs = models_json_schema(
        [(model, "serialization") for model in EVENTS.values()], ref_template=_REF + "{model}"
    )
    schemas = spec.setdefault("components", {}).setdefault("schemas", {})
    for name, schema in defs.get("$defs", {}).items():
        schemas.setdefault(name, schema)
    spec["x-server-events"] = {
        name: refs[(model, "serialization")]["$ref"] for name, model in EVENTS.items()
    }
    return spec


def serve_events_in_openapi(app: FastAPI) -> None:
    """Make `app`'s `/openapi.json` describe the server-sent events too."""
    build = app.openapi

    def described() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = add_events(build())
        return app.openapi_schema

    app.openapi = described  # type: ignore[method-assign]


# ---- TypeScript -------------------------------------------------------------------------


def typescript(spec: dict[str, Any]) -> str:
    """Every schema in `spec` as a TypeScript type, and `ServerEvents`: each event's name and
    its payload type."""
    schemas: dict[str, Any] = spec["components"]["schemas"]
    out = [
        "// Generated from the backend's API description by `make api-types`. Do not edit;",
        "// change the pydantic models in backend/creeparr and regenerate.",
        "",
    ]
    for name in sorted(schemas):
        out.append(_declaration(_name(name), schemas[name]))
    events = spec.get("x-server-events", {})
    out.append("/** Each server-sent event's name and its payload. */")
    out.append("export interface ServerEvents {")
    for event, ref in events.items():
        out.append(f"  {json.dumps(event)}: {_name(ref.removeprefix(_REF))};")
    out.append("}")
    return "\n".join(out) + "\n"


def _name(schema_name: str) -> str:
    return re.sub(r"\W", "_", schema_name).strip("_")


def _doc(schema: dict[str, Any], indent: str = "") -> str:
    text = schema.get("description")
    if not text:
        return ""
    lines = [line.rstrip() for line in text.strip().splitlines()]
    if len(lines) == 1:
        return f"{indent}/** {lines[0]} */\n"
    body = "\n".join(f"{indent} *{' ' + line if line else ''}" for line in lines)
    return f"{indent}/**\n{body}\n{indent} */\n"


def _declaration(name: str, schema: dict[str, Any]) -> str:
    doc = _doc(schema)
    if schema.get("type") == "object" and schema.get("properties") == {}:
        return f"{doc}export type {name} = Record<string, never>;\n"
    if schema.get("type") == "object" and "properties" in schema:
        return f"{doc}export interface {name} {_object(schema, '')}\n"
    return f"{doc}export type {name} = {_type(schema, '')};\n"


def _object(schema: dict[str, Any], indent: str) -> str:
    inner = indent + "  "
    required = set(schema.get("required", []))
    lines = ["{"]
    for prop, sub in schema.get("properties", {}).items():
        key = prop if re.fullmatch(r"[A-Za-z_$][\w$]*", prop) else json.dumps(prop)
        optional = "" if prop in required else "?"
        lines.append(f"{_doc(sub, inner)}{inner}{key}{optional}: {_type(sub, inner)};")
    extra = schema.get("additionalProperties")
    if extra:
        value = _type(extra, inner) if isinstance(extra, dict) and extra else "unknown"
        lines.append(f"{inner}[key: string]: {value};")
    lines.append(indent + "}")
    return "\n".join(lines)


def _type(schema: dict[str, Any], indent: str) -> str:
    if "$ref" in schema:
        return _name(schema["$ref"].removeprefix(_REF))
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"])
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return " | ".join(_member(s, indent) for s in schema[key])
    if "allOf" in schema and len(schema["allOf"]) == 1:
        return _type(schema["allOf"][0], indent)
    kind = schema.get("type")
    if kind == "object":
        if "properties" in schema:
            return _object(schema, indent)
        extra = schema.get("additionalProperties")
        value = _type(extra, indent) if isinstance(extra, dict) and extra else "unknown"
        return f"{{ [key: string]: {value} }}"
    if kind == "array":
        return f"{_member(schema.get('items', {}), indent)}[]"
    return {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(kind, "unknown")


def _member(schema: dict[str, Any], indent: str) -> str:
    """A type inside a union or an array, parenthesised when it is a union itself."""
    text = _type(schema, indent)
    return f"({text})" if " | " in text and not text.startswith("{") else text


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else schema_ts()
    target.write_text(typescript(openapi()))
    print(f"wrote {target}")
