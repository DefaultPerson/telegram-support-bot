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
