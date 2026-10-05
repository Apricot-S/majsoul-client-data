import pytest

from majsoul_client_data.parsing import (
    bundle_base_url,
    parse_client_html,
    warehouse_settings_url,
)


def test_jp_unity_html_yields_loader_and_version() -> None:
    # Synthetic examples, not copies of downloaded client files.
    info = parse_client_html("""
        <script src="Build/jp-WebGL-release-4.0.0(1).loader.js"></script>
        <script>
            createUnityInstance(canvas, { productVersion: "4.0.0" });
        </script>
    """)
    assert info.issuer == "jp"
    assert info.loader == "Build/jp-WebGL-release-4.0.0(1).loader.js"
    assert info.product_version == "4.0.0"


@pytest.mark.parametrize(
    "html", ["<html></html>", '<script src="old.js"></script>']
)
def test_legacy_or_changed_html_is_not_silently_treated_as_unity(
    html: str,
) -> None:
    with pytest.raises(ValueError, match="loader"):
        parse_client_html(html)


def test_cn_client_is_rejected() -> None:
    with pytest.raises(ValueError, match="JP"):
        parse_client_html(
            '<script src="Build/chs_t-WebGL-release-1.loader.js"></script>'
        )


def test_warehouse_selection_uses_first_and_priority_then_weight() -> None:
    settings = {
        "warehouses": [
            {
                "urls": [
                    {"url": "https://low.test/", "Priority": 1, "weight": 100},
                    {"url": "https://other.test/", "Priority": 2, "weight": 1},
                    {
                        "url": "https://cdn.test/root/",
                        "Priority": 2,
                        "weight": 10,
                    },
                ],
                "warehouseSettingPath": "/warehouse/jp.json",
            }
        ]
    }
    assert (
        warehouse_settings_url(settings)
        == "https://cdn.test/root/warehouse/jp.json"
    )


def test_bundle_base_keeps_versioned_path_and_trailing_slash() -> None:
    assert (
        bundle_base_url(
            {
                "urls": [{"url": "https://cdn.test/root"}],
                "bundlePath": "bundles/v1",
            }
        )
        == "https://cdn.test/root/bundles/v1/"
    )


@pytest.mark.parametrize(
    "settings", [{}, {"warehouses": []}, {"warehouses": [{}]}]
)
def test_incomplete_settings_raise_readable_error(settings: object) -> None:
    with pytest.raises(ValueError, match=r"warehouses|urls"):
        warehouse_settings_url(settings)


@pytest.mark.parametrize(
    "path", ["https://other.test/x", "../x", "//other.test/x"]
)
def test_settings_path_cannot_replace_the_selected_cdn(path: str) -> None:
    with pytest.raises(ValueError, match="relative distribution path"):
        bundle_base_url(
            {"urls": [{"url": "https://cdn.test/"}], "bundlePath": path}
        )
