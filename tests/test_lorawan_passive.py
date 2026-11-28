from src.decode.lorawan import decode_header


def test_join_request():
    raw = bytes.fromhex("00 0807060504030201 1817161514131211 3412 00000000")
    h = decode_header(raw)
    assert h["frame_type"] == "join_request"
    assert h["join_eui"] == "0102030405060708"
    assert h["dev_eui"] == "1112131415161718"
    assert h["dev_nonce"] == 0x1234
    assert h["mic_verified"] is False


def test_data_frame():
    h = decode_header(bytes.fromhex("40 78563412 00 3412 01 aabb 00000000"))
    assert h["frame_type"] == "unconfirmed_up"
    assert h["dev_addr"] == "12345678"
    assert h["fcnt16"] == 0x1234
    assert h["fport"] == 1
    assert h["encrypted_payload_size"] == 2


def test_bad_header():
    for raw in (b"", b"\x00", b"\x41" + bytes(20), b"\x40" + bytes(4)):
        assert decode_header(raw)["frame_type"] == "unknown"


def test_invalid_fopts_length():
    assert decode_header(bytes.fromhex("40 78563412 0f 3412 00000000"))["frame_type"] == "unknown"


def test_join_accept_is_opaque():
    h = decode_header(b"\x20" + bytes(16))
    assert h == {"frame_type": "join_accept", "mic_verified": False}


def test_rejoin_request():
    h = decode_header(bytes.fromhex("c0 00 010203 0807060504030201 0100 00000000"))
    assert h["dev_eui"] == "0102030405060708"
    assert h["rejoin_type"] == 0
