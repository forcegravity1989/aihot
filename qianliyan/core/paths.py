"""core/paths.py —— 四级路径解析与配置加载（数据目录的唯一权威入口）。

优先级铁律（spec §3.1）::

    env QLY_DATA_DIR  >  <repo根>/paths.local.json  >  <repo根>/config/paths.team.json
                      >  ~/qianliyan-data

``paths.local.json`` 是个人覆盖（入 .gitignore），``config/paths.team.json`` 是团队默认的
唯一来源——任何模块都禁止写死个人路径。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = "~/qianliyan-data"
LOCAL_PATHS_FILE = "paths.local.json"
TEAM_PATHS_FILE = "paths.team.json"


def repo_root() -> Path:
    """仓库根目录（``qianliyan/core/paths.py`` 上溯两级）。"""
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    """仓库 ``config/`` 目录（配置唯一权威层）。"""
    return repo_root() / "config"


def templates_dir() -> Path:
    """仓库 ``templates/`` 目录。"""
    return repo_root() / "templates"


def _read_data_dir_from(path: Path) -> Optional[str]:
    """从 ``{"data_dir": "~/some/path"}`` 形式的 json 读取配置值。"""
    try:
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("读取路径配置失败 %s: %s", path, exc)
        return None
    if not isinstance(payload, dict):
        logger.warning("路径配置格式非法（应为 object）: %s", path)
        return None
    value = payload.get("data_dir")
    if not value:
        return None
    return str(value)


def resolve_data_dir() -> Path:
    """四级优先级解析数据根目录，目录不存在则自动创建。"""
    candidate = os.environ.get("QLY_DATA_DIR")
    origin = "env QLY_DATA_DIR"
    if not candidate:
        root = repo_root()
        candidate = _read_data_dir_from(root / LOCAL_PATHS_FILE)
        origin = LOCAL_PATHS_FILE
    if not candidate:
        candidate = _read_data_dir_from(config_dir() / TEAM_PATHS_FILE)
        origin = "config/" + TEAM_PATHS_FILE
    if not candidate:
        candidate = DEFAULT_DATA_DIR
        origin = "内置默认值"

    data_dir = Path(str(candidate)).expanduser()
    _ensure_dir(data_dir)
    logger.debug("data_dir=%s（来源: %s）", data_dir, origin)
    return data_dir


def resolve_auth_dir() -> Path:
    """内眼登录态目录：env ``QLY_AUTH_DIR`` > ``<data_dir>/auth``。"""
    candidate = os.environ.get("QLY_AUTH_DIR")
    auth_dir = Path(candidate).expanduser() if candidate else resolve_data_dir() / "auth"
    _ensure_dir(auth_dir)
    return auth_dir


def resolve_browser_profile_dir() -> Path:
    """常驻浏览器 profile 目录：env ``QLY_BROWSER_PROFILE`` > ``<auth_dir>/company-profile``。"""
    candidate = os.environ.get("QLY_BROWSER_PROFILE")
    profile_dir = (
        Path(candidate).expanduser() if candidate else resolve_auth_dir() / "company-profile"
    )
    _ensure_dir(profile_dir)
    return profile_dir


def data_path(*parts: Any) -> Path:
    """拼接数据目录下的路径，并自动建好父目录。"""
    path = resolve_data_dir().joinpath(*[str(p) for p in parts])
    _ensure_dir(path.parent)
    return path


def load_yaml_config(name: str) -> Dict[str, Any]:
    """从 ``config/`` 读取 YAML 配置；文件缺失或为空时返回 ``{}``。"""
    filename = str(name)
    if not filename.endswith((".yaml", ".yml")):
        filename += ".yaml"
    path = config_dir() / filename
    if not path.is_file():
        logger.debug("配置文件不存在，返回空配置: %s", path)
        return {}
    try:
        import yaml  # pyyaml 属于核心依赖，惰性导入只为让 import 本模块永不失败
    except ImportError:  # pragma: no cover - 依赖缺失属部署问题
        logger.warning("缺少 pyyaml，无法读取配置 %s（pip install pyyaml）", path)
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = yaml.safe_load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("解析配置失败 %s: %s", path, exc)
        return {}
    except Exception as exc:  # yaml.YAMLError 等
        logger.warning("解析配置失败 %s: %s", path, exc)
        return {}
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        logger.warning("配置根节点应为 mapping: %s", path)
        return {}
    return payload


def _ensure_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("创建目录失败 %s: %s", path, exc)
