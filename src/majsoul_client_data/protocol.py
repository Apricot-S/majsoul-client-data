import re
from dataclasses import dataclass
from typing import Any

from .artifacts import OutputValue
from .lua import MAX_DEPTH, Reader, Reference, require, string_value

FIELD_MESSAGE = 11
FIELD_ENUM = 14
LABEL_REPEATED = 3
RESERVED_MIN = 19000
RESERVED_MAX = 19999

SCALARS = {
    1: "double",
    2: "float",
    3: "int64",
    4: "uint64",
    5: "int32",
    6: "fixed64",
    7: "fixed32",
    8: "bool",
    9: "string",
    12: "bytes",
    13: "uint32",
    15: "sfixed32",
    16: "sfixed64",
    17: "sint32",
    18: "sint64",
}
KINDS = {
    "Descriptor": "message",
    "EnumDescriptor": "enum",
    "EnumValueDescriptor": "value",
    "FieldDescriptor": "field",
}
PROPS = {
    "name",
    "full_name",
    "number",
    "index",
    "label",
    "type",
    "message_type",
    "enum_type",
    "containing_type",
    "fields",
    "nested_types",
    "enum_types",
    "values",
}
NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
FULL_NAME = re.compile(
    r"\.?[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*\Z"
)


@dataclass(frozen=True)
class ParsedModule:
    objects: dict[str, dict[str, Any]]
    imports: dict[str, str]


@dataclass(frozen=True)
class RPCType:
    full_name: str
    file_name: str


type TypeIndex = dict[str, list[RPCType]]


@dataclass(frozen=True)
class RPCResolution:
    spec: dict[str, str]
    resolved: dict[str, str]
    missing: list[str]
    imports: set[str]
    packages: set[str]


def _read_module(source: str) -> ParsedModule:
    reader = Reader(source)
    tokens = reader.tokens
    objects: dict[str, dict[str, Any]] = {}
    imports: dict[str, str] = {}
    for index in range(len(tokens) - 6):
        if (
            tokens[index + 1] == "="
            and tokens[index + 3] == "."
            and tokens[index + 4] in KINDS
            and tokens[index + 5 : index + 7] == ["(", ")"]
        ):
            name = tokens[index]
            require(
                name not in objects, f"Duplicate protobuf descriptor: {name}"
            )
            objects[name] = {"kind": KINDS[tokens[index + 4]]}
    for index in range(len(tokens) - 4):
        if tokens[index] == "local" and tokens[index + 2 : index + 4] == [
            "=",
            "require",
        ]:
            position = index + 4 + (tokens[index + 4] == "(")
            require(position < len(tokens), "Incomplete protobuf import")
            if tokens[position].startswith(('"', "'")):
                module = string_value(tokens[position])
                if module.startswith("Protol."):
                    imports[tokens[index + 1]] = module
    for index in range(len(tokens) - 4):
        if (
            tokens[index] in objects
            and tokens[index + 1] == "."
            and tokens[index + 2] in PROPS
            and tokens[index + 3] == "="
        ):
            key = tokens[index + 2]
            require(
                key not in objects[tokens[index]],
                f"Duplicate protobuf property: {key}",
            )
            objects[tokens[index]][key] = reader.value_at(index + 4)
    require(bool(objects) or bool(imports), "No protobuf descriptors found")
    return ParsedModule(objects, imports)


def _identifier(value: Any, *, full: bool = False) -> str:  # noqa: ANN401
    pattern = FULL_NAME if full else NAME
    require(
        isinstance(value, str) and bool(pattern.fullmatch(value)),
        "Invalid protobuf identifier",
    )
    return value


def _refs(item: dict, key: str, objects: dict, kind: str) -> list[str]:
    refs = item.get(key, [])
    require(isinstance(refs, list), f"Invalid protobuf {key}")
    result = []
    for ref in refs:
        require(
            isinstance(ref, Reference)
            and ref.index is None
            and ref.name in objects
            and objects[ref.name]["kind"] == kind,
            f"Invalid protobuf {key} reference",
        )
        result.append(ref.name)
    require(
        len(set(result)) == len(result), f"Duplicate protobuf {key} reference"
    )
    return result


