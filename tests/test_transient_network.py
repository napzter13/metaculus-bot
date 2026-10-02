"""A network blip is skipped and retried; anything that ANSWERED wrongly is still a hard failure.

2026-10-02: the home ISP reconnected and DNS for www.metaculus.com failed for one slot, which ended
the run red with ``ApiIdentityError``. The classifier below decides which failures may be skipped, so
the cases that must NOT be skipped matter as much as the ones that must: a TLS failure (an imposter
host's signature) and an HTTP status are never a blip, because the identity preflight exists to stop
exactly those from reaching a token.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
from unittest.mock import MagicMock, patch

import pytest
import requests
from urllib3.exceptions import ConnectTimeoutError, MaxRetryError, NameResolutionError

from metaculus_bot.api_preflight import (
    ApiIdentityError,
    TransientNetworkError,
    verify_api_identity,
    verify_metaculus_api_identity,
)
from metaculus_bot.http_status import is_transient_network_error
from metaculus_bot.mantic import ManticClient


def _dns_failure() -> requests.exceptions.ConnectionError:
    """What ``requests`` raises when the name does not resolve: urllib3's error rides inside it."""
    resolution = NameResolutionError("www.metaculus.com", MagicMock(), socket.gaierror(-3, "Temporary failure"))
    return requests.exceptions.ConnectionError(MaxRetryError(MagicMock(), "/api/posts/", reason=resolution))


class TestClassifier:
    @pytest.mark.parametrize(
        "exc",
        [
            _dns_failure(),
            requests.exceptions.ConnectTimeout("connect timed out"),
            requests.exceptions.ReadTimeout("read timed out"),
            requests.exceptions.ConnectionError("Connection refused"),
            ConnectTimeoutError(None, "connect timed out"),
            socket.gaierror(-2, "Name or service not known"),
            ConnectionResetError("reset by peer"),
        ],
        ids=lambda e: type(e).__name__,
    )
    def test_connectivity_failures_are_transient(self, exc: BaseException) -> None:
        assert is_transient_network_error(exc) is True

    @pytest.mark.parametrize(
        "exc",
        [
            requests.exceptions.SSLError("certificate verify failed"),
            ssl.SSLCertVerificationError("hostname mismatch"),
            requests.exceptions.HTTPError("500 Server Error"),
            requests.exceptions.TooManyRedirects("loop"),
            requests.exceptions.InvalidURL("bad"),
            ValueError("not a network problem"),
            RuntimeError("a bug"),
            TimeoutError("timed out"),
        ],
        ids=lambda e: type(e).__name__,
    )
    def test_everything_else_is_not(self, exc: BaseException) -> None:
        assert is_transient_network_error(exc) is False

    def test_a_tls_failure_anywhere_in_the_chain_wins_over_the_connection_error_around_it(self) -> None:
        """``SSLError`` subclasses ``ConnectionError`` in requests, so a bare isinstance would call a
        certificate failure a blip. It is what a parked or hijacked host looks like."""
        assert issubclass(requests.exceptions.SSLError, requests.exceptions.ConnectionError)
        wrapped = requests.exceptions.ConnectionError("outer")
        wrapped.__cause__ = requests.exceptions.SSLError("inner certificate failure")
        assert is_transient_network_error(wrapped) is False

    def test_a_cause_chain_is_followed(self) -> None:
        """``raise ... from`` says WHY: a wrapper raised from a DNS failure is a DNS failure."""
        outer = RuntimeError("fetch failed")
        outer.__cause__ = socket.gaierror(-3, "Temporary failure in name resolution")
        assert is_transient_network_error(outer) is True

    @staticmethod
    def _raised_while_handling_a_dns_failure(bug: BaseException) -> BaseException:
        """``bug`` as Python really chains it: raised inside an ``except`` for a connection error."""
        try:
            try:
                raise _dns_failure()
            except requests.exceptions.ConnectionError:
                raise bug  # noqa: B904  # the implicit __context__ is exactly the case under test
        except BaseException as caught:  # noqa: BLE001  # returned for the caller to classify
            return caught

    @pytest.mark.parametrize(
        "bug",
        [
            NameError("name 'x' is not defined"),
            KeyError("missing"),
            requests.exceptions.HTTPError("401 Client Error: Unauthorized"),
            RuntimeError("a bug in the handler"),
        ],
        ids=lambda e: type(e).__name__,
    )
    def test_an_error_raised_while_handling_a_network_error_is_not_a_blip(self, bug: BaseException) -> None:
        """A bug or an HTTP answer raised from inside the handler has the ConnectionError as its
        ``__context__`` only. Calling it transient would skip, with exit 0, a run that is really broken."""
        caught = self._raised_while_handling_a_dns_failure(bug)
        assert isinstance(caught.__context__, requests.exceptions.ConnectionError), "the fixture must chain for real"
        assert is_transient_network_error(caught) is False

    def test_a_tls_failure_in_the_context_still_vetoes_a_connection_error(self) -> None:
        """The veto reads both links, unlike the transient test: a ConnectionError raised while handling a
        certificate failure is the imposter case, not a blip."""
        try:
            try:
                raise requests.exceptions.SSLError("certificate verify failed")
            except requests.exceptions.SSLError:
                raise requests.exceptions.ConnectionError("outer")  # noqa: B904  # implicit context under test
        except requests.exceptions.ConnectionError as outer:
            assert is_transient_network_error(outer) is False  # noqa: PT017  # classify the real chained exception

    def test_the_builtin_timeout_is_not_a_network_blip(self) -> None:
        """On 3.11+ ``asyncio.wait_for`` raises the builtin TimeoutError, so counting it would class one of
        the bot's own deadlines as a connectivity failure."""

        async def too_slow() -> None:
            await asyncio.wait_for(asyncio.sleep(5), timeout=0.001)

        with pytest.raises(TimeoutError) as excinfo:
            asyncio.run(too_slow())
        assert is_transient_network_error(excinfo.value) is False

    def test_a_self_referencing_chain_terminates(self) -> None:
        loop = RuntimeError("loop")
        loop.__cause__ = loop
        assert is_transient_network_error(loop) is False


