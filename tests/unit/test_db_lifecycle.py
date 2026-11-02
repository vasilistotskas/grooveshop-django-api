import pytest

from tests.db_lifecycle import _db_name_is_configured_default


@pytest.fixture
def project_dir(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    return tmp_path


def test_the_env_files_name_keeps_its_databases(project_dir):
    (project_dir / ".env").write_text("DB_NAME='postgres'\n")
    assert _db_name_is_configured_default("postgres")


def test_a_name_other_than_the_env_files_drops_them(project_dir):
    (project_dir / ".env").write_text("DB_NAME='postgres'\n")
    assert not _db_name_is_configured_default("side_run")


def test_without_env_file_an_environment_db_name_drops_them(
    project_dir, monkeypatch
):
    monkeypatch.setenv("DB_NAME", "side_run")
    assert not _db_name_is_configured_default("side_run")


def test_without_env_file_or_db_name_the_default_keeps_them(
    project_dir, monkeypatch
):
    monkeypatch.delenv("DB_NAME", raising=False)

    assert _db_name_is_configured_default("postgres")