def _file_name(module: str) -> str:
    return (
        _identifier(module.removeprefix("Protol.").removesuffix("_pb"))
        + ".proto"
    )


def _resolve(
    ref: object, module: str, parsed: dict[str, ParsedModule], kind: str
) -> tuple[str, str]:
    if not isinstance(ref, Reference) or ref.index is not None:
        msg = "Missing protobuf type reference"
        raise ValueError(msg)

    objects = parsed[module].objects
    imports = parsed[module].imports
    target = module
    name = ref.name
    if "." in name:
        alias, name = name.split(".", 1)
        require(alias in imports, f"Unknown protobuf import: {alias}")
        target = imports[alias]
        require(target in parsed, f"Missing protobuf module: {target}")
        objects = parsed[target].objects
    require(
        name in objects and objects[name]["kind"] == kind,
        f"Unresolved protobuf type: {ref.name}",
    )
    return objects[name]["full_name"], target


def _field(
    field: dict, module: str, parsed: dict[str, ParsedModule], used: set[str]
) -> dict:
    number = field.get("number")
    require(
        type(number) is int
        and 0 < number < 2**29
        and not RESERVED_MIN <= number <= RESERVED_MAX,
        "Invalid protobuf field number",
    )
    require(
        type(field.get("label")) is int and field["label"] in {1, 3},
        "Unsupported protobuf field label",
    )
    code = field.get("type")
    require(
        type(code) is int and code in {*SCALARS, FIELD_MESSAGE, FIELD_ENUM},
        "Unsupported protobuf field type",
    )
    field_type = SCALARS.get(code)
    if code in {FIELD_MESSAGE, FIELD_ENUM}:
        field_type, target = _resolve(
            field.get(
                "message_type" if code == FIELD_MESSAGE else "enum_type"
            ),
            module,
            parsed,
            "message" if code == FIELD_MESSAGE else "enum",
        )
        if target != module:
            used.add(target)
    return {
        "name": field["name"],
        "number": number,
        "type": field_type,
        "type_code": code,
        "label": "repeated"
        if field["label"] == LABEL_REPEATED
        else "optional",
    }


def _parents(objects: dict) -> dict:
    parents = {}
    for var, item in objects.items():
        _identifier(item.get("name"))
        if item["kind"] != "value":
            _identifier(item.get("full_name"), full=True)
        if item["kind"] == "message":
            for key, kind in (
                ("nested_types", "message"),
                ("enum_types", "enum"),
            ):
                for child in _refs(item, key, objects, kind):
                    require(
                        child not in parents,
                        "Protobuf type has multiple parents",
                    )
                    parents[child] = var
    return parents


def _enum_values(item: dict, objects: dict, consumed: set[str]) -> list[dict]:
    values = []
    for value_var in _refs(item, "values", objects, "value"):
        value = objects[value_var]
        require(
            type(value.get("number")) is int
            and -(2**31) <= value["number"] < 2**31,
            "Invalid protobuf enum number",
        )
        require(
            type(value.get("index")) is int and value["index"] >= 0,
            "Invalid protobuf enum index",
        )
        values.append(
            {
                "name": value["name"],
                "number": value["number"],
                "index": value["index"],
            }
        )
        consumed.add(value_var)
    values.sort(key=lambda value: value["index"])
    require(
        bool(values) and values[0]["number"] == 0,
        "Proto3 enum must start with zero",
    )
    require(
        len({value["name"] for value in values}) == len(values),
        "Duplicate protobuf enum name",
    )
    return values


