import re

NUMBER_PATTERN = r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
LONG_STRING_PATTERN = r"\[(=*)\[(?:.|\n)*?\]\1\]"
DATA_STRING_PATTERN = r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'"
SOURCE_STRING_PATTERN = (
    r'"(?:\\[^\x00]|[^"\\\x00\r\n])*"|'
    r"'(?:\\[^\x00]|[^'\\\x00\r\n])*'"
)

# Data reading is permissive; source validation checks the whole input.
DATA_LEXEMES = (
    r"\s+",
    r"--\[\[(?:.|\n)*?\]\]",
    r"--[^\n]*",
    LONG_STRING_PATTERN,
    DATA_STRING_PATTERN,
    NUMBER_PATTERN,
    r"[A-Za-z_]\w*",
    r".",
)
TOKEN = re.compile("|".join(DATA_LEXEMES), re.DOTALL)
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
        return (None if comment else "STRING"), end + len(closing)
    if comment:
        newline = text.find("\n", start)
        return None, len(text) if newline < 0 else newline + 1
    match = LUA_LEXEME.match(text, cursor)
    if match is None:
        msg = "Invalid Lua lexeme"
        raise ValueError(msg)
    token = match[0]
    return ("STRING" if token[0] in "\"'" else token), match.end()


def source_tokens(text: str, *, max_depth: int, max_tokens: int) -> list[str]:
    tokens = []
    stack = []
    pairs = {"}": "{", ")": "(", "]": "["}
    cursor = 0
    while cursor < len(text):
        token, cursor = _scan_lua_token(text, cursor)
        if token is None:
            continue
        if token in pairs:
            require(bool(stack), "Unmatched Lua delimiter")
            require(stack.pop() == pairs[token], "Mismatched Lua delimiter")
        elif token in pairs.values():
            stack.append(token)
            require(len(stack) < max_depth, "Lua nesting limit exceeded")
        require(len(tokens) < max_tokens, "Lua token limit exceeded")
        tokens.append(token)
    require(not stack, "Unclosed Lua delimiter")
    return tokens
