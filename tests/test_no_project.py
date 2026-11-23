"""Tests for No Project (general chat) default mode and project selection controls."""

from __future__ import annotations

import pocket_agent.app as app_mod
import pocket_agent.config as config_mod
from pocket_agent.agents import BUILTIN_AGENTS
from pocket_agent.app import PocketAgentApp
from pocket_agent.config import AppConfig, resolve_session_cwd
from pocket_agent.ui.drawer import ChatDrawer
from pocket_agent.ui.settings_page import SettingsPage


def test_app_config_defaults_to_no_project_and_roundtrips(tmp_path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg = AppConfig.load(cfg_path)
    assert cfg.workspace == ""
    assert cfg.start_without_project is True
    assert cfg.recent_workspaces == []

    proj = tmp_path / "my-proj"
    proj.mkdir()
    cfg.workspace = str(proj)
    cfg.record_recent_workspace(str(proj))
    cfg.start_without_project = False
    cfg.save(cfg_path)

    reloaded = AppConfig.load(cfg_path)
    assert reloaded.workspace == str(proj)
    assert reloaded.start_without_project is False
    assert reloaded.recent_workspaces == [str(proj)]


def test_resolve_session_cwd_uses_isolated_scratch_when_no_project(tmp_path) -> None:
    scratch = tmp_path / "scratch"
    assert not scratch.exists()

    cwd_none = resolve_session_cwd("", no_project_dir=scratch)
    assert cwd_none == str(scratch)
    assert scratch.is_dir()

    proj = tmp_path / "repo"
    proj.mkdir()
    assert resolve_session_cwd(str(proj), no_project_dir=scratch) == str(proj)
    assert resolve_session_cwd(str(tmp_path / "missing"), no_project_dir=scratch) == str(scratch)


def test_drawer_and_settings_no_project_controls(qapp, tmp_path) -> None:
    proj = tmp_path / "alpha"
    proj.mkdir()

    drawer = ChatDrawer(
        agents=list(BUILTIN_AGENTS),
        current_agent_id="agy",
        workspace="",
        start_without_project=True,
        recent_workspaces=[str(proj)],
    )
    selected_ws: list[str] = []
    default_toggles: list[bool] = []
    drawer.workspace_selected.connect(selected_ws.append)
    drawer.start_without_project_toggled.connect(default_toggles.append)

    assert drawer._cwd_btn.text() == "No Project"
    assert drawer._settings_page._workspace_val.text() == "No project (general chat)"
    assert drawer._settings_page._clear_ws_btn.isEnabled() is False
    assert drawer._settings_page._no_project_default_cb.isChecked() is True

    drawer._select_workspace_direct(str(proj))
    assert drawer._cwd_btn.text() == "alpha"
    assert drawer._settings_page._workspace_val.text() == str(proj)
    assert drawer._settings_page._clear_ws_btn.isEnabled() is True
    assert selected_ws == [str(proj)]

    drawer._settings_page._clear_ws_btn.click()
    assert drawer._cwd_btn.text() == "No Project"
    assert drawer._settings_page._clear_ws_btn.isEnabled() is False
    assert selected_ws == [str(proj), ""]

    drawer._settings_page._no_project_default_cb.setChecked(False)
    assert default_toggles == [False]
    drawer.set_start_without_project(True)
    assert drawer._settings_page._no_project_default_cb.isChecked() is True
    assert default_toggles == [False]

    drawer.close()
    drawer.deleteLater()
    qapp.processEvents()


def test_settings_page_no_project_checkbox_and_clear_button(qapp) -> None:
    page = SettingsPage(workspace="C:/some/project", start_without_project=True)
    cleared: list[bool] = []
    toggled: list[bool] = []
    page.workspace_clear_requested.connect(lambda: cleared.append(True))
    page.start_without_project_toggled.connect(toggled.append)

    assert page._clear_ws_btn.isEnabled() is True
    page._clear_ws_btn.click()
    assert cleared == [True]

    page._no_project_default_cb.setChecked(False)
    assert toggled == [False]

    page.deleteLater()
    qapp.processEvents()


def test_app_starts_without_project_by_default_and_switches(qapp, monkeypatch, tmp_path) -> None:
    proj = tmp_path / "sample-repo"
    proj.mkdir()
    scratch = tmp_path / "scratch"

    monkeypatch.setattr(config_mod, "NO_PROJECT_DIR", scratch)
    monkeypatch.setattr(app_mod, "resolve_session_cwd", lambda ws: resolve_session_cwd(ws, no_project_dir=scratch))
    monkeypatch.setattr(
        AppConfig,
        "load",
        classmethod(
            lambda cls: AppConfig(
                workspace=str(proj),
                start_without_project=True,
                check_updates_on_startup=False,
            )
        ),
    )
    monkeypatch.setattr(AppConfig, "save", lambda self: None)

    connect_calls: list[str] = []
    new_session_calls: list[str] = []
    monkeypatch.setattr(app_mod.AcpBridge, "connect_agent", lambda self, spec, cwd: connect_calls.append(cwd))
    monkeypatch.setattr(app_mod.AcpBridge, "new_session", lambda self, cwd: new_session_calls.append(cwd))

    app = PocketAgentApp(qapp)
    try:
        assert app._active_workspace == ""
        assert connect_calls == [str(scratch)]
        assert app.drawer._cwd_btn.text() == "No Project"
        assert str(proj) in app.config.recent_workspaces

        app._switch_workspace(str(proj))
        assert app._active_workspace == str(proj)
        assert app.drawer._cwd_btn.text() == "sample-repo"
        assert new_session_calls[-1] == str(proj)

        app._switch_workspace("")
        assert app._active_workspace == ""
        assert app.drawer._cwd_btn.text() == "No Project"
        assert new_session_calls[-1] == str(scratch)
    finally:
        app._working_phrase_timer.stop()
        app._hotkey_filter.unregister()
        qapp.removeNativeEventFilter(app._hotkey_filter)
        app.voice.shutdown()
        app.bridge.shutdown()
        app.pet.bubble.close()
        app.drawer.close()
        app.pet.close_tray()
        app.pet.close()
        app.deleteLater()
        qapp.processEvents()


def test_app_respects_remembered_project_when_default_no_project_disabled(qapp, monkeypatch, tmp_path) -> None:
    proj = tmp_path / "saved-repo"
    proj.mkdir()
    scratch = tmp_path / "scratch"

    monkeypatch.setattr(config_mod, "NO_PROJECT_DIR", scratch)
    monkeypatch.setattr(app_mod, "resolve_session_cwd", lambda ws: resolve_session_cwd(ws, no_project_dir=scratch))
    monkeypatch.setattr(
        AppConfig,
        "load",
        classmethod(
            lambda cls: AppConfig(
                workspace=str(proj),
                start_without_project=False,
                check_updates_on_startup=False,
            )
        ),
    )
    monkeypatch.setattr(AppConfig, "save", lambda self: None)

    connect_calls: list[str] = []
    monkeypatch.setattr(app_mod.AcpBridge, "connect_agent", lambda self, spec, cwd: connect_calls.append(cwd))

    app = PocketAgentApp(qapp)
    try:
        assert app._active_workspace == str(proj)
        assert connect_calls == [str(proj)]
        assert app.drawer._cwd_btn.text() == "saved-repo"
    finally:
        app._working_phrase_timer.stop()
        app._hotkey_filter.unregister()
        qapp.removeNativeEventFilter(app._hotkey_filter)
        app.voice.shutdown()
        app.bridge.shutdown()
        app.pet.bubble.close()
        app.drawer.close()
        app.pet.close_tray()
        app.pet.close()
        app.deleteLater()
        qapp.processEvents()
