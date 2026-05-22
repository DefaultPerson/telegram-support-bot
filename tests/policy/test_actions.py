import pytest

from app.bot.policy.actions import apply_action, render_template
from app.bot.policy.context import EVENT_USER_MESSAGE, EvalContext
from app.bot.policy.decision import Decision
from app.bot.policy.schema import Action, PolicyDocument


def make_doc(**kwargs):
    base = {
        "variables": {"url": "https://x"},
        "templates": {"rules": {"en": "see {url}", "ru": "смотри {url}"}},
    }
    base.update(kwargs)
    return PolicyDocument.model_validate(base)


def ctx(language="en"):
    return EvalContext(EVENT_USER_MESSAGE, "text", language)


def test_render_template_substitutes_variables():
    doc = make_doc()
    assert render_template(doc, "rules", "en") == "see https://x"
    assert render_template(doc, "rules", "ru") == "смотри https://x"


def test_render_missing_template_key_raises():
    doc = make_doc()
    with pytest.raises(KeyError):
        render_template(doc, "absent", "en")


def test_render_missing_variable_raises():
    doc = PolicyDocument.model_validate({
        "templates": {"t": {"en": "needs {missing}"}},
    })
    with pytest.raises(KeyError):
        render_template(doc, "t", "en")


def test_auto_reply_action_appends_rendered_text():
    doc = make_doc()
    decision = Decision()
    apply_action(Action(type="auto_reply", template_key="rules"), ctx(), doc, decision)
    assert decision.auto_replies == ["see https://x"]


def test_boolean_actions_set_flags():
    doc = make_doc()
    decision = Decision()
    for action_type, attr in [
        ("suppress_topic_creation", "suppress_topic_creation"),
        ("suppress_group_notify", "suppress_group_notify"),
        ("close_topic", "close_topic"),
        ("escalate", "escalate"),
    ]:
        apply_action(Action(type=action_type), ctx(), doc, decision)
        assert getattr(decision, attr) is True


def test_auto_reply_without_template_key_raises():
    doc = make_doc()
    with pytest.raises(ValueError):
        apply_action(Action(type="auto_reply"), ctx(), doc, Decision())
