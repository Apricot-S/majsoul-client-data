import pytest

from majsoul_client_data.lua import parse_literal
from majsoul_client_data.protocol import convert_protocol
from majsoul_client_data.tables import convert_tables


def test_lua_literals_decode_bytes_and_do_not_execute_calls() -> None:
    assert parse_literal(r'{"\228\184\173",false,nil,-2,1e2}') == [
        "中",
        False,
        None,
        -2,
        100.0,
    ]
    with pytest.raises(ValueError, match="Unsupported"):
        parse_literal('os.execute("danger")')


def test_tables_expand_defaults_ranges_chunks_and_translations() -> None:
    sources = {
        "All": (
            'local a={{TableName="demo",SheetName="item",jp={'
            '"Excels.Langs.demo_item_name_jp"}}}return a'
        ),
        "Data.demo.item": (
            'local a=require("ExcelTool")local b=a.Pack;local e=a.Dmap;'
            "local f={id=1,name_jp=2,count=3}local g={1,1,8}local h="
            "function(i,j)return d(i,j,f,g)end;local k={}k[1]=b({false,"
            "false,9},h)return{tb=k}"
        ),
        "Data.demo.item_b1": (
            'local a=require("ExcelTool")local b=a.Pack;local e=a.Dmap;'
            'local f=require("Excels.Data.demo.item")k[2]=b({2,e[2002],10'
            "},h)"
        ),
        "Langs.demo_item_name_jp": 'local a={"日本語"}return a',
    }
    tables, index = convert_tables(sources)
    assert tables == {
        "demo/item": [
            {"id": 1, "name": {"jp": "日本語"}, "count": 9},
            {"id": 2, "name": {"jp": "日本語"}, "count": 10},
        ]
    }
    assert len(index) == 1


def test_tables_fail_on_missing_translation_and_unknown_data() -> None:
    with pytest.raises(ValueError, match="schema"):
        convert_tables(
            {
                "All": 'local a={{TableName="a",SheetName="b"}}',
                "Data.a.b": "local a={}return a",
            }
        )


def test_protocol_resolves_imports_nested_types_and_rpc() -> None:
    sources = {
        "Protol.base_pb": (
            'local a=require"protobuf.protobuf"M=a.Descriptor()M.name='
            '"Request"M.full_name=".lq.Request"M.fields={}M.nested_types='
            "{}M.enum_types={}"
        ),
        "Protol.other_pb": (
            'local a=require"protobuf.protobuf"local b=require('
            '"Protol.base_pb")M=a.Descriptor()F=a.FieldDescriptor()N=a.'
            'Descriptor()M.name="Reply"M.full_name=".lq.Reply"M.fields={F'
            '}M.nested_types={N}M.enum_types={}N.name="Inner"N.full_name='
            '".lq.Reply.Inner"N.fields={}N.nested_types={}N.enum_types={}'
            'F.name="requests"F.full_name=".lq.Reply.requests"F.number=1;'
            "F.type=11;F.label=3;F.message_type=b.M"
        ),
    }
    outputs, counts = convert_protocol(
        sources,
        {
            "service": {
                "Demo": {"get": {"request": "Request", "response": "Reply"}}
            }
        },
    )
    assert "repeated .lq.Request requests = 1;" in outputs["proto/other.proto"]
    assert "message Inner {" in outputs["proto/other.proto"]
    assert 'import "base.proto";' in outputs["proto/other.proto"]
    assert (
        "rpc get (.lq.Request) returns (.lq.Reply);"
        in outputs["proto/services.proto"]
    )
    assert counts["messages"] == 3
    assert counts["rpcs"] == 1
    sources["Protol.config_pb"] = sources["Protol.base_pb"].replace(
        ".lq.Request", ".config.Request"
    )
    # Fully qualified RPC references disambiguate matching short names.
    extra_outputs, _ = convert_protocol(
        sources,
        {
            "service": {
                "Demo": {
                    "get": {"request": ".lq.Request", "response": ".lq.Reply"}
                }
            }
        },
    )
    assert "package lq;" in extra_outputs["proto/services.proto"]
    sources.pop("Protol.config_pb")
    missing_outputs, missing_counts = convert_protocol(
        sources,
        {
            "service": {
                "Demo": {"get": {"request": "Missing", "response": "Reply"}}
            }
        },
    )
    assert missing_counts["unresolved_rpcs"] == 1
    unresolved = missing_outputs["unresolved_rpcs.json"]
    assert isinstance(unresolved, list)
    assert unresolved[0]["request"] == "Missing"
    assert "proto/services.proto" not in missing_outputs


def test_absent_translation_is_omitted_but_bad_positive_index_stops() -> None:
    sources = {
        "All": (
            'local a={{TableName="a",SheetName="b",jp={'
            '"Excels.Langs.a_b_name_jp"}}}'
        ),
        "Data.a.b": (
            "local b=a.Pack;local f={id=1,name_jp=2}local g={1,nil}local "
            "h=function(i,j)return d(i,j,f,g)end;k[1]=b({false,false},h)"
        ),
        "Langs.a_b_name_jp": 'local a={"名前"}',
    }
    assert convert_tables(sources)[0] == {"a/b": [{"id": 1}]}
    sources["Data.a.b"] = sources["Data.a.b"].replace("false,false", "false,2")
    with pytest.raises(ValueError, match="translation index"):
        convert_tables(sources)


