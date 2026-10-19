from __future__ import annotations

import html
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = ["TemplateError", "Template", "compile_template", "render"]

_TOKEN_RE = re.compile(r"\{\{(.*?)\}\}|\{%(.*?)%\}", re.DOTALL)
_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*$")
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FOR_RE = re.compile(r"^for\s+(\S+)\s+in\s+(\S+)$")
_IF_RE = re.compile(r"^if\s+(not\s+)?(\S+)$")

_SUPPORTED_FILTERS = ("safe",)
_MISSING = object()


class TemplateError(ValueError):
    pass


class _TextNode(object):
    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text

    def render(self, ctx: "_Context", out: List[str]) -> None:
        out.append(self.text)


class _VarNode(object):
    __slots__ = ("path", "safe")

    def __init__(self, path: str, safe: bool) -> None:
        self.path = path
        self.safe = safe

    def render(self, ctx: "_Context", out: List[str]) -> None:
        text = _stringify(ctx.resolve(self.path))
        out.append(text if self.safe else html.escape(text, quote=True))


class _ForNode(object):
    __slots__ = ("var", "path", "body")

    def __init__(self, var: str, path: str, body: Sequence[Any]) -> None:
        self.var = var
        self.path = path
        self.body = list(body)

    def render(self, ctx: "_Context", out: List[str]) -> None:
        for value in _iterate(ctx.resolve(self.path)):
            ctx.push({self.var: value})
            try:
                for node in self.body:
                    node.render(ctx, out)
            finally:
                ctx.pop()


class _IfNode(object):
    __slots__ = ("path", "negate", "body")

    def __init__(self, path: str, negate: bool, body: Sequence[Any]) -> None:
        self.path = path
        self.negate = negate
        self.body = list(body)

    def render(self, ctx: "_Context", out: List[str]) -> None:
        truthy = bool(ctx.resolve(self.path))
        if truthy != self.negate:
            for node in self.body:
                node.render(ctx, out)


class _Context(object):
    __slots__ = ("_scopes",)

    def __init__(self, base: Optional[Dict[str, Any]] = None) -> None:
        self._scopes = [dict(base or {})]

    def push(self, scope: Dict[str, Any]) -> None:
        self._scopes.append(scope)

    def pop(self) -> None:
        self._scopes.pop()

    def resolve(self, path: str) -> Any:
        parts = path.split(".")
        value = _MISSING
        for scope in reversed(self._scopes):
            if parts[0] in scope:
                value = scope[parts[0]]
                break
        if value is _MISSING:
            return None
        for part in parts[1:]:
            value = _lookup(value, part)
            if value is None:
                return None
        return value


def _lookup(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    if isinstance(obj, (list, tuple)):
        if key.isdigit():
            index = int(key)
            if 0 <= index < len(obj):
                return obj[index]
        return None
    return getattr(obj, key, None)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _iterate(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return []
    if isinstance(value, dict):
        return list(value.items())
    try:
        return list(value)
    except TypeError:
        logger.debug("minitpl: 不可迭代的循环对象 %r", type(value))
        return []


def _tokenize(text: str) -> List[Tuple[str, str]]:
    tokens: List[Tuple[str, str]] = []
    pos = 0
    for match in _TOKEN_RE.finditer(text):
        if match.start() > pos:
            tokens.append(("text", text[pos:match.start()]))
        if match.group(1) is not None:
            tokens.append(("var", match.group(1).strip()))
        else:
            tokens.append(("tag", (match.group(2) or "").strip()))
        pos = match.end()
    if pos < len(text):
        tokens.append(("text", text[pos:]))
    return tokens


def _check_path(path: str, where: str) -> str:
    if not _PATH_RE.match(path or ""):
        raise TemplateError("非法取值路径 {0!r}（{1}）".format(path, where))
    return path


def _parse_var(expr: str) -> Tuple[str, bool]:
    parts = [piece.strip() for piece in (expr or "").split("|")]
    path = _check_path(parts[0], "{{ }}")
    safe = False
    for filter_name in parts[1:]:
        if filter_name not in _SUPPORTED_FILTERS:
            raise TemplateError("未知过滤器 {0!r}（仅支持 |safe）".format(filter_name))
        safe = True
    return path, safe


def _parse(tokens: Sequence[Tuple[str, str]], index: int, stop: Sequence[str]) -> Tuple[List[Any], int]:
    """遇到 stop 中的标签即返回，不消费该标签。"""
    nodes: List[Any] = []
    while index < len(tokens):
        kind, value = tokens[index]
        if kind == "text":
            nodes.append(_TextNode(value))
            index += 1
            continue
        if kind == "var":
            path, safe = _parse_var(value)
            nodes.append(_VarNode(path, safe))
            index += 1
            continue

        head = value.split(" ", 1)[0] if value else ""
        if head in stop:
            return nodes, index

        if head == "for":
            match = _FOR_RE.match(" ".join(value.split()))
            if not match:
                raise TemplateError("for 语法应为 {% for x in path %}，实际: " + repr(value))
            var = match.group(1)
            if not _NAME_RE.match(var):
                raise TemplateError("非法循环变量名 {0!r}".format(var))
            path = _check_path(match.group(2), "for")
            body, index = _parse(tokens, index + 1, ("endfor",))
            if index >= len(tokens):
                raise TemplateError("{% for %} 未闭合（缺少 {% endfor %}）")
            nodes.append(_ForNode(var, path, body))
            index += 1
            continue

        if head == "if":
            match = _IF_RE.match(" ".join(value.split()))
            if not match:
                raise TemplateError("if 语法应为 {% if path %} 或 {% if not path %}，实际: " + repr(value))
            negate = bool(match.group(1))
            path = _check_path(match.group(2), "if")
            body, index = _parse(tokens, index + 1, ("endif",))
            if index >= len(tokens):
                raise TemplateError("{% if %} 未闭合（缺少 {% endif %}）")
            nodes.append(_IfNode(path, negate, body))
            index += 1
            continue

        raise TemplateError("未知模板标签 {0!r}（仅支持 for/endfor/if/endif）".format(value))

    if stop:
        raise TemplateError("模板块未闭合，期待 {0}".format("/".join(stop)))
    return nodes, index


class Template(object):
    __slots__ = ("nodes",)

    def __init__(self, nodes: Sequence[Any]) -> None:
        self.nodes = list(nodes)

    def render(self, context: Optional[Dict[str, Any]] = None) -> str:
        ctx = _Context(context)
        out: List[str] = []
        for node in self.nodes:
            node.render(ctx, out)
        return "".join(out)


def compile_template(template_text: str) -> Template:
    nodes, _ = _parse(_tokenize(template_text or ""), 0, ())
    return Template(nodes)


def render(template_text: str, context: Optional[Dict[str, Any]] = None) -> str:
    return compile_template(template_text).render(context or {})
