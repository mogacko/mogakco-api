from app.images.service import is_image

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "


def test_accepts_real_image_signatures():
    assert is_image(JPEG)
    assert is_image(PNG)
    assert is_image(WEBP)


def test_rejects_disguised_and_broken_files():
    # .jpg로 이름만 바꾼 실행 파일과 HTML
    assert not is_image(b"MZ\x90\x00\x03\x00\x00\x00")
    assert not is_image(b"<!DOCTYPE html><html>")
    assert not is_image(b"")
    assert not is_image(b"\xff\xd8")
    # RIFF 컨테이너지만 WEBP가 아닌 것 (WAV)
    assert not is_image(b"RIFF\x24\x00\x00\x00WAVEfmt ")
