"""Tests for gateway.file_gateway — local file streaming."""

import pytest
from pathlib import Path

from gateway.file_gateway import FileGateway


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "a.txt").write_text("Hello World", encoding="utf-8")
    (d / "b.txt").write_text("Second file", encoding="utf-8")
    (d / "sub").mkdir()
    (d / "sub" / "c.txt").write_text("Nested", encoding="utf-8")
    return d


class TestFileGateway:
    def test_stream_files_basic(self, data_dir):
        gw = FileGateway(str(data_dir))
        files = list(gw.stream_files("*.txt"))
        assert len(files) == 2
        names = {f["file_name"] for f in files}
        assert "a.txt" in names
        assert "b.txt" in names

    def test_stream_files_recursive(self, data_dir):
        gw = FileGateway(str(data_dir))
        files = list(gw.stream_files("**/*.txt"))
        assert len(files) == 3

    def test_stream_files_content(self, data_dir):
        gw = FileGateway(str(data_dir))
        files = list(gw.stream_files("a.txt"))
        assert files[0]["content"] == "Hello World"

    def test_stream_files_metadata(self, data_dir):
        gw = FileGateway(str(data_dir))
        files = list(gw.stream_files("a.txt"))
        f = files[0]
        assert f["file_name"] == "a.txt"
        assert "relative_path" in f
        assert f["size"] > 0

    def test_stream_files_empty_dir(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        gw = FileGateway(str(empty))
        files = list(gw.stream_files())
        assert files == []

    def test_stream_files_no_match(self, data_dir):
        gw = FileGateway(str(data_dir))
        files = list(gw.stream_files("*.xyz"))
        assert files == []

    def test_save_result(self, data_dir):
        gw = FileGateway(str(data_dir))
        out = gw.save_result("a.txt", "Refined content", suffix=".out")
        assert out.exists()
        assert out.read_text(encoding="utf-8") == "Refined content"

    def test_save_result_custom_dir(self, data_dir, tmp_path):
        gw = FileGateway(str(data_dir))
        out_dir = tmp_path / "output"
        out = gw.save_result("a.txt", "Content", output_dir=str(out_dir))
        assert out_dir.exists()
        assert out.read_text(encoding="utf-8") == "Content"

    def test_init_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            FileGateway(str(tmp_path / "nonexistent"))
