from app.bot.utils.redact import redact

OPENROUTER_402 = (
    "Error code: 402 - {'error': {'message': \"This request requires more credits. "
    "To increase, visit https://openrouter.ai/workspaces/default/keys/"
    "824979044481837b3ade2cec8502bfb640915bfa6a6bf1fec582e5d45030cdbb and adjust\"}}"
)


def test_openrouter_key_hash_is_removed():
    out = redact(OPENROUTER_402, limit=None)

    assert "824979044481837b" not in out
    assert "/keys/[redacted]" in out
    # The useful part of the message survives.
    assert "402" in out


def test_api_keys_and_bot_tokens_are_removed():
    out = redact("key sk-or-v1-abcdefgh12345678 token 8953444085:AAH" + "x" * 32, limit=None)

    assert "sk-or-v1" not in out
    assert "8953444085:" not in out
    assert out.count("[redacted]") == 2


def test_authorization_header_is_removed():
    assert "topsecretvalue" not in redact("Authorization: Bearer topsecretvalue123", limit=None)


def test_long_text_is_truncated():
    out = redact("x" * 5000)

    assert len(out) < 600
    assert out.endswith("(truncated)")


def test_plain_text_is_untouched():
    assert redact("AI draft failed: timeout after 8s", limit=None) == "AI draft failed: timeout after 8s"
