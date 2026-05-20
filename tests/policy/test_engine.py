from app.bot.policy import EvalContext, load_policy_from_dict
from app.bot.policy.context import (
    EVENT_TOPIC_CREATED,
    EVENT_USER_MESSAGE,
    EVENT_USER_STARTED,
)


def make_engine():
    return load_policy_from_dict({
        "defaults": {"language_fallback": "en"},
        "variables": {"url": "https://x"},
        "templates": {
            "clarify": {"en": "elaborate", "ru": "уточните"},
            "rules": {"en": "rules at {url}"},
        },
        "rules": [
            {"id": "lifecycle", "when": {"event_type": ["user_started", "user_stopped"]},
             "actions": [{"type": "suppress_group_notify"}]},
            {"id": "short", "when": {"all": [
                {"event_type": "user_message"},
                {"message_length": {"max": 5}},
            ]},
             "actions": [
                 {"type": "auto_reply", "template_key": "clarify"},
                 {"type": "set_tag", "name": "short"},
             ]},
            {"id": "always_tag", "when": {"event_type": "user_message"},
             "actions": [{"type": "set_tag", "name": "seen"}]},
        ],
    })


def test_lifecycle_suppresses_notify():
    engine = make_engine()
    decision = engine.evaluate(EvalContext(EVENT_USER_STARTED, "", "en"))
    assert decision.suppress_group_notify is True
    assert decision.auto_replies == []


def test_multiple_rules_aggregate():
    engine = make_engine()
    decision = engine.evaluate(EvalContext(EVENT_USER_MESSAGE, "hi", "en"))
    # both "short" and "always_tag" match
    assert decision.auto_replies == ["elaborate"]
    assert decision.tags == ["short", "seen"]


def test_long_message_only_tag():
    engine = make_engine()
    decision = engine.evaluate(EvalContext(EVENT_USER_MESSAGE, "x" * 50, "en"))
    assert decision.auto_replies == []
    assert decision.tags == ["seen"]


def test_language_fallback_in_template():
    engine = make_engine()
    # 'fr' has no template; falls back to 'en'
    decision = engine.evaluate(EvalContext(EVENT_USER_MESSAGE, "hi", "fr"))
    assert decision.auto_replies == ["elaborate"]


def test_russian_template_used():
    engine = make_engine()
    decision = engine.evaluate(EvalContext(EVENT_USER_MESSAGE, "hi", "ru"))
    assert decision.auto_replies == ["уточните"]


def test_noop_for_unmatched_event():
    engine = make_engine()
    decision = engine.evaluate(EvalContext(EVENT_TOPIC_CREATED, "", "en"))
    assert decision.is_noop is True
