"""Tests for timestamped output directories."""

import re
from pathlib import Path

from schnabel.config import make_output_dir


def test_make_output_dir_creates_directory(tmp_path):
    """make_output_dir creates the directory and returns the Path."""
    result = make_output_dir("export", base_dir=tmp_path)
    assert result.exists()
    assert result.is_dir()


def test_make_output_dir_format(tmp_path):
    """Output directory follows YYYY-MM-DD_HHMM_command format."""
    result = make_output_dir("import", base_dir=tmp_path)
    name = result.name
    assert re.match(r"\d{4}-\d{2}-\d{2}_\d{4}_import$", name)


def test_make_output_dir_different_commands(tmp_path):
    """Different command names produce different directories."""
    dir1 = make_output_dir("import", base_dir=tmp_path)
    dir2 = make_output_dir("sanitize", base_dir=tmp_path)
    assert dir1 != dir2
    assert "import" in dir1.name
    assert "sanitize" in dir2.name


def test_make_output_dir_default_base(tmp_path, monkeypatch):
    """When no base_dir given, uses DEFAULT_OUTPUT_DIR."""
    import schnabel.config
    monkeypatch.setattr(schnabel.config, "DEFAULT_OUTPUT_DIR", tmp_path / "output")
    result = make_output_dir("test")
    assert result.parent == tmp_path / "output"
    assert result.exists()
