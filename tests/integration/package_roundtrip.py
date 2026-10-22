"""Construye un wheel e instala sus entry points en un venv temporal, sin red."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def checked(args, cwd, env):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f'{args[0]} terminó con {result.returncode}:\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}')
    return result.stdout


with tempfile.TemporaryDirectory(prefix='thinkisp-wheel-') as directory:
    work = Path(directory)
    source = work / 'source'
    source.mkdir()
    for name in ('main.py', 'pyproject.toml', 'README.md', 'LICENSE'):
        shutil.copy2(ROOT / name, source / name)
    shutil.copytree(ROOT / 'thinkisp', source / 'thinkisp', ignore=shutil.ignore_patterns('__pycache__'))
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env['PIP_NO_CACHE_DIR'] = '1'
    wheels = work / 'wheels'
    checked([sys.executable, '-m', 'pip', '--isolated', 'wheel', '--no-index', '--no-deps',
             '--no-build-isolation', '--wheel-dir', str(wheels), str(source)], cwd=work, env=env)
    wheel = next(wheels.glob('thinkisp-*.whl'))
    with zipfile.ZipFile(wheel) as archive:
        for name in ('main.py', 'thinkisp/core/profile.py', 'thinkisp/core/doctor.py', 'thinkisp/ui/runtime.py'):
            assert name in archive.namelist(), f'Falta {name} en el wheel'
    target = work / 'venv'
    venv.EnvBuilder(with_pip=True).create(target)
    python = target / 'bin/python'
    checked([str(python), '-m', 'pip', '--isolated', 'install', '--no-index', '--no-deps', str(wheel)], cwd=work, env=env)
    for command in ('thinkisp', 'isp'):
        help_text = checked([str(target / 'bin' / command), '--help'], cwd=work, env=env)
        assert 'doctor' in help_text and 'setup' in help_text
        assert '--json' in checked([str(target / 'bin' / command), 'doctor', '--help'], cwd=work, env=env)
    path = checked([str(python), '-I', '-c', 'import main; print(main.__file__)'], cwd=work, env=env).strip()
    assert str(target) in path, f'Entry point importado desde otra instalación: {path}'
    print(f'PASS: wheel, módulos y entry points thinkisp/isp instalados en venv con Python {sys.version.split()[0]}; sin red.')