def _build(module: str, parsed: dict[str, ParsedModule]) -> dict:
    objects = parsed[module].objects
    if not objects:
        return {
            "name": module,
            "package": "",
            "messages": [],
            "enums": [],
            "imports": [],
        }
    used = set()
    parents = _parents(objects)
    consumed = set()
    active = set()

    def build(var: str) -> dict:
        require(var not in active, "Cyclic protobuf nesting")
        require(len(active) < MAX_DEPTH, "Protobuf nesting limit exceeded")
        active.add(var)
        consumed.add(var)
        item = objects[var]
        result = {"name": item["name"], "full_name": item["full_name"]}
        if item["kind"] == "enum":
            result["values"] = _enum_values(item, objects, consumed)
        else:
            fields = []
            for field_var in _refs(item, "fields", objects, "field"):
                field = objects[field_var]
                require(
                    field_var not in consumed,
                    "Protobuf field has multiple parents",
                )
                consumed.add(field_var)
                fields.append(_field(field, module, parsed, used))
            require(
                len({f["number"] for f in fields}) == len(fields)
                and len({f["name"] for f in fields}) == len(fields),
                "Duplicate protobuf field",
            )
            result["fields"] = sorted(
                fields, key=lambda field: field["number"]
            )
            result["messages"] = [
                build(child)
                for child in _refs(item, "nested_types", objects, "message")
            ]
            result["enums"] = [
                build(child)
                for child in _refs(item, "enum_types", objects, "enum")
            ]
        active.remove(var)
        return result

    roots = [
        var
        for var, item in objects.items()
        if item["kind"] in {"message", "enum"} and var not in parents
    ]
    messages = [
        build(var) for var in roots if objects[var]["kind"] == "message"
    ]
    enums = [build(var) for var in roots if objects[var]["kind"] == "enum"]
    require(consumed == set(objects), "Unattached protobuf descriptor")
    packages = {
        objects[var]["full_name"].lstrip(".").rsplit(".", 1)[0]
        for var in roots
    }
    require(len(packages) == 1, "Inconsistent protobuf packages")
    return {
        "name": module,
        "package": packages.pop(),
        "messages": messages,
        "enums": enums,
        "imports": sorted(_file_name(name) for name in used),
    }


def _render_type(item: dict, kind: str, indent: int = 0) -> list[str]:
    prefix = " " * indent
    inner = prefix + "  "
    lines = [f"{prefix}{kind} {item['name']} {{"]
    if kind == "enum":
        values = item["values"]
        if len({value["number"] for value in values}) < len(values):
            lines.append(inner + "option allow_alias = true;")
        lines.extend(
            f"{inner}{value['name']} = {value['number']};" for value in values
        )
    else:
        for child in item["enums"]:
            lines.extend(_render_type(child, "enum", indent + 2))
        for child in item["messages"]:
            lines.extend(_render_type(child, "message", indent + 2))
        for field in item["fields"]:
            label = "repeated " if field["label"] == "repeated" else ""
            lines.append(
                f"{inner}{label}{field['type']} "
                f"{field['name']} = {field['number']};"
            )
    lines.append(prefix + "}")
    return lines


def _render_module(module: dict) -> str:
    lines = ['syntax = "proto3";']
    if module["package"]:
        lines.append(f"package {module['package']};")
    lines.extend(f'import "{name}";' for name in module["imports"])
    for kind, key in (("enum", "enums"), ("message", "messages")):
        for item in module[key]:
            lines.extend(["", *_render_type(item, kind)])
    return "\n".join(lines) + "\n"


def _counts(messages: list[dict]) -> tuple[int, int, int]:
    total, fields, enums = 0, 0, 0
    for message in messages:
        nested, nested_fields, nested_enums = _counts(message["messages"])
        total += 1 + nested
        fields += len(message["fields"]) + nested_fields
        enums += len(message["enums"]) + nested_enums
    return total, fields, enums


def _resolve_rpc(spec: dict, type_index: TypeIndex) -> RPCResolution:
    names = {
        direction: _identifier(spec.get(direction), full=True)
        for direction in ("request", "response")
    }
    resolved = {}
    missing = []
    imports = set()
    packages = set()
    for direction, name in names.items():
        if name not in type_index:
            missing.append(name)
            continue

        require(len(type_index[name]) == 1, f"Ambiguous RPC type: {name}")
        target = type_index[name][0]
        resolved[direction] = target.full_name
        imports.add(target.file_name)
        packages.add(target.full_name.lstrip(".").rsplit(".", 1)[0])
    return RPCResolution(names, resolved, missing, imports, packages)


