from drissionpage_mcp.services import role_sessions


class _Tab:
    tab_id = "role-tab-1"
    url = "https://example.test/scm-admin"


def _prepare_role(monkeypatch):
    tab = _Tab()
    monkeypatch.setattr(role_sessions, "_roles", {})
    monkeypatch.setattr(
        role_sessions.browser_session,
        "create_context",
        lambda proxy=None: (41, tab),
    )
    monkeypatch.setattr(
        role_sessions.browser_session,
        "context_exists",
        lambda context_id: context_id == 41,
    )
    monkeypatch.setattr(
        role_sessions.browser_session,
        "switch_context",
        lambda context_id: tab if context_id == 41 else None,
    )
    monkeypatch.setattr(
        role_sessions.browser_session,
        "list_contexts",
        lambda: [{"context_id": 41, "tab_ids": [tab.tab_id], "is_active": True}],
    )
    return tab


def test_role_login_uses_only_role_credentials_and_hides_password(monkeypatch):
    tab = _prepare_role(monkeypatch)
    username_env, password_env = role_sessions.credential_env_names("dept_manager")
    monkeypatch.setenv(username_env, "manager@example.test")
    monkeypatch.setenv(password_env, "manager-secret")
    received = {}

    def fake_login(target_tab, username, password, credential_envs):
        received.update({
            "tab": target_tab,
            "username": username,
            "password": password,
            "credential_envs": credential_envs,
        })
        return {"ok": True, "cookies": ["SESSION"], "url": target_tab.url}

    monkeypatch.setattr(role_sessions.session_auth, "login_ocr_for_tab", fake_login)

    opened = role_sessions.open_role("dept_manager")
    logged_in = role_sessions.login_role("dept_manager")
    activated = role_sessions.activate_role("dept_manager")
    listed = role_sessions.list_roles()

    assert opened["ok"] is True
    assert logged_in["ok"] is True
    assert activated == {
        "ok": True,
        "role_id": "dept_manager",
        "context_id": 41,
        "url": tab.url,
    }
    assert received == {
        "tab": tab,
        "username": "manager@example.test",
        "password": "manager-secret",
        "credential_envs": (username_env, password_env),
    }
    assert "manager-secret" not in repr(opened)
    assert "manager-secret" not in repr(logged_in)
    assert listed == [{
        "role_id": "dept_manager",
        "context_id": 41,
        "tab_id": "role-tab-1",
        "state": "ready",
        "is_active": True,
        "authenticated": True,
        "has_proxy": False,
        "credentials_configured": True,
        "credential_env": {"username": username_env, "password": password_env},
    }]


def test_role_login_does_not_fall_back_to_default_credentials(monkeypatch):
    _prepare_role(monkeypatch)
    username_env, password_env = role_sessions.credential_env_names("requester")
    monkeypatch.delenv(username_env, raising=False)
    monkeypatch.delenv(password_env, raising=False)
    monkeypatch.setenv("HL_USERNAME", "default-user")
    monkeypatch.setenv("HL_USERPWD", "default-secret")

    assert role_sessions.open_role("requester")["ok"] is True
    result = role_sessions.login_role("requester")

    assert result["ok"] is False
    assert "HL_ROLE_REQUESTER_USERNAME" in result["reason"]
    assert "HL_ROLE_REQUESTER_USERPWD" in result["reason"]


def test_role_login_failure_does_not_echo_password(monkeypatch):
    _prepare_role(monkeypatch)
    username_env, password_env = role_sessions.credential_env_names("finance_approver")
    monkeypatch.setenv(username_env, "finance@example.test")
    monkeypatch.setenv(password_env, "finance-secret")
    monkeypatch.setattr(
        role_sessions.session_auth,
        "login_ocr_for_tab",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("finance-secret")),
    )

    assert role_sessions.open_role("finance_approver")["ok"] is True
    result = role_sessions.login_role("finance_approver")

    assert result == {
        "ok": False,
        "reason": "角色登录失败，请检查账号、网络或服务日志",
        "role_id": "finance_approver",
    }
    assert "finance-secret" not in repr(result)


def test_start_role_opens_and_logs_in_with_one_public_operation(monkeypatch):
    tab = _prepare_role(monkeypatch)
    username_env, password_env = role_sessions.credential_env_names("requester")
    monkeypatch.setenv(username_env, "requester@example.test")
    monkeypatch.setenv(password_env, "requester-secret")
    monkeypatch.setattr(
        role_sessions.session_auth,
        "login_ocr_for_tab",
        lambda *_args, **_kwargs: {"ok": True, "url": tab.url, "cookies": ["SESSION"]},
    )

    result = role_sessions.start_role("requester")

    assert result["ok"] is True
    assert result["stage"] == "ready"
    assert result["authenticated"] is True
    assert result["credential_env"] == {"username": username_env, "password": password_env}
    assert "requester-secret" not in repr(result)


def test_start_role_cleans_up_context_when_login_fails(monkeypatch):
    _prepare_role(monkeypatch)
    closed = []
    monkeypatch.setattr(
        role_sessions.browser_session,
        "close_context",
        lambda context_id: closed.append(context_id) or {"ok": True},
    )

    result = role_sessions.start_role("finance_approver")

    assert result["ok"] is False
    assert result["stage"] == "login"
    assert result["cleanup"] == {"ok": True, "reason": ""}
    assert closed == [41]
    assert role_sessions.list_roles() == []
