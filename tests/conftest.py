"""What the tests of this folder get, beside the repository's own conftest.

The tests that drive a page skip when they cannot: no Playwright, no
Chromium, no way to the CDN the page takes KaTeX from.  On a developer's
machine that is right - the rest of the suite still says something.  In CI's
browser job it meant a job that was green having run nothing: one request to
the CDN that timed out while the tests were collected, and the whole front
end's suite was skipped.

With ``SYMPY_EDITOR_REQUIRE_BROWSER=1`` in the environment - the workflow
sets it where the browser is what the job is for - such a skip is a failure,
which says what was missing.  Every other skip stays a skip: an add-on's
package that is not installed, a slow test that was not asked for.
"""
import os
import re

import pytest

REQUIRE_BROWSER = "SYMPY_EDITOR_REQUIRE_BROWSER"

#: The reasons the tests give when there is no browser to drive, or no network
#: for the page it would show: "could not import 'playwright.sync_api'",
#: "playwright unavailable", "chromium not available", "KaTeX CDN not
#: reachable", "a CDN is not reachable", "needs Playwright and the KaTeX CDN".
NO_BROWSER = re.compile(r"playwright|chromium|\bCDN\b", re.IGNORECASE)


def _reason(report) -> str:
    """Why a report was skipped: its longrepr is (file, line, reason)."""
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2])
    return str(longrepr)


def _refuse(report) -> None:
    if not report.skipped or os.environ.get(REQUIRE_BROWSER, "0") in ("", "0"):
        return
    reason = _reason(report)
    if hasattr(report, "wasxfail") or not NO_BROWSER.search(reason):
        return
    report.outcome = "failed"
    report.longrepr = f"{REQUIRE_BROWSER} is set, and this was about to be skipped: {reason}"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    _refuse(outcome.get_result())


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    """A module that skips as it is imported (``importorskip``) never gets as
    far as a test: it is refused here."""
    outcome = yield
    _refuse(outcome.get_result())
