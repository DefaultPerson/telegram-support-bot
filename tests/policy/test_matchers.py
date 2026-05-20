import pytest

from app.bot.policy.context import EVENT_USER_MESSAGE, EVENT_USER_STARTED, EvalContext
from app.bot.policy.matchers import matches


def ctx(text="", event_type=EVENT_USER_MESSAGE):
    return EvalContext(event_type=event_type, text=text, language="en")


def test_event_type_string_and_list():
    assert matches({"event_type": "user_message"}, ctx())
    assert matches({"event_type": ["user_started", "user_message"]}, ctx())
    assert not matches({"event_type": "user_started"}, ctx())


def test_keywords_any_list_default_one_match():
    clause = {"keywords_any": ["mediahold", "tier1"]}
    assert matches(clause, ctx("We are MediaHold agency"))
    assert not matches(clause, ctx("just a channel"))


def test_keywords_any_min_matches():
    clause = {"keywords_any": {"min_matches": 2, "list": ["a", "b", "c"]}}
    assert matches(clause, ctx("a and b"))
    assert not matches(clause, ctx("only a"))


def test_regex_case_insensitive():
    clause = {"regex": r"(?i)\b(price|цена)\b"}
    assert matches(clause, ctx("What is the PRICE?"))
    assert matches(clause, ctx("какая цена"))
    assert not matches(clause, ctx("hello there"))


def test_message_length_bounds():
    assert matches({"message_length": {"max": 10}}, ctx("short"))
    assert not matches({"message_length": {"max": 10}}, ctx("x" * 20))
    assert matches({"message_length": {"min": 3}}, ctx("abcd"))
    assert not matches({"message_length": {"min": 3}}, ctx("ab"))


def test_has_link():
    assert matches({"has_link": True}, ctx("see https://t.me/foo"))
    assert matches({"has_link": True}, ctx("@my_channel join"))
    assert matches({"has_link": False}, ctx("no links here"))
    assert not matches({"has_link": True}, ctx("no links here"))


def test_all_combinator():
    clause = {"all": [
        {"event_type": "user_message"},
        {"message_length": {"max": 10}},
        {"has_link": False},
    ]}
    assert matches(clause, ctx("hi"))
    assert not matches(clause, ctx("hi", event_type=EVENT_USER_STARTED))


def test_any_combinator():
    clause = {"any": [
        {"regex": r"price"},
        {"keywords_any": ["cost"]},
    ]}
    assert matches(clause, ctx("the cost is high"))
    assert not matches(clause, ctx("unrelated text"))


def test_implicit_and_multiple_leaves():
    clause = {"event_type": "user_message", "has_link": True}
    assert matches(clause, ctx("https://t.me/x"))
    assert not matches(clause, ctx("no link"))


def test_unknown_matcher_raises():
    with pytest.raises(ValueError):
        matches({"bogus": 1}, ctx("text"))
