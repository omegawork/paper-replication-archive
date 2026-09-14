#!/usr/bin/env python3
"""Build an allowlisted release ZIP and checksum using only the standard library."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ('README.md', 'README.zh-CN.md', 'LICENSE', 'CHANGELOG.md', 'CONTRIBUTING.md', 'VERSION')
DIRECTORIES = ('skills', 'scripts', 'baselines', 'examples', 'docs')


def build(output: Path) -> tuple[Path, Path]:
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('VERSION must contain a release version')
    paths = [ROOT / name for name in FILES]
    for directory in DIRECTORIES:
        paths.extend(path for path in (ROOT / directory).rglob('*') if path.is_file()
                     and '__pycache__' not in path.parts and path.suffix != '.pyc')
    output.mkdir(parents=True, exist_ok=True)
    prefix = f'paper-replication-archive-{version}'
    archive = output / f'{prefix}.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
        for path in sorted(paths, key=lambda p: p.relative_to(ROOT).as_posix().encode('utf-8')):
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
                raise ValueError(f'release input is not a regular project file: {path}')
            info = zipfile.ZipInfo(prefix + '/' + path.relative_to(ROOT).as_posix(), (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            handle.writestr(info, path.read_bytes(), compresslevel=9)
    checksum = output / 'SHA256SUMS.txt'
    checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n',
                        encoding='utf-8', newline='\n')
    return archive, checksum


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    for item in build(args.output.resolve()):
        print(item)
