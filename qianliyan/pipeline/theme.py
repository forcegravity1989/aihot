"""pipeline/theme.py —— 设计系统 CSS 的唯一入口。

``templates/_theme.css`` 是全站 design tokens + 共用组件的**唯一源**。minitpl 刻意不支持
``{% include %}``（模板只当数据看、不做文件系统解析），所以由本模块把那份 CSS 读进来，
以 ``{{ theme_css|safe }}`` 注入各模板。

为什么是内联而不是 ``<link>``：千里眼的产物是**单文件自包含**静态页，要能在邮件客户端、
聊天内嵌预览、离线文件面板里打开——一旦引外部样式表，这些环境全部裸奔。

读取结果按 ``(路径, mtime)`` 缓存：一次 ``daily_digest_all`` 要渲染几十个详情页，
不该把同一份 CSS 读几十遍；mtime 参与键，改完 CSS 重跑立刻生效，不用重启进程。
"""

from __future__ import annotations

import logging
from typing import Dict, Tuple

from ..core import paths

logger = logging.getLogger("qianliyan.pipeline.theme")

#: 设计系统文件名（下划线前缀 = 「不是可独立渲染的模板，是被引用的片段」）
THEME_FILE = "_theme.css"

_CACHE: Dict[Tuple[str, int], str] = {}


def load_theme_css() -> str:
    """读 ``templates/_theme.css`` 并返回其文本；缺失时抛 ``FileNotFoundError``。

    缺文件是**部署问题不是数据问题**——静默返回空串会渲染出一堆无样式的裸 HTML，
    比直接报错难查得多，所以这里不做兜底降级。
    """
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
