import asyncio
from types import SimpleNamespace

from app.bot.utils.policy_runtime import _with_images

URL = "data:image/jpeg;base64,AAAA"


def test_caption_turn_is_upgraded_in_place():
    """The caption is already in the transcript; it must not be repeated."""
    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "Что на картинке"},
    ]

    out = _with_images(messages, "Что на картинке", [URL])

    assert len(out) == 2
    assert out[-1] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "Что на картинке"},
            {"type": "image_url", "image_url": {"url": URL}},
        ],
    }


def test_caption_less_photo_appends_an_image_only_turn():
    """A bare screenshot stores nothing, so the image needs its own turn."""
    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "earlier question"},
    ]

    out = _with_images(messages, "", [URL])

    assert len(out) == 3
    assert out[1] == {"role": "user", "content": "earlier question"}
    assert out[-1] == {
        "role": "user",
        "content": [{"type": "image_url", "image_url": {"url": URL}}],
    }


def test_image_is_not_grafted_onto_an_assistant_turn():
    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "assistant", "content": "previous reply"},
    ]

    out = _with_images(messages, "look", [URL])

    assert out[1]["role"] == "assistant"
    assert out[-1]["role"] == "user"
    assert len(out) == 3


def test_every_image_of_an_album_is_attached():
    urls = [f"data:image/jpeg;base64,{i}" for i in range(3)]
    messages = [{"role": "system", "content": "prompt"}, {"role": "user", "content": "cap"}]

    parts = _with_images(messages, "cap", urls)[-1]["content"]

    assert [p["type"] for p in parts] == ["text", "image_url", "image_url", "image_url"]


class _Bot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class _Storage:
    def __init__(self) -> None:
        self.draft = None

    async def get_conversation(self, user_id, limit):
        return [{"role": "user", "content": "where is my payout?"}]

    async def set_ai_draft(self, user_id, text):
        self.draft = text


def _completion(content: str) -> dict:
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": content},
        }],
    }


def _rate_limited_provider(wait_ms: int, timeout: int):
    """A real provider whose transport answers 429 once, then 200."""
    import httpx2
    from openai import AsyncOpenAI

    from app.bot.llm.openai_compatible import OpenAICompatibleProvider

    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx2.Response(
                429,
                headers={"retry-after-ms": str(wait_ms)},
                json={"error": {"message": "rate limited", "code": 429}},
            )
        return httpx2.Response(200, json=_completion("Payouts go out on Fridays."))

    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider._client = AsyncOpenAI(
        base_url="https://llm.test/v1",
        api_key="sk-test",
        timeout=timeout,
        max_retries=2,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
    )
    provider._model = "test-model"
    provider._max_tokens = 256
    provider._reasoning_effort = ""
    return provider, calls


def test_draft_survives_a_rate_limit_retry():
    """A 429 whose Retry-After outlasts AI_TIMEOUT_S must not kill the draft."""
    from app.bot.utils.policy_runtime import run_ai_draft
    from app.bot.utils.redis.models import UserData
    from app.config import AIConfig

    ai = AIConfig(
        PROVIDER="openai_compatible",
        BASE_URL="https://llm.test/v1",
        API_KEY="sk-test",
        MODEL="test-model",
        SYSTEM_PROMPT_PATH="",
        TIMEOUT_S=1,
        VISION=False,
    )
    config = SimpleNamespace(ai=ai, bot=SimpleNamespace(GROUP_ID=-100))
    bot = _Bot()
    message = SimpleNamespace(bot=bot, text="where is my payout?", caption=None)
    storage = _Storage()
    user = UserData(
        message_thread_id=7, message_silent_id=None, message_silent_mode=False,
        id=42, full_name="User", username="-", language_code="en",
    )
    # The client waits 1.5 s before retrying, longer than one request may take.
    provider, calls = _rate_limited_provider(wait_ms=1500, timeout=1)

    asyncio.run(run_ai_draft(provider, config, message, storage, user, 12))

    assert len(calls) == 2
    assert storage.draft == "Payouts go out on Fridays."
    assert len(bot.sent) == 1
    assert bot.sent[0]["message_thread_id"] == 7
    assert "Payouts go out on Fridays." in bot.sent[0]["text"]


def test_total_timeout_covers_every_retry():
    from app.config import AIConfig

    def cfg(**kwargs):
        return AIConfig(
            PROVIDER="none", BASE_URL="", API_KEY="", MODEL="", SYSTEM_PROMPT_PATH="",
            TIMEOUT_S=8, **kwargs,
        )

    # 3 attempts of 8 s plus 2 waits of up to the honoured Retry-After.
    assert cfg().total_timeout_s == 8 * 3 + AIConfig.RETRY_AFTER_CAP_S * 2
    assert cfg(MAX_RETRIES=0).total_timeout_s == 8
    assert cfg(TOTAL_TIMEOUT_S=30).total_timeout_s == 30
