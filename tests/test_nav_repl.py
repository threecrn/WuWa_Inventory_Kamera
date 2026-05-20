from __future__ import annotations

import sys
from types import ModuleType

from wuwa_inventory_kamera.cli import nav


class _FakeStream:
    def __init__(self, is_tty: bool) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def _install_fake_prompt_toolkit(monkeypatch, prompt_result: str = 'prompt:>>> '):
    prompt_toolkit = ModuleType('prompt_toolkit')
    prompt_toolkit_history = ModuleType('prompt_toolkit.history')
    calls: dict[str, object] = {}

    class FakeHistory:
        pass

    class FakeSession:
        def __init__(self, *, history=None) -> None:
            calls['history'] = history

        def prompt(self, prompt: str) -> str:
            calls['prompt'] = prompt
            return prompt_result

    prompt_toolkit.PromptSession = FakeSession
    prompt_toolkit_history.InMemoryHistory = FakeHistory

    monkeypatch.setitem(sys.modules, 'prompt_toolkit', prompt_toolkit)
    monkeypatch.setitem(sys.modules, 'prompt_toolkit.history', prompt_toolkit_history)
    return calls, FakeHistory


def test_build_repl_readfunc_prefers_prompt_toolkit_on_tty(monkeypatch) -> None:
    calls, fake_history = _install_fake_prompt_toolkit(monkeypatch)
    monkeypatch.setattr(nav.sys, 'stdin', _FakeStream(True))
    monkeypatch.setattr(nav.sys, 'stdout', _FakeStream(True))

    readfunc = nav._build_repl_readfunc()

    assert readfunc('>>> ') == 'prompt:>>> '
    assert isinstance(calls['history'], fake_history)


def test_build_repl_readfunc_prefers_prompt_toolkit_for_windows_pty(monkeypatch) -> None:
    _install_fake_prompt_toolkit(monkeypatch)
    monkeypatch.setattr(nav.sys, 'stdin', _FakeStream(True))
    monkeypatch.setattr(nav.sys, 'stdout', _FakeStream(False))
    monkeypatch.setenv('MSYSTEM', 'MINGW64')
    monkeypatch.setenv('TERM', 'xterm-256color')

    readfunc = nav._build_repl_readfunc()

    assert readfunc('>>> ') == 'prompt:>>> '


def test_build_repl_readfunc_strips_ansi_sequences(monkeypatch) -> None:
    _install_fake_prompt_toolkit(monkeypatch, prompt_result='\x1b[Afocus_window()\x1b[I')
    monkeypatch.setattr(nav.sys, 'stdin', _FakeStream(True))
    monkeypatch.setattr(nav.sys, 'stdout', _FakeStream(True))

    readfunc = nav._build_repl_readfunc()

    assert readfunc('>>> ') == 'focus_window()'


def test_build_repl_readfunc_uses_plain_input_without_tty(monkeypatch) -> None:
    monkeypatch.setattr(nav.sys, 'stdin', _FakeStream(False))
    monkeypatch.setattr(nav.sys, 'stdout', _FakeStream(True))

    assert nav._build_repl_readfunc() is input