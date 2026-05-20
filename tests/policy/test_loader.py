import pytest
from pydantic import ValidationError

from app.bot.policy import PolicyEngine, load_policy, load_policy_from_dict
from app.config import PolicyConfig


def test_load_from_dict_valid():
    engine = load_policy_from_dict({
        "version": 1,
        "templates": {"hi": {"en": "hello"}},
        "rules": [
            {"id": "r1", "when": {"event_type": "user_message"},
             "actions": [{"type": "auto_reply", "template_key": "hi"}]},
        ],
    })
    assert isinstance(engine, PolicyEngine)
    assert engine.document.rules[0].id == "r1"


def test_load_from_empty_dict():
    engine = load_policy_from_dict({})
    assert engine.document.rules == []
    assert engine.document.defaults.language_fallback == "en"


def test_missing_file_raises():
    cfg = PolicyConfig(ENABLED=True, PATH="/nonexistent/policy.yaml")
    with pytest.raises(FileNotFoundError):
        load_policy(cfg)


def test_unknown_action_type_rejected():
    with pytest.raises(ValidationError):
        load_policy_from_dict({
            "rules": [{"id": "bad", "when": {}, "actions": [{"type": "nope"}]}],
        })


def test_extra_top_level_key_rejected():
    with pytest.raises(ValidationError):
        load_policy_from_dict({"unexpected": True})


def test_loads_real_example(tmp_path):
    import shutil
    from pathlib import Path

    src = Path("config/policy.example.yaml")
    dst = tmp_path / "policy.yaml"
    shutil.copy(src, dst)
    engine = load_policy(PolicyConfig(ENABLED=True, PATH=str(dst)))
    assert any(r.id == "skip_lifecycle" for r in engine.document.rules)
