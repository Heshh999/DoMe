import pytest

from dome_protocol import ProtocolError, dumps_compact, loads_strict


def test_roundtrip_preserves_order():
    text = '{"b":1,"a":[1,2,{"z":"ü"}]}'
    assert dumps_compact(loads_strict(text)) == text


def test_rejects_duplicate_keys():
    with pytest.raises(ProtocolError) as ei:
        loads_strict('{"a":1,"a":2}')
    assert ei.value.code == "MALFORMED_MESSAGE"


def test_rejects_nested_duplicate_keys():
    with pytest.raises(ProtocolError):
        loads_strict('{"a":{"b":1,"b":2}}')


@pytest.mark.parametrize("text", ["{\"a\":NaN}", "{\"a\":Infinity}", "{\"a\":-Infinity}"])
def test_rejects_non_finite(text):
    with pytest.raises(ProtocolError):
        loads_strict(text)


def test_rejects_too_deep():
    text = '{"a":' * 9 + "1" + "}" * 9
    with pytest.raises(ProtocolError) as ei:
        loads_strict(text, max_depth=8)
    assert "deep" in ei.value.message


def test_accepts_at_depth_limit():
    text = '{"a":' * 7 + "1" + "}" * 7
    loads_strict(text, max_depth=8)


def test_rejects_extreme_depth_without_recursion_error():
    text = "[" * 100000 + "]" * 100000
    with pytest.raises(ProtocolError):
        loads_strict(text, max_bytes=10_000_000, require_object=False)


def test_rejects_oversize():
    with pytest.raises(ProtocolError) as ei:
        loads_strict('{"a":"' + "x" * 20000 + '"}')
    assert ei.value.code == "PAYLOAD_TOO_LARGE"


def test_rejects_non_object_top_level():
    with pytest.raises(ProtocolError):
        loads_strict("[1,2]")
    assert loads_strict("[1,2]", require_object=False) == [1, 2]


def test_rejects_lone_surrogate():
    with pytest.raises(ProtocolError):
        loads_strict('{"a":"\\ud800"}')


def test_rejects_invalid_utf8_bytes():
    with pytest.raises(ProtocolError):
        loads_strict(b'{"a":"\xff"}')


def test_rejects_garbage():
    with pytest.raises(ProtocolError):
        loads_strict("{not json}")


def test_dumps_compact_refuses_nan():
    with pytest.raises(ValueError):
        dumps_compact({"a": float("nan")})
