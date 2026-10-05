import pytest

from majsoul_client_data.protocol import convert_protocol


def message_source(name: str, package: str = "lq") -> str:
    return (
        'local a=require"protobuf.protobuf"'
        "M=a.Descriptor()"
        f'M.name="{name}"M.full_name=".{package}.{name}"'
        "M.fields={}M.nested_types={}M.enum_types={}"
    )


def test_mixed_rpcs_keep_partial_imports_empty_services_and_counts() -> None:
    sources = {
        "Protol.request_pb": message_source("Request"),
        "Protol.reply_pb": message_source("Reply"),
    }
    outputs, counts = convert_protocol(
        sources,
        {
            "service": {
                "OnlyMissing": {
                    "get": {"request": "Missing", "response": "Reply"},
                },
                "Demo": {
                    "get": {"request": "Request", "response": "Request"},
                },
            },
        },
    )
    assert outputs["proto/services.proto"] == (
        'syntax = "proto3";\n'
        "package lq;\n"
        'import "reply.proto";\n'
        'import "request.proto";\n'
        "\n"
        "service Demo {\n"
        "  rpc get (.lq.Request) returns (.lq.Request);\n"
        "}\n"
        "service OnlyMissing {\n"
        "}\n"
    )
    assert outputs["unresolved_rpcs.json"] == [
        {
            "service": "OnlyMissing",
            "method": "get",
            "request": "Missing",
            "response": "Reply",
            "missing_types": ["Missing"],
        },
    ]
    assert {
        key: counts[key]
        for key in ("services", "rpcs", "resolved_rpcs", "unresolved_rpcs")
    } == {
        "services": 2,
        "rpcs": 2,
        "resolved_rpcs": 1,
        "unresolved_rpcs": 1,
    }


def test_partial_rpc_resolution_keeps_its_package_validation() -> None:
    sources = {
        "Protol.request_pb": message_source("Request"),
        "Protol.other_pb": message_source("Other", "other"),
    }
    with pytest.raises(ValueError, match="Inconsistent RPC packages"):
        convert_protocol(
            sources,
            {
                "service": {
                    "Demo": {
                        "get": {"request": "Request", "response": "Request"},
                        "missing": {"request": "Missing", "response": "Other"},
                    },
                },
            },
        )


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        ("F=a.FieldDescriptor()", "Invalid protobuf identifier"),
        (
            (
                'N=a.Descriptor()N.name="Inner"N.full_name=".lq.Request.Inner"'
                "N.fields={}N.nested_types={}N.enum_types={}"
            ),
            "Inconsistent protobuf packages",
        ),
        (
            'F=a.FieldDescriptor()F.name="field"F.full_name=".lq.Request.field"',
            "Unattached protobuf descriptor",
        ),
        ("M.nested_types={M}", "Duplicate protobuf property"),
    ],
)
def test_descriptor_failures_keep_their_existing_diagnostics(
    extra: str, error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        convert_protocol(
            {"Protol.request_pb": message_source("Request") + extra},
            {"service": {}},
        )
