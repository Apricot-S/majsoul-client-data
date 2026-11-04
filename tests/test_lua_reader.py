import pytest

from majsoul_client_data.extract import _looks_like_lua
from majsoul_client_data.lua import Reader, local_tables, parse_literal


def test_reading_at_a_position_keeps_the_callers_cursor() -> None:
    reader = Reader('local a={1};local b={"two"}')
    reader.take("local")
    position = reader.pos

    assert reader.value_at(3) == [1]
    assert reader.pos == position
    assert local_tables(reader) == {"a": [1], "b": ["two"]}
    assert reader.pos == position


@pytest.mark.parametrize(
    ("source", "literal"),
    [
        ('return "value"', "value"),
        ("return [=[value]=]", "value"),
        ('--[[return fake]]\nreturn "value"', "value"),
        ('--[=[return fake\n]=]return "value"', "value"),
        ('--[===[return fake\n]===]return "value"', "value"),
        ('return "--[=[comment]=]"', "--[=[comment]=]"),
        ("return [==[--[=[comment]=] } (\n]==]", "--[=[comment]=] } (\n"),
        (r'''return "escaped\"--text"''', 'escaped"--text'),
    ],
)
def test_source_validation_and_literal_reading_share_lexical_rules(
    source: str, literal: str
) -> None:
    assert _looks_like_lua(source.encode())
    reader = Reader(source)
    reader.take("return")
    assert reader.value() == literal
    assert reader.pos == len(reader.tokens)


@pytest.mark.parametrize(
    "source",
    [
        'return "line\nbreak"',
        'return "unterminated',
        "return 'unterminated",
        "return [=[unterminated]]",
        "return {} --[=[unterminated]]",
    ],
)
def test_source_validation_and_reading_reject_invalid_lexemes(
    source: str,
) -> None:
    assert not _looks_like_lua(source.encode())
    with pytest.raises(ValueError, match="Lua"):
        Reader(source)


def test_comments_do_not_consume_the_token_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("majsoul_client_data.lua.MAX_TOKENS", 3)
    assert parse_literal(
        "--[=[\nignored tokens here\n]=]\n{1}-- trailing"
    ) == [1]


def test_dense_lua_data_keeps_its_existing_token_boundaries() -> None:
    assert parse_literal('{a="value",b={false,nil}}') == {
        "a": "value",
        "b": [False, None],
    }


def test_failed_read_at_a_position_keeps_the_callers_cursor() -> None:
    reader = Reader("local a={1,")
    reader.take("local")
    position = reader.pos

    with pytest.raises(ValueError, match="Expected Lua lexeme"):
        reader.value_at(3)
    assert reader.pos == position
