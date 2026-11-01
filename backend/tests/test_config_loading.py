"""Configuration regression checks using only isolated fake credentials."""
import importlib.util
from pathlib import Path

import pytest

from app import config


def isolated_settings(tmp_path):
    backend = tmp_path / 'project' / 'backend'
    module_file = backend / 'app' / 'config.py'
    module_file.parent.mkdir(parents=True)
    module_file.write_text(Path(config.__file__).read_text())
    (backend / '.env').write_text('OPENAI_API_KEY=fixture-backend-key\n')
    spec = importlib.util.spec_from_file_location('app.isolated_config', module_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Settings, backend


@pytest.mark.parametrize('directory', ['backend', 'repo', 'unrelated'])
def test_default_dotenv_follows_backend_not_working_directory(tmp_path, monkeypatch, directory):
    Settings, backend = isolated_settings(tmp_path)
    cwd = backend if directory == 'backend' else backend.parent if directory == 'repo' else tmp_path / 'unrelated'
    cwd.mkdir(exist_ok=True)
    if cwd != backend:
        (cwd / '.env').write_text('OPENAI_API_KEY=wrong-project-fixture\n')
    monkeypatch.chdir(cwd)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    assert Settings().openai_api_key == 'fixture-backend-key'


def test_explicit_environment_and_dotenv_override_remain_authoritative(tmp_path, monkeypatch):
    Settings, _ = isolated_settings(tmp_path)
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-container-key')
    assert Settings().openai_api_key == 'fixture-container-key'
    monkeypatch.setenv('OPENAI_API_KEY', '')
    assert Settings().openai_api_key == ''  # Offline tests deliberately disable providers.
    monkeypatch.delenv('OPENAI_API_KEY')
    override = tmp_path / 'override.env'
    override.write_text('OPENAI_API_KEY=fixture-override-key\n')
    assert Settings(_env_file=override).openai_api_key == 'fixture-override-key'
    assert Settings(_env_file=None).openai_api_key == ''