def _get_raising(exc: BaseException):
    return patch("requests.Session.get", side_effect=exc)


class TestPreflight:
    def test_a_dns_failure_is_a_transient_error_that_is_still_an_identity_error(self) -> None:
        original = _dns_failure()
        with _get_raising(original), pytest.raises(TransientNetworkError) as excinfo:
            verify_metaculus_api_identity()
        assert isinstance(excinfo.value, ApiIdentityError), "existing `except ApiIdentityError` handlers still hold"
        assert excinfo.value.__cause__ is original
        assert "this probe carries no credential" in str(excinfo.value)

    def test_a_timeout_is_transient(self) -> None:
        with _get_raising(requests.exceptions.ConnectTimeout("slow")), pytest.raises(TransientNetworkError):
            verify_metaculus_api_identity()

    def test_a_tls_failure_stays_a_hard_identity_failure(self) -> None:
        with _get_raising(requests.exceptions.SSLError("bad certificate")), pytest.raises(ApiIdentityError) as excinfo:
            verify_metaculus_api_identity()
        assert not isinstance(excinfo.value, TransientNetworkError)

    def test_a_host_that_answers_wrongly_stays_a_hard_identity_failure(self) -> None:
        response = MagicMock(status_code=404, text="")
        with patch("requests.Session.get", return_value=response), pytest.raises(ApiIdentityError) as excinfo:
            verify_metaculus_api_identity()
        assert not isinstance(excinfo.value, TransientNetworkError)

    def test_the_mantic_host_gets_the_same_classification(self) -> None:
        with _get_raising(_dns_failure()), pytest.raises(TransientNetworkError, match=r"competitions\.mantic\.com"):
            verify_api_identity("https://competitions.mantic.com/api")


class TestManticTournamentCheck:
    def test_a_transport_failure_on_the_authenticated_check_is_transient(self) -> None:
        client = ManticClient(token="m" * 40)
        fails = patch("metaculus_bot.mantic.requests.get", side_effect=_dns_failure())
        with fails, pytest.raises(TransientNetworkError, match="cannot be confirmed"):
            client.get_tournament("series-2")

    def test_a_tls_failure_there_stays_hard(self) -> None:
        client = ManticClient(token="m" * 40)
        bad_cert = patch("metaculus_bot.mantic.requests.get", side_effect=requests.exceptions.SSLError("bad"))
        with bad_cert, pytest.raises(ApiIdentityError) as excinfo:
            client.get_tournament("series-2")
        assert not isinstance(excinfo.value, TransientNetworkError)