def _services(
    config: dict, type_index: TypeIndex
) -> tuple[dict[str, OutputValue], dict[str, int]]:
    require(
        isinstance(config, dict) and isinstance(config.get("service"), dict),
        "Invalid RPC service config",
    )
    services = {}
    unresolved = []
    counts = {
        "services": 0,
        "rpcs": 0,
        "resolved_rpcs": 0,
        "unresolved_rpcs": 0,
    }
    imports = set()
    packages = set()
    lines = []
    for service, methods in sorted(config["service"].items()):
        _identifier(service)
        require(
            isinstance(methods, dict) and bool(methods), "Invalid RPC methods"
        )
        services[service] = {}
        lines.append(f"service {service} {{")

        for method, spec in sorted(methods.items()):
            _identifier(method)
            require(isinstance(spec, dict), "Invalid RPC method")
            rpc = _resolve_rpc(spec, type_index)
            services[service][method] = rpc.spec
            counts["rpcs"] += 1
            # Partial resolutions affect imports and package checks too.
            imports.update(rpc.imports)
            packages.update(rpc.packages)
            if rpc.missing:
                unresolved.append(
                    {
                        "service": service,
                        "method": method,
                        **rpc.spec,
                        "missing_types": rpc.missing,
                    }
                )
                counts["unresolved_rpcs"] += 1
                continue

            lines.append(
                f"  rpc {method} ({rpc.resolved['request']}) "
                f"returns ({rpc.resolved['response']});"
            )
            counts["resolved_rpcs"] += 1

        lines.append("}")
        counts["services"] += 1

    files: dict[str, OutputValue] = {}
    if counts["resolved_rpcs"]:
        require(len(packages) == 1, "Inconsistent RPC packages")
        files["proto/services.proto"] = (
            "\n".join(
                [
                    'syntax = "proto3";',
                    f"package {packages.pop()};",
                    *(f'import "{name}";' for name in sorted(imports)),
                    "",
                    *lines,
                ]
            )
            + "\n"
        )
    files["services.json"] = services
    files["unresolved_rpcs.json"] = unresolved
    return files, counts


def convert_protocol(
    sources: dict[str, str], config: dict
) -> tuple[dict[str, OutputValue], dict[str, int]]:
    require(bool(sources), "Missing protocol modules")
    parsed = {
        name: _read_module(source) for name, source in sorted(sources.items())
    }
    modules = [_build(name, parsed) for name in parsed]
    require(
        len({_file_name(name).casefold() for name in parsed}) == len(parsed),
        "Duplicate protobuf file name",
    )
    files: dict[str, OutputValue] = {
        "proto/" + _file_name(module["name"]): _render_module(module)
        for module in modules
    }
    counts = {
        "modules": len(modules),
        "messages": 0,
        "fields": 0,
        "enums": 0,
        "services": 0,
        "rpcs": 0,
        "resolved_rpcs": 0,
        "unresolved_rpcs": 0,
    }
    type_index: TypeIndex = {}
    for module, descriptors in parsed.items():
        for item in descriptors.objects.values():
            if item["kind"] == "message":
                for key in {
                    item["name"],
                    item["full_name"],
                    item["full_name"].lstrip("."),
                }:
                    type_index.setdefault(key, []).append(
                        RPCType(item["full_name"], _file_name(module))
                    )
    for module in modules:
        messages, fields, enums = _counts(module["messages"])
        counts["messages"] += messages
        counts["fields"] += fields
        counts["enums"] += enums + len(module["enums"])
    service_files, service_counts = _services(config, type_index)
    files.update(service_files)
    counts.update(service_counts)
    files["schema.json"] = {"modules": modules, "counts": counts}
    return files, counts
