"""Tests run without login unless a test switches it on (see test_step2.py)."""
import os
import tempfile

os.environ.setdefault("TUSCORE_AUTH", "off")
os.environ.setdefault("TUSCORE_DATA", tempfile.mkdtemp(prefix="tuscore-test-"))
