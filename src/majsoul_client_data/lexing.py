import re
from collections.abc import Iterator

NUMBER_PATTERN = r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
SOURCE_STRING_PATTERN = (
    r'"(?:\\[^\x00]|[^"\\\x00\r\n])*"|'
    r"'(?:\\[^\x00]|[^'\\\x00\r\n])*'"
)

IDENT = re.compile(r"[A-Za-z_]\w*\Z", re.ASCII)
NUMBER = re.compile(NUMBER_PATTERN + r"\Z")

SOURCE_LEXEMES = (
    SOURCE_STRING_PATTERN,
    r"[A-Za-z_][A-Za-z_0-9]*",
    NUMBER_PATTERN,
    r"[{}()\[\],;.=:+*/%#<>~^-]",
)
LUA_LEXEME = re.compile("|".join(SOURCE_LEXEMES))
LONG_OPEN = re.compile(r"\[(=*)\[")


def require(condition: bool, message: str) -> None:  # noqa: FBT001
    if not condition:
        raise ValueError(message)


def _scan_lua_token(text: str, cursor: int) -> tuple[str | None, int]:
    if text[cursor].isspace():
        return None, cursor + 1
    comment = text.startswith("--", cursor)
    start = cursor + 2 if comment else cursor
    opening = LONG_OPEN.match(text, start)
    if opening:
        closing = "]" + opening[1] + "]"
        end = text.find(closing, opening.end())
        require(end >= 0, "Unterminated Lua long string or comment")
        end += len(closing)
        return (None if comment else text[cursor:end]), end
    if comment:
        newline = text.find("\n", start)
        return None, len(text) if newline < 0 else newline + 1
    match = LUA_LEXEME.match(text, cursor)
    if match is None:
        msg = "Invalid Lua lexeme"
        raise ValueError(msg)
    return match[0], match.end()


def lua_tokens(text: str, *, max_tokens: int) -> Iterator[str]:
    cursor = 0
    count = 0
    while cursor < len(text):
        token, cursor = _scan_lua_token(text, cursor)
        if token is None:
            continue
        require(count < max_tokens, "Lua token limit exceeded")
        count += 1
        yield token


def source_tokens(text: str, *, max_depth: int, max_tokens: int) -> list[str]:
    tokens = []
    stack = []
    pairs = {"}": "{", ")": "(", "]": "["}
    for token in lua_tokens(text, max_tokens=max_tokens):
        if token in pairs:
            require(bool(stack), "Unmatched Lua delimiter")
            require(stack.pop() == pairs[token], "Mismatched Lua delimiter")
        elif token in pairs.values():
            stack.append(token)
            require(len(stack) < max_depth, "Lua nesting limit exceeded")
        tokens.append(
            "STRING"
            if token.startswith(("'", '"')) or LONG_OPEN.match(token)
            else token
        )
    require(not stack, "Unclosed Lua delimiter")
    return tokens
