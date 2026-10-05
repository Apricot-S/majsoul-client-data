import time
from collections.abc import Callable
from http import HTTPStatus
from types import TracebackType
from typing import Self

import niquests
from niquests.adapters import HTTPAdapter

from .parsing import validate_url

MAX_METADATA_REQUESTS = 3
MIN_REQUEST_INTERVAL = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0",  # noqa: E501
}


class MetadataHTTPClient:
    def __init__(  # noqa: PLR0913 - transport limits and test dependencies
        self,
        *,
        timeout: float = 20,
        max_bytes: int = 1024 * 1024,
        max_requests: int = MAX_METADATA_REQUESTS,
        session: niquests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout <= 0 or max_bytes <= 0 or max_requests <= 0:
            msg = "Timeout and size limit must be positive"
            raise ValueError(msg)

        self._session = session if session is not None else niquests.Session()
        self._session.mount("https://", HTTPAdapter(max_retries=0))
        self._session.mount("http://", HTTPAdapter(max_retries=0))
        self._session.headers.update(HEADERS.items())

        self._timeout = timeout
        self._max_bytes = max_bytes
        self._max_requests = max_requests
        self._sleep = sleep
        self._clock = clock
        self._last_finished: float | None = None
        self._requests = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._session.close()

    def get_text(self, url: str) -> str:
        return self.get_bytes(url).decode("utf-8-sig")

    def get_bytes(self, url: str, *, max_bytes: int | None = None) -> bytes:
        limit = self._max_bytes if max_bytes is None else max_bytes
        if limit <= 0:
            msg = "Size limit must be positive"
            raise ValueError(msg)
        validate_url(url)
        if self._requests >= self._max_requests:
            msg = f"Metadata request budget ({self._max_requests}) exhausted"
            raise ValueError(msg)
        if self._last_finished is not None:
            delay = MIN_REQUEST_INTERVAL - (
                self._clock() - self._last_finished
            )
            if delay > 0:
                self._sleep(delay)
        self._requests += 1
        try:
            with self._session.get(
                url,
                timeout=self._timeout,
                allow_redirects=False,
                stream=True,
            ) as response:
                response.raise_for_status()
                if (
                    not HTTPStatus.OK
                    <= response.status_code
                    < HTTPStatus.MULTIPLE_CHOICES
                ):
                    msg = (
                        f"HTTP {response.status_code} at {url}; "
                        "redirects are disabled"
                    )
                    raise ValueError(msg)
                length = response.headers.get("Content-Length")
                if length is not None and int(length) > limit:
                    msg = f"Metadata size limit exceeded at {url}"
                    raise ValueError(msg)
                body = bytearray()
                for chunk in response.iter_content(chunk_size=8192):
                    if len(body) + len(chunk) > limit:
                        msg = f"Metadata size limit exceeded at {url}"
                        raise ValueError(msg)
                    body.extend(chunk)
                return bytes(body)
        finally:
            self._last_finished = self._clock()
