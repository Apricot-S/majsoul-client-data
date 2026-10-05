import re
from dataclasses import dataclass
from typing import Any

from .lua import (
    IDENT,
    Call,
    LuaValue,
    Reader,
    Reference,
    local_tables,
    require,
    string_value,
)

POSITION_BASE = 1000
GET_ARGS = 4
PACK_ARGS = 2
MAX_COLUMNS = 4096
MAX_ROWS = 1_000_000
MAX_CELLS = 8_000_000

LOCALES = ("chs", "chs_t", "jp", "en", "kr")
NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")


@dataclass(frozen=True)
class Sheet:
    fields: dict[str, int]
    defaults: list[LuaValue]


def _aliases(reader: Reader) -> dict[str, str]:
    tokens = reader.tokens
    return {
        tokens[i + 1]: tokens[i + 5]
        for i in range(len(tokens) - 5)
        if tokens[i] == "local"
        and tokens[i + 2] == "="
        and tokens[i + 4] == "."
    }


def _schema(reader: Reader, tables: dict) -> Sheet | None:
    for index, lexeme in enumerate(reader.tokens[:-2]):
        if lexeme != "return" or reader.tokens[index + 2] != "(":
            continue
        call = reader.value_at(index + 1)
        if not isinstance(call, Call) or len(call.args) < GET_ARGS:
            continue
        fields, defaults = call.args[2:4]
        if not isinstance(fields, Reference) or not isinstance(
            defaults, Reference
        ):
            continue
        fields, defaults = tables.get(fields.name), tables.get(defaults.name)
        if isinstance(fields, dict) and isinstance(defaults, list):
            require(bool(fields), "Empty table schema")
            require(
                max(len(fields), len(defaults)) <= MAX_COLUMNS,
                "Table column limit exceeded",
            )
            require(
                all(
                    isinstance(k, str) and type(v) is int and v != 0
                    for k, v in fields.items()
                ),
                "Invalid table schema",
            )
            return Sheet(fields, defaults)
    return None


def _position(value: int) -> int:
    return (
        abs(value) % POSITION_BASE
        if abs(value) >= POSITION_BASE
        else abs(value)
    )


def _json_value(value: Any, aliases: dict) -> Any:  # noqa: ANN401
    if isinstance(value, Call):
        require(
            aliases.get(value.name) == "Sub" and len(value.args) == 1,
            f"Unsupported table call: {value.name}",
        )
        return _json_value(value.args[0], aliases)
    require(not isinstance(value, Reference), "Unsupported table reference")
    if isinstance(value, list):
        return [_json_value(item, aliases) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _json_value(item, aliases) for key, item in value.items()
        }
    return value


def _row(call: Call, sheet: Sheet, aliases: dict) -> dict:
    if aliases.get(call.name) != "Pack" or len(call.args) != PACK_ARGS:
        msg = "Unsupported packed row"
        raise ValueError(msg)
    packed = call.args[0]
    if not isinstance(packed, list):
        msg = "Unsupported packed row"
        raise ValueError(msg)  # noqa: TRY004 - malformed packed row

    positions = {key: _position(value) for key, value in sheet.fields.items()}
    require(
        all(0 < value <= len(sheet.defaults) for value in positions.values()),
        "Invalid schema default positions",
    )
    values = list(sheet.defaults)
    skipped = set()
    cursor = 1
    for item in packed:
        if isinstance(item, Reference):
            if aliases.get(item.name) != "Dmap" or type(item.index) is not int:
                msg = "Unsupported row reference"
                raise ValueError(msg)
            start, end = divmod(item.index, POSITION_BASE)
            require(0 < start <= end <= len(values), "Invalid Dmap range")
            skipped.update(range(start, end + 1))
            continue
        while cursor in skipped:
            cursor += 1
        require(cursor <= len(values), "Too many packed row values")
        if item is not False:
            values[cursor - 1] = item
        cursor += 1
    return {
        key: _json_value(values[pos - 1], aliases)
        for key, pos in positions.items()
    }


def _is_row_assignment(tokens: list[str], index: int) -> bool:
    indexed_name = (
        bool(IDENT.fullmatch(tokens[index])) and tokens[index + 1] == "["
    )
    if not indexed_name:
        return False

    close = tokens.index("]", index + 2)
    if close + 1 < len(tokens) and tokens[close + 1] == "=":
        require(close == index + 3, "Unsupported row index expression")
    return tokens[index + 3 : index + 5] == ["]", "="]


def _row_calls(value: LuaValue) -> list[Call]:
    candidates = value if isinstance(value, list) else [value]
    calls = []
    for item in candidates:
        if not isinstance(item, Call):
            msg = "Unsupported row assignment"
            raise ValueError(msg)  # noqa: TRY004 - malformed row
        calls.append(item)
    return calls


