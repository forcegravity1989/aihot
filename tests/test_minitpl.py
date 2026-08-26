"""test_minitpl.py —— 锁死 spec §9.1：变量取值 / HTML 转义 / |safe / for / if / 嵌套。"""

from __future__ import annotations

import pytest

from qianliyan.pipeline import minitpl


# =========================================================================
# {{ path }} 取值
# =========================================================================
def test_plain_variable():
    assert minitpl.render("你好 {{ name }}！", {"name": "千里眼"}) == "你好 千里眼！"


def test_dotted_path_walks_dict_list_and_attribute():
    class Holder(object):
        label = "属性值"

    context = {
        "a": {"b": {"c": "深层"}},
        "rows": [{"title": "第一条"}, {"title": "第二条"}],
        "obj": Holder(),
    }
    assert minitpl.render("{{ a.b.c }}", context) == "深层"
    assert minitpl.render("{{ rows.1.title }}", context) == "第二条"
    assert minitpl.render("{{ obj.label }}", context) == "属性值"


@pytest.mark.parametrize(
    "template",
    ["{{ missing }}", "{{ a.missing }}", "{{ a.b.c.d }}", "{{ rows.9.title }}", "{{ nil.x }}"],
)
def test_missing_path_renders_empty_string(template):
    context = {"a": {"b": {}}, "rows": [], "nil": None}
    assert minitpl.render(template, context) == ""


def test_none_renders_empty_but_zero_and_false_render_literally():
    assert minitpl.render("[{{ v }}]", {"v": None}) == "[]"
    assert minitpl.render("[{{ v }}]", {"v": 0}) == "[0]"
    assert minitpl.render("[{{ v }}]", {"v": 0.5}) == "[0.5]"


def test_whitespace_inside_tag_is_tolerated():
    assert minitpl.render("{{name}}|{{   name   }}", {"name": "x"}) == "x|x"


# =========================================================================
# 转义与 |safe
# =========================================================================
def test_default_escapes_html_including_quotes():
    payload = {"raw": "<script>alert('x' & \"y\")</script>"}
    out = minitpl.render("{{ raw }}", payload)
    assert "<script>" not in out
    assert out == "&lt;script&gt;alert(&#x27;x&#x27; &amp; &quot;y&quot;)&lt;/script&gt;"


def test_safe_filter_skips_escaping():
    payload = {"raw": '<b class="x">粗</b>'}
    assert minitpl.render("{{ raw|safe }}", payload) == '<b class="x">粗</b>'
    assert minitpl.render("{{ raw | safe }}", payload) == '<b class="x">粗</b>'


def test_unknown_filter_is_rejected():
    with pytest.raises(minitpl.TemplateError):
        minitpl.render("{{ x|upper }}", {"x": "a"})


# =========================================================================
# {% for %}
# =========================================================================
def test_for_loop_injects_loop_variable():
    tpl = "{% for row in rows %}<li>{{ row.title }}</li>{% endfor %}"
    out = minitpl.render(tpl, {"rows": [{"title": "A"}, {"title": "B"}]})
    assert out == "<li>A</li><li>B</li>"


def test_for_loop_over_missing_or_empty_renders_nothing():
    tpl = "|{% for row in rows %}{{ row }}{% endfor %}|"
    assert minitpl.render(tpl, {}) == "||"
    assert minitpl.render(tpl, {"rows": []}) == "||"
    assert minitpl.render(tpl, {"rows": None}) == "||"


def test_for_loop_variable_does_not_leak_after_endfor():
    tpl = "{% for x in rows %}{{ x }}{% endfor %}[{{ x }}]"
    assert minitpl.render(tpl, {"rows": [1, 2]}) == "12[]"


def test_for_loop_shadows_outer_name_then_restores():
    tpl = "{{ x }}{% for x in rows %}-{{ x }}{% endfor %}-{{ x }}"
    assert minitpl.render(tpl, {"x": "外", "rows": ["内"]}) == "外-内-外"


