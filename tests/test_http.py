from unittest.mock import Mock

import niquests
import pytest

from majsoul_client_data.http import MetadataHTTPClient


def response_for(body: bytes = b"hello", status: int = 200) -> Mock:
    response = Mock()
    response.status_code = status
    response.headers = {}
    response.iter_content.return_value = [body]
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


def client_with(
    session: Mock, *, max_bytes: int = 1024 * 1024
) -> MetadataHTTPClient:
    return MetadataHTTPClient(
        session=session, sleep=Mock(), clock=lambda: 0, max_bytes=max_bytes
    )


def test_http_uses_timeout_streaming_and_disables_redirects() -> None:
    session = Mock()
    response = response_for("雀魂".encode())
    session.get.return_value = response
    client = client_with(session)
    assert client.get_text("https://example.test/") == "雀魂"
    session.get.assert_called_once_with(
        "https://example.test/",
        timeout=20,
        allow_redirects=False,
        stream=True,
    )
    response.raise_for_status.assert_called_once()
    assert session.mount.call_count == 2
    for call in session.mount.call_args_list:
        assert call.args[1].max_retries.total == 0


@pytest.mark.parametrize("status", [301, 302, 403, 429, 500])
def test_redirects_and_http_errors_never_retry(status: int) -> None:
    session = Mock()
    response = response_for(status=status)
    if status >= 400:
        response.raise_for_status.side_effect = niquests.HTTPError(
            f"HTTP {status}"
        )
    session.get.return_value = response
    client = client_with(session)
    with pytest.raises((ValueError, niquests.HTTPError)):
        client.get_text("https://example.test/")
    session.get.assert_called_once()
    response.iter_content.assert_not_called()


def test_timeout_stops_without_retry() -> None:
    session = Mock()
    session.get.side_effect = niquests.Timeout("timed out")
    with pytest.raises(niquests.Timeout):
        client_with(session).get_text("https://example.test/")
    session.get.assert_called_once()


def test_request_budget_prevents_fourth_request() -> None:
    session = Mock()
    session.get.return_value = response_for()
    client = client_with(session)
    for _ in range(3):
        client.get_text("https://example.test/")
    with pytest.raises(ValueError, match="budget"):
        client.get_text("https://example.test/")
    assert session.get.call_count == 3


def test_requests_are_spaced_at_least_two_seconds_apart() -> None:
    session = Mock()
    session.get.return_value = response_for()
    sleep = Mock()
    client = MetadataHTTPClient(session=session, sleep=sleep, clock=lambda: 0)
    client.get_text("https://example.test/")
    sleep.assert_not_called()
    client.get_text("https://example.test/")
    sleep.assert_called_once_with(2)


def test_decoded_body_size_limit_stops_chunked_response() -> None:
    session = Mock()
    response = response_for()
    response.iter_content.return_value = [b"123", b"456"]
    session.get.return_value = response
    with pytest.raises(ValueError, match="size limit"):
        client_with(session, max_bytes=5).get_text("https://example.test/")
    response.__exit__.assert_called_once()


def test_oversized_content_length_is_rejected_before_reading_body() -> None:
    session = Mock()
    response = response_for()
    response.headers = {"Content-Length": "100"}
    session.get.return_value = response
    with pytest.raises(ValueError, match="size limit"):
        client_with(session, max_bytes=5).get_text("https://example.test/")
    response.iter_content.assert_not_called()


def test_client_closes_session_even_on_failure() -> None:
    session = Mock()
    session.get.side_effect = niquests.Timeout("timed out")
    with pytest.raises(niquests.Timeout), client_with(session) as client:
        client.get_text("https://example.test/")
    session.close.assert_called_once()


def test_binary_download_preserves_bytes_and_uses_own_limit() -> None:
    session = Mock()
    session.get.return_value = response_for(b"\xff\x00\x81")
    client = client_with(session, max_bytes=1)
    assert client.get_bytes("https://example.test/", max_bytes=3) == (
        b"\xff\x00\x81"
    )


def test_download_budget_allows_four_requests_but_not_five() -> None:
    session = Mock()
    session.get.return_value = response_for()
    client = MetadataHTTPClient(
        session=session,
        sleep=Mock(),
        clock=lambda: 0,
        max_requests=4,
    )
    for _ in range(4):
        client.get_bytes("https://example.test/")
    with pytest.raises(ValueError, match="budget"):
        client.get_bytes("https://example.test/")
    assert session.get.call_count == 4
