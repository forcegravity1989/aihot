from __future__ import annotations

import logging
from typing import Dict, Tuple

from ..core import paths

logger = logging.getLogger("qianliyan.pipeline.theme")

# 下划线前缀 = 被引用的片段，不是可独立渲染的模板
THEME_FILE = "_theme.css"

_CACHE: Dict[Tuple[str, int], str] = {}


def load_theme_css() -> str:
    """缺文件是部署问题：静默返回空串会渲染出裸 HTML，更难查，所以不兜底。"""
    path = paths.templates_dir() / THEME_FILE
    if not path.is_file():
        raise FileNotFoundError("设计系统样式缺失: {0}".format(path))
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:  # pragma: no cover - stat 失败极罕见，退化为不缓存
        return path.read_text(encoding="utf-8")
    cached = _CACHE.get(key)
    if cached is None:
        cached = path.read_text(encoding="utf-8")
        _CACHE[key] = cached
        logger.debug("载入设计系统样式 %s（%d 字节）", path, len(cached))
    return cached
