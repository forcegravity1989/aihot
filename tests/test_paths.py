"""test_paths.py —— 锁死 spec §3 的四级路径解析优先级与同族解析器。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qianliyan.core import paths


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """每个用例都从「零环境变量」起步，避免外部环境污染优先级判定。"""
    for name in ("QLY_DATA_DIR", "QLY_AUTH_DIR", "QLY_BROWSER_PROFILE"):
        monkeypatch.delenv(name, raising=False)


def _fake_repo(monkeypatch, tmp_path, local=None, team=None) -> Path:
    """造一个假仓库根目录，可选写入 paths.local.json / config/paths.team.json。"""
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True, exist_ok=True)
    if local is not None:
        (root / paths.LOCAL_PATHS_FILE).write_text(
            json.dumps({"data_dir": str(local)}), encoding="utf-8"
        )
    if team is not None:
        (root / "config" / paths.TEAM_PATHS_FILE).write_text(
            json.dumps({"data_dir": str(team)}), encoding="utf-8"
        )
    monkeypatch.setattr(paths, "repo_root", lambda: root)
    return root


# =========================================================================
# 四级优先级
# =========================================================================
def test_level1_env_wins_over_everything(monkeypatch, tmp_path):
    _fake_repo(monkeypatch, tmp_path, local=tmp_path / "from-local", team=tmp_path / "from-team")
    monkeypatch.setenv("QLY_DATA_DIR", str(tmp_path / "from-env"))
    assert paths.resolve_data_dir() == tmp_path / "from-env"


def test_level2_local_json_wins_over_team(monkeypatch, tmp_path):
    _fake_repo(monkeypatch, tmp_path, local=tmp_path / "from-local", team=tmp_path / "from-team")
    assert paths.resolve_data_dir() == tmp_path / "from-local"


def test_level3_team_json_when_no_local(monkeypatch, tmp_path):
    _fake_repo(monkeypatch, tmp_path, team=tmp_path / "from-team")
    assert paths.resolve_data_dir() == tmp_path / "from-team"


def test_level4_default_home_dir(monkeypatch, tmp_path):
    _fake_repo(monkeypatch, tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    assert paths.resolve_data_dir() == home / "qianliyan-data"


def test_json_values_are_expanduser_ed(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    _fake_repo(monkeypatch, tmp_path, team="~/team-data")
    assert paths.resolve_data_dir() == home / "team-data"


def test_env_value_is_expanduser_ed(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("QLY_DATA_DIR", "~/env-data")
    assert paths.resolve_data_dir() == home / "env-data"


def test_resolve_data_dir_creates_missing_dir(monkeypatch, tmp_path):
    target = tmp_path / "deep" / "nested" / "data"
    monkeypatch.setenv("QLY_DATA_DIR", str(target))
    assert not target.exists()
    assert paths.resolve_data_dir() == target
    assert target.is_dir()


def test_broken_json_falls_through_to_next_level(monkeypatch, tmp_path):
    root = _fake_repo(monkeypatch, tmp_path, team=tmp_path / "from-team")
    (root / paths.LOCAL_PATHS_FILE).write_text("{ not json", encoding="utf-8")
    assert paths.resolve_data_dir() == tmp_path / "from-team"


def test_json_without_data_dir_key_falls_through(monkeypatch, tmp_path):
    root = _fake_repo(monkeypatch, tmp_path, team=tmp_path / "from-team")
    (root / paths.LOCAL_PATHS_FILE).write_text(json.dumps({"other": 1}), encoding="utf-8")
    assert paths.resolve_data_dir() == tmp_path / "from-team"


# =========================================================================
# 同族解析器
# =========================================================================
def test_auth_dir_defaults_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("QLY_DATA_DIR", str(tmp_path / "data"))
    auth = paths.resolve_auth_dir()
    assert auth == tmp_path / "data" / "auth"
    assert auth.is_dir()


def test_auth_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("QLY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QLY_AUTH_DIR", str(tmp_path / "elsewhere"))
    assert paths.resolve_auth_dir() == tmp_path / "elsewhere"


def test_browser_profile_defaults_under_auth_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("QLY_DATA_DIR", str(tmp_path / "data"))
    profile = paths.resolve_browser_profile_dir()
    assert profile == tmp_path / "data" / "auth" / "company-profile"
    assert profile.is_dir()


def test_browser_profile_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("QLY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QLY_BROWSER_PROFILE", str(tmp_path / "edge-profile"))
    assert paths.resolve_browser_profile_dir() == tmp_path / "edge-profile"


def test_browser_profile_follows_auth_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("QLY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QLY_AUTH_DIR", str(tmp_path / "auth-x"))
    assert paths.resolve_browser_profile_dir() == tmp_path / "auth-x" / "company-profile"


def test_data_path_joins_and_creates_parents(tmp_data_dir):
    target = paths.data_path("items", "2026-08-25", "aihot.jsonl")
    assert target == tmp_data_dir / "items" / "2026-08-25" / "aihot.jsonl"
    assert target.parent.is_dir()
    assert not target.exists()  # 只建父目录，不建文件


# =========================================================================
# 仓库目录与配置加载
# =========================================================================
def test_repo_root_points_at_project_root():
    root = paths.repo_root()
    assert (root / "qianliyan" / "core" / "paths.py").is_file()
    assert (root / "pyproject.toml").is_file()


def test_config_and_templates_dir_under_repo_root():
    assert paths.config_dir() == paths.repo_root() / "config"
    assert paths.templates_dir() == paths.repo_root() / "templates"


def test_team_paths_json_is_shipped_and_uses_no_personal_path():
    payload = json.loads((paths.config_dir() / paths.TEAM_PATHS_FILE).read_text(encoding="utf-8"))
    assert payload["data_dir"].startswith("~")


def test_load_yaml_config_missing_file_returns_empty(monkeypatch, tmp_path):
    _fake_repo(monkeypatch, tmp_path)
    assert paths.load_yaml_config("nope") == {}
    assert paths.load_yaml_config("nope.yaml") == {}


def test_load_yaml_config_reads_mapping(monkeypatch, tmp_path):
    root = _fake_repo(monkeypatch, tmp_path)
    (root / "config" / "demo.yaml").write_text(
        "sources:\n  - name: A\n    weight: 0.9\n", encoding="utf-8"
    )
    cfg = paths.load_yaml_config("demo")
    assert cfg["sources"][0]["name"] == "A"
    assert cfg["sources"][0]["weight"] == 0.9


def test_load_yaml_config_empty_file_returns_empty(monkeypatch, tmp_path):
    root = _fake_repo(monkeypatch, tmp_path)
    (root / "config" / "blank.yaml").write_text("", encoding="utf-8")
    assert paths.load_yaml_config("blank") == {}
