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
    ("source", "literal", "is_source"),
    [
        ('return "value"', "value", True),
        ("return [=[value]=]", "value", True),
        ('--[[return fake]]\nreturn "value"', "value", True),
        ('--[=[return fake\n]=]return "value"', None, True),
        ('return "line\nbreak"', "line\nbreak", False),
    ],
)
def test_source_validation_and_literal_reading_keep_distinct_rules(
    source: str, literal: str | None, *, is_source: bool
) -> None:
    assert _looks_like_lua(source.encode()) is is_source
    reader = Reader(source)
    if literal is None:
        assert reader.tokens == ["]", "=", "]", "return", '"value"']
    else:
        reader.take("return")
        assert reader.value() == literal


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