def test_nested_for_loops():
    tpl = "{% for g in groups %}[{{ g.name }}{% for i in g.items %}:{{ i }}{% endfor %}]{% endfor %}"
    context = {"groups": [
        {"name": "甲", "items": [1, 2]},
        {"name": "乙", "items": []},
    ]}
    assert minitpl.render(tpl, context) == "[甲:1:2][乙]"


# =========================================================================
# {% if %} / {% if not %}
# =========================================================================
@pytest.mark.parametrize(
    "value,expected",
    [(True, "Y"), (False, "N"), (None, "N"), (0, "N"), ("", "N"), ([], "N"), ("x", "Y"), ([0], "Y")],
)
def test_if_and_if_not_follow_python_truthiness(value, expected):
    tpl = "{% if flag %}Y{% endif %}{% if not flag %}N{% endif %}"
    assert minitpl.render(tpl, {"flag": value}) == expected


def test_if_on_missing_path_is_falsy():
    assert minitpl.render("{% if nope %}Y{% endif %}-", {}) == "-"


def test_nested_if_inside_for():
    tpl = (
        "{% for row in rows %}"
        "{% if row.badge %}[{{ row.badge }}]{% endif %}"
        "{% if not row.badge %}[-]{% endif %}"
        "{{ row.t }};"
        "{% endfor %}"
    )
    context = {"rows": [{"t": "a", "badge": "📈"}, {"t": "b", "badge": ""}]}
    assert minitpl.render(tpl, context) == "[📈]a;[-]b;"


def test_deeply_nested_if_blocks():
    tpl = "{% if a %}A{% if b %}B{% if c %}C{% endif %}{% endif %}{% endif %}"
    assert minitpl.render(tpl, {"a": 1, "b": 1, "c": 1}) == "ABC"
    assert minitpl.render(tpl, {"a": 1, "b": 1, "c": 0}) == "AB"
    assert minitpl.render(tpl, {"a": 0, "b": 1, "c": 1}) == ""


# =========================================================================
# 语法错误
# =========================================================================
@pytest.mark.parametrize(
    "bad",
    [
        "{% for row in rows %}x",             # for 未闭合
        "{% if a %}x",                        # if 未闭合
        "{% endfor %}",                       # 孤立 endfor
        "{% endif %}",                        # 孤立 endif
        "{% while a %}x{% endwhile %}",       # 未知标签
        "{% for row rows %}{% endfor %}",     # for 语法错
        "{% if a and b %}{% endif %}",        # 不支持表达式
        "{{ a['b'] }}",                       # 非法路径
    ],
)
def test_syntax_errors_raise_template_error(bad):
    with pytest.raises(minitpl.TemplateError):
        minitpl.render(bad, {})


# =========================================================================
# 安全：不得使用 eval/exec 执行模板内容
# =========================================================================
def test_implementation_uses_no_eval_or_exec():
    import inspect
    import re as _re

    source = inspect.getsource(minitpl)
    for forbidden in ("eval(", "exec(", "__import__", "globals(", "locals("):
        assert forbidden not in source, "minitpl 不允许出现 {0}".format(forbidden)
    # 内建 compile() 也禁；re.compile / compile_template 不在此列
    assert _re.search(r"(?<![.\w])compile\(", source) is None


def test_template_text_is_never_executed():
    # 模板里写 Python 也只当字面量处理
    tpl = "{{ payload }}"
    out = minitpl.render(tpl, {"payload": "__import__('os').system('echo pwned')"})
    assert "__import__" in out  # 原样出现（转义后），未被执行


def test_compile_template_is_reusable():
    compiled = minitpl.compile_template("{% for x in xs %}{{ x }}{% endfor %}")
    assert compiled.render({"xs": [1, 2]}) == "12"
    assert compiled.render({"xs": ["a"]}) == "a"


def test_template_without_tags_is_passthrough():
    text = "纯文本 <div>没有任何标记</div>"
    assert minitpl.render(text, {"x": 1}) == text
