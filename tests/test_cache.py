from pydantic import BaseModel

from app.services.cache import ExactMatchCache


class FakeResult(BaseModel):
    text: str


def make_result(text: str) -> FakeResult:
    return FakeResult(text=text)


def test_same_request_produces_same_key():
    key_a = ExactMatchCache.make_key("anthropic", "m", "prompt", "transcripción")
    key_b = ExactMatchCache.make_key("anthropic", "m", "prompt", "transcripción")

    assert key_a == key_b


def test_any_difference_produces_different_key():
    base = ExactMatchCache.make_key("anthropic", "m", "prompt", "transcripción")

    assert base != ExactMatchCache.make_key("openai", "m", "prompt", "transcripción")
    assert base != ExactMatchCache.make_key(
        "anthropic", "m2", "prompt", "transcripción"
    )
    assert base != ExactMatchCache.make_key("anthropic", "m", "otro", "transcripción")
    assert base != ExactMatchCache.make_key(
        "anthropic", "m", "prompt", "transcripción "
    )


def test_get_returns_none_when_missing():
    cache = ExactMatchCache()

    assert cache.get("no-existe") is None


def test_set_then_get_returns_result():
    cache = ExactMatchCache()
    cache.set("clave", make_result("estimación"))

    assert cache.get("clave").text == "estimación"


def test_evicts_least_recently_used_entry_when_full():
    cache = ExactMatchCache(max_entries=2)
    cache.set("a", make_result("A"))
    cache.set("b", make_result("B"))
    cache.get("a")
    cache.set("c", make_result("C"))

    assert cache.get("b") is None
    assert cache.get("a").text == "A"
    assert cache.get("c").text == "C"
    assert len(cache) == 2
