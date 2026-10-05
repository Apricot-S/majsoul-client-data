import math
import re
from dataclasses import dataclass

from .lexing import IDENT, NUMBER, TOKEN, require

MAX_DEPTH = 64
BYTE_MAX = 255
ESCAPE_DIGITS = 3
MAX_TOKENS = 2_000_000

type LuaValue = (
    str
    | int
    | float
    | bool
    | Reference
    | Call
    | list[LuaValue]
    | dict[str | int, LuaValue]
    | None
)


@dataclass(frozen=True)
class Reference:
    name: str
    index: LuaValue = None


@dataclass(frozen=True)
class Call:
    name: str
    args: list[LuaValue]


def string_value(lexeme: str) -> str:
    if lexeme.startswith("["):
        match = re.match(r"\[(=*)\[", lexeme)
        if match is None:
            msg = "Invalid Lua long string"
            raise ValueError(msg)
        size = len(match[0])
        return lexeme[size:-size].removeprefix("\n")
    output = bytearray()
    index = 1
    escapes = dict(zip("abfnrtv", "\a\b\f\n\r\t\v", strict=True))
    while index < len(lexeme) - 1:
        char = lexeme[index]
        index += 1
        if char == "\\":
            char = lexeme[index]
            index += 1
            if char.isdigit():
                digits = char
                while (
                    index < len(lexeme) - 1
                    and len(digits) < ESCAPE_DIGITS
                    and lexeme[index].isdigit()
                ):
                    digits += lexeme[index]
                    index += 1
                value = int(digits)
                require(value <= BYTE_MAX, "Invalid Lua byte escape")
                output.append(value)
                continue
            if char == "x":
                value = lexeme[index : index + 2]
                require(
                    bool(re.fullmatch(r"[0-9A-Fa-f]{2}", value)),
                    "Invalid Lua hex escape",
                )
                output.append(int(value, 16))
                index += 2
                continue
            require(
                char in escapes or char in "\\\"'\n", "Unsupported Lua escape"
            )
            char = escapes.get(char, char)
        output.extend(char.encode("utf-8"))
    return output.decode("utf-8")


class Reader:
    def __init__(self, source: str) -> None:
        self.tokens = []
        for match in TOKEN.finditer(source):
            lexeme = match[0]
            if lexeme.isspace() or lexeme.startswith("--"):
                continue
            require(len(self.tokens) < MAX_TOKENS, "Lua token limit exceeded")
            self.tokens.append(lexeme)
        self.pos = 0

    def peek(self, offset: int = 0) -> str:
        index = self.pos + offset
        return self.tokens[index] if index < len(self.tokens) else ""

    def take(self, expected: str | None = None) -> str:
        lexeme = self.peek()
        require(
            bool(lexeme) and (expected is None or lexeme == expected),
            f"Expected Lua lexeme {expected!r}, got {lexeme!r}",
        )
        self.pos += 1
        return lexeme

    def value_at(self, position: int) -> LuaValue:
        previous = self.pos
        self.pos = position
        try:
            return self.value()
        finally:
            self.pos = previous

    def value(self, depth: int = 0) -> LuaValue:
        require(depth < MAX_DEPTH, "Lua nesting limit exceeded")
        lexeme = self.take()
        if lexeme == "{":
            return self.table(depth + 1)
        if lexeme.startswith(("'", '"', "[")):
            return string_value(lexeme)
        if lexeme in {"nil", "true", "false"}:
            return {"nil": None, "true": True, "false": False}[lexeme]
        if lexeme == "-":
            number = self.value(depth + 1)
            if type(number) is not int and type(number) is not float:
                msg = "Invalid Lua negative number"
                raise ValueError(msg)
            return -number
        if NUMBER.fullmatch(lexeme):
            number = (
                float(lexeme)
                if any(c in lexeme for c in ".eE")
                else int(lexeme)
            )
            require(math.isfinite(number), "Nonfinite Lua number")
            return number
        return self.reference(lexeme, depth)

    def reference(self, name: str, depth: int) -> Reference | Call:
        require(bool(IDENT.fullmatch(name)), f"Unsupported Lua value: {name}")
        while self.peek() == ".":
            self.take(".")
            part = self.take()
            require(bool(IDENT.fullmatch(part)), "Invalid Lua reference")
            name += "." + part
        if self.peek() == "[":
            self.take("[")
            key = self.value(depth + 1)
            self.take("]")
            return Reference(name, key)
        if self.peek() == "(":
            self.take("(")
            args = []
            while self.peek() != ")":
                args.append(self.value(depth + 1))
                if self.peek() != ",":
                    break
                self.take(",")
            self.take(")")
            return Call(name, args)
        return Reference(name)

    def table(self, depth: int) -> list | dict:
        entries = {}
        array = []
        keyed = False
        while self.peek() != "}":
            if self.peek() == "[":
                self.take("[")
                key = self.value(depth)
                self.take("]")
                self.take("=")
                keyed = True
            elif IDENT.fullmatch(self.peek()) and self.peek(1) == "=":
                key = self.take()
                self.take("=")
                keyed = True
            else:
                key = len(array) + 1
            value = self.value(depth)
            require(isinstance(key, (str, int)), "Invalid Lua table key")
            require(key not in entries, "Duplicate Lua table key")
            entries[key] = value
            if not keyed:
                array.append(value)
            if self.peek() not in {",", ";"}:
                break
            self.take()
        self.take("}")
        return entries if keyed else array


def parse_literal(source: str) -> LuaValue:
    reader = Reader(source)
    value = reader.value()
    require(reader.pos == len(reader.tokens), "Unexpected trailing Lua tokens")
    require(
        not isinstance(value, (Reference, Call)), "Unsupported Lua expression"
    )
    return value


def local_tables(reader: Reader) -> dict[str, LuaValue]:
    result = {}
    for index in range(len(reader.tokens) - 3):
        if reader.tokens[index] == "local" and reader.tokens[
            index + 2 : index + 4
        ] == ["=", "{"]:
            result[reader.tokens[index + 1]] = reader.value_at(index + 3)
    return result
