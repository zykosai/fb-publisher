"""Explicit source-only distribution. Never discover files by archiving the root."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess
import zipfile


ROOT_FILES = frozenset({
    '.gitignore', '.gitattributes', '.env.example', 'config.example.env', 'LICENSE',
    'README.md', 'README.ru.md', 'WEB-APP.md', 'CONTRIBUTING.md', 'SECURITY.md',
    'requirements.txt', 'requirements-dev.txt', 'pytest.ini', 'app.py',
    'PC START.cmd', 'TAILSCALE START.cmd', 'STOPBOT.cmd', 'SETUP.cmd',
    'setup.ps1', 'run-web.ps1', 'run.ps1', 'run_worker.ps1', 'stop-bot.ps1',
})
TREES = frozenset({'src', 'web', 'tests', 'docs', '.github'})
SCRIPTS = frozenset({'web.py', 'worker.py', 'facebook_login.py', 'facebook_login.ps1',
    'import_zip.py', 'prepare_ui_locales.py', 'build_release.py', 'check_distribution.py'})
DEPLOY = frozenset({'README.md', 'setup_ubuntu.sh', 'fb-publisher.service'})
EXCLUDED_PARTS = frozenset({'data', '.git', '.venv', 'venv', '__pycache__', '.pytest_cache', 'test-tmp', 'dist'})
EXCLUDED_SUFFIXES = {'.pyc', '.pyo', '.db', '.sqlite', '.sqlite3', '.zip', '.log', '.pem', '.key', '.pfx', '.p12'}
TEXT_SUFFIXES = {'.py', '.js', '.json', '.css', '.html', '.svg', '.md', '.txt', '.yml', '.yaml', '.ps1', '.cmd', '.sh', '.service', '.ini', '.webmanifest', '.env'}
SECRET_PATTERNS = {
    'GitHub token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'connection key': re.compile(r'\?key=[A-Za-z0-9_-]{20,}'),
    'AWS access key': re.compile(r'\bAKIA[A-Z0-9]{16}\b'),
    'personal Windows home': re.compile(r'[A-Za-z]:[\\/]Users[\\/](?!YOUR_|<|example(?:[\\/]|$))[^\\/\s"\']+[\\/]'),
}


def allowed_path(relative: Path) -> bool:
    if relative.is_absolute() or any(part in EXCLUDED_PARTS or part == '..' for part in relative.parts):
        return False
    if relative.name.startswith('.env') and relative.as_posix() != '.env.example':
        return False
    if relative.suffix.lower() in EXCLUDED_SUFFIXES or relative.name.endswith(('.db-wal', '.db-shm')):
        return False
    if len(relative.parts) == 1:
        return relative.name in ROOT_FILES
    if relative.parts[0] in TREES:
        return relative.suffix.lower() in TEXT_SUFFIXES or relative.name in {'.gitignore', '.gitattributes'}
    return (len(relative.parts) == 2 and
        ((relative.parts[0] == 'scripts' and relative.name in SCRIPTS) or
         (relative.parts[0] == 'deploy' and relative.name in DEPLOY)))


def public_files(root: Path) -> list[Path]:
    root = root.resolve()
    required = {'README.md', 'LICENSE', '.gitignore', 'PC START.cmd', 'setup.ps1', 'requirements.txt'}
    if any(not (root / name).is_file() for name in required):
        raise ValueError('Not a complete FB Publisher source directory')
    candidates = [root / name for name in ROOT_FILES if (root / name).exists()]
    for folder in (*TREES, 'scripts', 'deploy'):
        if (root / folder).exists():
            candidates.extend((root / folder).rglob('*'))
    result = []
    for path in candidates:
        relative = path.relative_to(root)
        if not allowed_path(relative):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f'Symlink/outside source root: {relative.as_posix()}')
        if path.is_file():
            result.append(path)
    return sorted(set(result), key=lambda path: path.relative_to(root).as_posix())


def check_sources(root: Path, *, check_git: bool = False) -> list[Path]:
    root = root.resolve()
    files = public_files(root)
    failures = []
    for path in files:
        text = path.read_text(encoding='utf-8-sig')
        for reason, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                # Never include the matched credential in error output.
                failures.append(f'{path.relative_to(root).as_posix()}: possible {reason}')
    if check_git:
        tracked = subprocess.run(['git', 'ls-files', '-z'], cwd=root, check=True, capture_output=True).stdout
        for name in tracked.decode('utf-8').split('\0'):
            if name and not allowed_path(Path(name)):
                failures.append(f'{name}: not in the public source allowlist')
    if failures:
        raise ValueError('Distribution check failed:\n' + '\n'.join(failures))
    return files


def build_archive(root: Path, output: Path) -> tuple[int, str]:
    root, output = root.resolve(), output.resolve()
    files = check_sources(root)
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite existing archive: {output.name}')
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            manifest.append(f'{hashlib.sha256(data).hexdigest()}  {relative}')
            info = zipfile.ZipInfo('FB-Publisher/' + relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100755 if path.suffix == '.sh' else 0o100644) << 16
            archive.writestr(info, data)
        archive.writestr('FB-Publisher/MANIFEST.sha256', '\n'.join(manifest) + '\n')
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + '.sha256').write_text(f'{digest}  {output.name}\n', encoding='ascii')
    return len(files), digest