def test_direct_locale_columns_share_a_packed_position() -> None:
    sources = {
        "All": (
            'local a={{TableName="a",SheetName="b",jp={'
            '"Excels.Langs.a_b_jp"},en={"Excels.Langs.a_b_en"}}}'
        ),
        "Data.a.b": (
            "local b=a.Pack;local f={id=1,jp=-2,en=-2}local g={1,1}local "
            "h=function(i,j)return d(i,j,f,g)end;k[1]=b({false,false},h)"
        ),
        "Langs.a_b_jp": 'local a={"名前"}',
        "Langs.a_b_en": 'local a={"Name"}',
    }
    assert convert_tables(sources)[0] == {
        "a/b": [{"id": 1, "jp": "名前", "en": "Name"}]
    }


@pytest.mark.parametrize(
    "source",
    ['local b=require("Protol.base_pb")', 'local b=require"Protol.base_pb"'],
)
def test_import_only_protocol_module_has_no_package(source: str) -> None:
    outputs, counts = convert_protocol(
        {"Protol.client_pb": source},
        {"service": {}},
    )
    assert outputs["proto/client.proto"] == 'syntax = "proto3";\n'
    assert counts["messages"] == 0


@pytest.mark.parametrize(
    "source", ['{"\\999"}', "{1,1e999}", '{["a"]=1,["a"]=2}', '{"bad\\q"}']
)
def test_lua_rejects_invalid_literals(source: str) -> None:
    with pytest.raises(
        ValueError, match=r"Invalid|Nonfinite|Duplicate|Unsupported"
    ):
        parse_literal(source)


def test_lua_enforces_nesting_and_token_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="nesting"):
        parse_literal("{" * 65 + "1" + "}" * 65)
    monkeypatch.setattr("majsoul_client_data.lua.MAX_TOKENS", 3)
    with pytest.raises(ValueError, match="token limit"):
        parse_literal("{1,2}")


def test_protocol_renders_enums_and_rejects_broken_type_refs() -> None:
    source = (
        'local a=require"protobuf.protobuf"M=a.Descriptor()F=a.'
        "FieldDescriptor()E=a.EnumDescriptor()V=a.EnumValueDescriptor"
        '()M.name="Request"M.full_name=".lq.Request"M.fields={F}F.'
        'name="mode"F.full_name=".lq.Request.mode"F.number=1;F.type='
        '14;F.label=1;F.enum_type=E;E.name="Mode"E.full_name='
        '".lq.Mode"E.values={V}V.name="NONE"V.number=0;V.index=0;'
    )
    outputs, counts = convert_protocol(
        {"Protol.demo_pb": source}, {"service": {}}
    )
    assert "enum Mode {" in outputs["proto/demo.proto"]
    assert ".lq.Mode mode = 1;" in outputs["proto/demo.proto"]
    assert counts["enums"] == 1
    with pytest.raises(ValueError, match="Unresolved"):
        convert_protocol(
            {
                "Protol.demo_pb": source.replace(
                    "enum_type=E", "enum_type=MISSING"
                )
            },
            {"service": {}},
        )


def test_table_locales_replace_fallback_strings() -> None:
    sources = {
        "All": (
            'local a={{TableName="a",SheetName="b",jp={'
            '"Excels.Langs.a_b_name_jp"}}}'
        ),
        "Data.a.b": (
            "local b=a.Pack;local f={id=1,name_jp=2,name=3}local g={1,1,"
            '"fallback"}local h=function(i,j)return d(i,j,f,g)end;k[1]=b('
            "{false,false},h)"
        ),
        "Langs.a_b_name_jp": 'local a={"translated"}',
    }
    assert convert_tables(sources)[0]["a/b"] == [
        {"id": 1, "name": {"jp": "translated"}}
    ]


def test_table_expansion_has_a_column_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "majsoul_client_data.tables.MAX_COLUMNS", 1, raising=False
    )
    sources = {
        "All": 'local a={{TableName="a",SheetName="b"}}',
        "Data.a.b": (
            "local b=a.Pack;local f={id=1,value=2}local g={1,0}local h="
            "function(i,j)return d(i,j,f,g)end;k[1]=b({false,false},h)"
        ),
    }
    with pytest.raises(ValueError, match="column limit"):
        convert_tables(sources)


def test_table_rejects_unsupported_row_index_expression() -> None:
    sources = {
        "All": 'local a={{TableName="a",SheetName="b"}}',
        "Data.a.b": (
            "local b=a.Pack;local f={id=1}local g={1}local h=function(i,j"
            ")return d(i,j,f,g)end;k[1+1]=b({false},h)"
        ),
    }
    with pytest.raises(ValueError, match="row index"):
        convert_tables(sources)
