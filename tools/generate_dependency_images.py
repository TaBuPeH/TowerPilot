"""Regenerate dependency_images.json from hash-verified portable input artifacts.

Run: python tools/generate_dependency_images.py
Downloads go into a TemporaryDirectory, never the checkout. Wheels are inspected
without importing or executing them. The pinned tpk_ar sdist is inspected too;
an sdist image requires a reviewed built wheel before it can be allowed.
"""
import argparse
import hashlib
import json
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


def generate(pins_path: Path, artifact_dir: Path) -> dict[str, object]:
    """Inspect every pinned input, refusing bytes that disagree with its pin."""
    pins_bytes = pins_path.read_bytes()
    pins = json.loads(pins_bytes)
    images = []
    inspected = []
    for row in pins['packages']:
        package, version = row['requirement'].split('==')
        artifact, = row['artifacts']
        filename = artifact['filename']
        path = artifact_dir / filename
        if not path.exists():
            urllib.request.urlretrieve(artifact['url'], path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != artifact['sha256']:
            raise ValueError(f'Pinned artifact hash mismatch: {filename}')
        count = 0
        if filename.endswith('.whl'):
            with zipfile.ZipFile(path) as wheel:
                for name in sorted(wheel.namelist()):
                    if PurePosixPath(name).suffix.lower() != '.png':
                        continue
                    parts = name.split('/')
                    # Wheel .data/{purelib,platlib} files relocate into site-packages.
                    if parts[0].endswith('.data'):
                        if len(parts) < 3 or parts[1] not in {'purelib', 'platlib'}:
                            raise ValueError(f'Image outside wheel site-packages: {filename}')
                        relative = '/'.join(parts[2:])
                    else:
                        relative = name
                    images.append({
                        'path': 'runtime/Lib/site-packages/' + relative,
                        'sha256': hashlib.sha256(wheel.read(name)).hexdigest(),
                        'package': package, 'version': version,
                        'wheel': filename, 'wheel_sha256': digest, 'wheel_path': name,
                    })
                    count += 1
        else:
            with tarfile.open(path) as source:
                if any(PurePosixPath(member.name).suffix.lower() == '.png'
                       for member in source.getmembers()):
                    raise ValueError(f'Source image needs a reviewed built wheel: {filename}')
        inspected.append({'requirement': row['requirement'], 'filename': filename,
                          'sha256': digest, 'png_count': count})
        print(f'{filename}: verified, {count} PNGs')
    return {
        'schema_version': 1,
        'generation': {
            'command': 'python tools/generate_dependency_images.py',
            'method': 'Download every pinned artifact into a temporary directory; verify '
                      'SHA-256 before inspecting wheel members and the source-build exception. '
                      'Image hashes cover the exact uncompressed installed bytes; no images '
                      'are stored in the repository.',
            'pins_sha256': hashlib.sha256(pins_bytes).hexdigest(),
        },
        'inspected_artifacts': inspected,
        'images': sorted(images, key=lambda image: image['path']),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    directory = Path(__file__).resolve().parent
    parser.add_argument('--pins', type=Path, default=directory / 'portable_pins.json')
    parser.add_argument('--output', type=Path, default=directory / 'dependency_images.json')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='dependency-images-') as work:
        manifest = generate(args.pins, Path(work))
    args.output.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