def _rows(reader: Reader, sheet: Sheet) -> list[dict]:
    aliases = _aliases(reader)
    result = []
    tokens = reader.tokens
    for index in range(len(tokens) - 5):
        if not _is_row_assignment(tokens, index):
            continue

        calls = _row_calls(reader.value_at(index + 5))
        expanded_rows = len(result) + len(calls)
        require(
            expanded_rows <= MAX_ROWS
            and expanded_rows * len(sheet.fields) <= MAX_CELLS,
            "Table expansion limit exceeded",
        )
        result.extend(_row(item, sheet, aliases) for item in calls)
    return result


def _first_table(source: str) -> list:
    tables = local_tables(Reader(source))
    require(bool(tables), "Missing Lua array")
    result = next(iter(tables.values()))
    if not isinstance(result, list):
        msg = "Expected Lua array"
        raise ValueError(msg)  # noqa: TRY004 - malformed Lua array
    return result


def _translate(rows: list[dict], entry: dict, sources: dict[str, str]) -> None:
    prefix = f"Excels.Langs.{entry['TableName']}_{entry['SheetName']}_"
    localized = {}
    for locale in LOCALES:
        modules = entry.get(locale, [])
        require(isinstance(modules, list), "Invalid locale index")
        for module in modules:
            require(
                isinstance(module, str)
                and module.startswith(prefix)
                and module.endswith("_" + locale),
                "Invalid locale module",
            )
            field = module[len(prefix) : -(len(locale) + 1)]
            require(
                not field or bool(NAME.fullmatch(field)),
                "Invalid locale field",
            )
            key = module.removeprefix("Excels.")
            require(key in sources, f"Missing translation module: {key}")
            strings = _first_table(sources[key])
            require(
                all(isinstance(item, str) for item in strings),
                "Invalid translation array",
            )
            for row_index, row in enumerate(rows):
                column = field + "_" + locale if field else locale
                require(column in row, f"Missing locale column: {column}")
                pointer = row.pop(column)
                if pointer is None or (type(pointer) is int and pointer == 0):
                    continue
                require(
                    type(pointer) is int and 0 < pointer <= len(strings),
                    f"Invalid translation index: {column}={pointer!r}",
                )
                if field:
                    localized.setdefault((row_index, field), {})[locale] = (
                        strings[pointer - 1]
                    )
                else:
                    row[locale] = strings[pointer - 1]
    for (row_index, field), translations in localized.items():
        rows[row_index][field] = translations


def _schema_owner(name: str, reader: Reader, sheets: dict[str, Sheet]) -> str:
    if name in sheets:
        return name

    tokens = reader.tokens
    imports = [
        tokens[i + 2]
        for i in range(len(tokens) - 2)
        if tokens[i : i + 2] == ["require", "("]
        and tokens[i + 2].startswith(('"Excels.Data.', "'Excels.Data."))
    ]
    require(len(imports) == 1, f"Missing table schema: {name}")
    return string_value(imports[0]).removeprefix("Excels.")


def convert_tables(sources: dict[str, str]) -> tuple[dict[str, list], list]:
    require("All" in sources, "Missing Excel All index")
    index = _first_table(sources["All"])
    require(bool(index), "Empty Excel index")

    sheets = {}
    modules = {}
    for name, source in sources.items():
        if not name.startswith("Data."):
            continue
        reader = Reader(source)
        modules[name] = reader
        sheet = _schema(reader, local_tables(reader))
        if sheet is not None:
            sheets[name] = sheet

    gathered = {name: [] for name in sheets}
    row_count = 0
    cell_count = 0
    for name, reader in modules.items():
        owner = _schema_owner(name, reader, sheets)
        require(owner in sheets, f"Missing table schema: {owner}")
        try:
            rows = _rows(reader, sheets[owner])
            row_count += len(rows)
            cell_count += len(rows) * len(sheets[owner].fields)
            require(
                row_count <= MAX_ROWS and cell_count <= MAX_CELLS,
                "Table expansion limit exceeded",
            )
            gathered[owner].extend(rows)
        except ValueError as exc:
            msg = f"{name}: {exc}"
            raise ValueError(msg) from exc

    outputs = {}
    for entry in index:
        require(isinstance(entry, dict), "Invalid Excel index entry")
        table, sheet = entry.get("TableName"), entry.get("SheetName")
        require(
            isinstance(table, str)
            and bool(NAME.fullmatch(table))
            and isinstance(sheet, str)
            and bool(NAME.fullmatch(sheet)),
            "Invalid table or sheet name",
        )
        module = f"Data.{table}.{sheet}"
        require(module in gathered, f"Missing table schema: {module}")
        rows = gathered.pop(module)
        _translate(rows, entry, sources)
        outputs[f"{table}/{sheet}"] = sorted(
            rows,
            key=lambda row: (type(row.get("id")).__name__, row.get("id", 0)),
        )
    require(not gathered, f"Tables missing from index: {list(gathered)}")
    require(len(outputs) == len(index), "Duplicate Excel index entry")
    return outputs, index
