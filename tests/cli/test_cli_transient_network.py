"""``cli.main`` skips a run, with exit 0 and one marker, when only the network failed.

Both places a blip can end a run are covered: the identity preflight at startup and the question
fetch. A hard failure (a wrong host, a TLS error, any other exception) must still propagate, and the
skip must not have spent or built anything it did not need to.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from urllib3.exceptions import MaxRetryError, NameResolutionError

from metaculus_bot.api_preflight import ApiIdentityError, TransientNetworkError
from metaculus_bot.cli import main as cli_main
from metaculus_bot.credit_telemetry import reset_role_spend
from scripts.telemetry.markers import parse_log_text
from tests.cli_test_helpers import _cli_main_test_mode, _forecaster_class, asyncio_run_stub


@pytest.fixture(autouse=True)
def _clean_spend_ledger() -> Iterator[None]:
    """The ledger is process-global, and the fetch stage reads it, so no earlier test may leave rows."""
    reset_role_spend()
    yield
    reset_role_spend()


def _dns_failure() -> requests.exceptions.ConnectionError:
    resolution = NameResolutionError("www.metaculus.com", MagicMock(), socket.gaierror(-3, "Temporary failure"))
    return requests.exceptions.ConnectionError(MaxRetryError(MagicMock(), "/api/posts/", reason=resolution))


def _raise(exc: BaseException):
    def _side_effect(*_args: object, **_kwargs: object) -> None:
        raise exc

    return asyncio_run_stub(_side_effect)


_META = {
    "run_id": "999",
    "workflow": "tournament",
    "artifact": "research-999",
    "run_date": "2026-10-02T07:43:00Z",
    "log_file": "run.log",
}


def _markers(caplog: pytest.LogCaptureFixture) -> list[dict]:
    """The ``transient_network_skip`` records the real marker registry harvests from the captured log."""
    return parse_log_text(caplog.text, **_META).get("transient_network_skip", [])


class TestPreflightBlip:
    def test_a_transient_preflight_failure_skips_the_run_with_exit_zero(self, caplog: pytest.LogCaptureFixture) -> None:
        forecaster_class = _forecaster_class()
        caplog.set_level(logging.WARNING)
        blip = patch("metaculus_bot.cli.verify_metaculus_api_identity", side_effect=TransientNetworkError("no DNS"))
        with (
            _cli_main_test_mode(alertable_count=0, forecaster_class=forecaster_class, mode="tournament"),
            blip,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_main()
        assert exc_info.value.code == 0
        forecaster_class.assert_not_called()  # nothing was built, so nothing can have been spent
        markers = _markers(caplog)
        assert [(m["stage"], m["error"]) for m in markers] == [("preflight", "TransientNetworkError")]

    def test_a_wrong_host_is_still_a_hard_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        parked = patch("metaculus_bot.cli.verify_metaculus_api_identity", side_effect=ApiIdentityError("parked"))
        with _cli_main_test_mode(alertable_count=0, mode="tournament"), parked, pytest.raises(ApiIdentityError):
            cli_main()
        assert _markers(caplog) == []

    def test_mantic_mode_skips_on_the_same_blip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tests.cli_test_helpers import _mantic_env  # only this test needs the Mantic env

        _mantic_env(monkeypatch)
        blip = patch("metaculus_bot.cli.verify_api_identity", side_effect=TransientNetworkError("no DNS"))
        with _cli_main_test_mode(alertable_count=0, mode="mantic"), blip, pytest.raises(SystemExit) as exc_info:
            cli_main()
        assert exc_info.value.code == 0


class TestFetchBlip:
    def test_a_connection_failure_fetching_questions_skips_the_run(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        fetch_fails = patch("metaculus_bot.cli.asyncio.run", _raise(_dns_failure()))
        with _cli_main_test_mode(alertable_count=0) as telemetry, fetch_fails, pytest.raises(SystemExit) as exc_info:
            cli_main()
        assert exc_info.value.code == 0
        assert [(m["stage"], m["error"]) for m in _markers(caplog)] == [("fetch", "ConnectionError")]
        telemetry.log_end_and_check_floor.assert_called_once()  # the end-of-run accounting still ran

    def test_a_blip_is_not_reported_as_a_failed_run(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        slow = patch("metaculus_bot.cli.asyncio.run", _raise(requests.exceptions.ReadTimeout("slow")))
        # alertable_count=1 would normally exit 1; a skipped blip must not.
        with _cli_main_test_mode(alertable_count=1), slow, pytest.raises(SystemExit) as exc_info:
            cli_main()
        assert exc_info.value.code == 0

    @pytest.mark.parametrize(
        "exc",
        [
            requests.exceptions.SSLError("certificate verify failed"),
            requests.exceptions.HTTPError("500 Server Error"),
            ValueError("a real bug"),
        ],
        ids=lambda e: type(e).__name__,
    )
    def test_anything_else_still_fails_the_run(self, exc: BaseException, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        failing_fetch = patch("metaculus_bot.cli.asyncio.run", _raise(exc))
        with _cli_main_test_mode(alertable_count=0), failing_fetch, pytest.raises(type(exc)):
            cli_main()
        assert _markers(caplog) == []


class TestAConnectionErrorAfterSpendIsNotASkip:
    """The fetch stage may only be called a blip while nothing has been spent."""

    def test_a_connection_error_after_a_booked_llm_call_fails_the_run(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        booked = patch("metaculus_bot.cli.role_spend_rows", return_value=[SimpleNamespace(calls=3)])
        escaping = patch("metaculus_bot.cli.asyncio.run", _raise(_dns_failure()))
        with (
            _cli_main_test_mode(alertable_count=0),
            booked,
            escaping,
            pytest.raises(requests.exceptions.ConnectionError),
        ):
            cli_main()
        assert _markers(caplog) == [], "a run that spent money did not skip"

    def test_zero_call_rows_do_not_count_as_spend(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        idle = patch("metaculus_bot.cli.role_spend_rows", return_value=[SimpleNamespace(calls=0)])
        escaping = patch("metaculus_bot.cli.asyncio.run", _raise(_dns_failure()))
        with _cli_main_test_mode(alertable_count=0), idle, escaping, pytest.raises(SystemExit) as exc_info:
            cli_main()
        assert exc_info.value.code == 0
        assert [m["stage"] for m in _markers(caplog)] == ["fetch"]

    def test_the_preflight_stage_is_unaffected_by_the_ledger(self, caplog: pytest.LogCaptureFixture) -> None:
        """The preflight runs before any model call, so it never consults the ledger."""
        caplog.set_level(logging.WARNING)
        booked = patch("metaculus_bot.cli.role_spend_rows", return_value=[SimpleNamespace(calls=9)])
        blip = patch("metaculus_bot.cli.verify_metaculus_api_identity", side_effect=TransientNetworkError("no DNS"))
        with _cli_main_test_mode(alertable_count=0, mode="tournament"), booked, blip, pytest.raises(SystemExit):
            cli_main()
        assert [m["stage"] for m in _markers(caplog)] == ["preflight"]
