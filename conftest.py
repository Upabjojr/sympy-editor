"""What every test in this repository gets.

A page keeps things - its sessions, which add-ons are on, what an add-on saves
of its own - and, when Python serves it, the server keeps them in the user's
state directory (``sympy_editor.server.default_store``).  A test run must not
write there, must not read what the developer's own editor left, and one test
must not find what another kept: every test gets a state directory of its own,
thrown away with it.
"""
import pytest


@pytest.fixture(autouse=True)
def _state_dir_of_its_own(tmp_path_factory, monkeypatch):
    state = tmp_path_factory.mktemp("state")
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    return state
