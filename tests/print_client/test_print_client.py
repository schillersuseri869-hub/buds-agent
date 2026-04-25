import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "print_client"))
import print_client as pc


def make_minimal_pdf() -> bytes:
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=164, height=113)  # 58×40mm at 72 DPI
    page.insert_text((10, 30), "Test label")
    buf = doc.write()
    doc.close()
    return bytes(buf)


def test_render_pdf_to_image_returns_correct_width():
    from PIL import Image
    pdf_bytes = make_minimal_pdf()
    img = pc.render_pdf_to_image(pdf_bytes, target_width_mm=58.0, dpi=203)
    assert isinstance(img, Image.Image)
    expected_width = int(58.0 * 203 / 25.4)  # 464
    assert abs(img.width - expected_width) <= 2  # allow 2px rounding


def test_render_pdf_to_image_is_monochrome():
    pdf_bytes = make_minimal_pdf()
    img = pc.render_pdf_to_image(pdf_bytes)
    assert img.mode == "1"


def test_acquire_lock_succeeds_first_time():
    lock_path = os.path.join(tempfile.gettempdir(), "buds_test_lock.lock")
    if os.path.exists(lock_path):
        os.remove(lock_path)
    try:
        assert pc.acquire_lock(lock_path) is True
    finally:
        pc.release_lock(lock_path)


def test_acquire_lock_fails_when_self_already_holds():
    lock_path = os.path.join(tempfile.gettempdir(), "buds_test_lock2.lock")
    if os.path.exists(lock_path):
        os.remove(lock_path)
    try:
        assert pc.acquire_lock(lock_path) is True
        # Write own PID → simulates same process trying again
        assert pc.acquire_lock(lock_path) is False
    finally:
        pc.release_lock(lock_path)


def test_release_lock_removes_file():
    lock_path = os.path.join(tempfile.gettempdir(), "buds_test_lock3.lock")
    pc.acquire_lock(lock_path)
    pc.release_lock(lock_path)
    assert not os.path.exists(lock_path)
