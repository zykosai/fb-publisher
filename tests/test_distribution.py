import hashlib
from pathlib import Path
import subprocess
import zipfile

import pytest

from bike_publisher.distribution import allowed_path, build_archive, check_sources, public_files


@pytest.mark.parametrize('name', [
    'data/app.db', 'data/accounts/slot/browser_profile/Cookies', 'data/photos/item/image.jpg',
    '.env', '.env.production', '.venv/Lib/site-packages/package.py', 'test-tmp/draft.html',
    'src/__pycache__/module.pyc', 'web/account.key', 'scripts/facebook_diagnostic.py',
    'deploy/bike-publisher-worker.service', '../private.py', 'dist/release.zip',
])
def test_private_and_generated_paths_cannot_enter_release(name):
    assert not allowed_path(Path(name))


def source_tree(tmp_path):
    for name in ('README.md','LICENSE','.gitignore','PC START.cmd','setup.ps1','requirements.txt'):
        (tmp_path/name).write_text('Public sample source\n',encoding='utf-8')
    (tmp_path/'src').mkdir()
    (tmp_path/'src'/'example.py').write_text('VALUE = 1\n',encoding='utf-8')
    return tmp_path


def test_archive_excludes_runtime_and_verifies_integrity(tmp_path):
    (tmp_path/'project').mkdir()
    root=source_tree(tmp_path/'project')
    (root/'data').mkdir()
    (root/'data'/'app.db').write_bytes(b'PRIVATE DATABASE')
    (root/'.env').write_text('PRIVATE CONFIG',encoding='utf-8')
    target=tmp_path/'release.zip'
    count,digest=build_archive(root,target)
    assert digest==hashlib.sha256(target.read_bytes()).hexdigest()
    with zipfile.ZipFile(target) as archive:
        names=archive.namelist()
        assert not any('/data/' in name or name.endswith('/.env') for name in names)
        manifest=archive.read('FB-Publisher/MANIFEST.sha256').decode().splitlines()
        assert len(manifest)==count
        for line in manifest:
            expected,name=line.split('  ',1)
            assert hashlib.sha256(archive.read('FB-Publisher/'+name)).hexdigest()==expected
    with pytest.raises(FileExistsError): build_archive(root,target)


def test_distribution_secret_check_redacts_value(tmp_path):
    root=source_tree(tmp_path)
    secret='ghp_'+'a'*36
    (root/'src'/'example.py').write_text(f'TOKEN = "{secret}"',encoding='utf-8')
    with pytest.raises(ValueError) as error: check_sources(root)
    assert 'GitHub token' in str(error.value)
    assert secret not in str(error.value)


def test_git_check_rejects_even_force_added_private_files(tmp_path):
    root=source_tree(tmp_path)
    subprocess.run(['git','init','-q'],cwd=root,check=True)
    (root/'data').mkdir()
    (root/'data'/'app.db').write_bytes(b'private')
    subprocess.run(['git','add','data/app.db'],cwd=root,check=True)
    with pytest.raises(ValueError,match='not in the public source allowlist'):
        check_sources(root,check_git=True)


def test_real_source_manifest_has_no_runtime_data():
    root=Path(__file__).resolve().parents[1]
    files=check_sources(root)
    assert root/'web'/'locales.js' in files
    assert root/'scripts'/'worker.py' in files
    assert all(allowed_path(path.relative_to(root)) for path in files)
    assert not any('data' in path.relative_to(root).parts for path in files)
