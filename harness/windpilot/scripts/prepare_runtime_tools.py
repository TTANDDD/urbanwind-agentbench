"""Extract the installed Ubuntu coreutils ls into this revision, never the OS."""
import argparse
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', type=Path, default=Path('/var/cache/apt/archives/coreutils_8.32-4.1ubuntu1.4_amd64.deb'))
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'runtime-tools')
    args = parser.parse_args()
    version = subprocess.run(['dpkg-deb','-f',str(args.package),'Version'],check=True,capture_output=True,text=True).stdout.strip()
    package_sha = hashlib.sha256(args.package.read_bytes()).hexdigest()
    metadata = subprocess.run(['apt-cache','show','coreutils'],check=True,capture_output=True,text=True).stdout
    entry = next((block for block in metadata.split('\n\n') if ('Version: '+version+'\n') in block+'\n'
                  and ('SHA256: '+package_sha+'\n') in block+'\n'), None)
    if entry is None:
        raise ValueError('cached_package_not_verified_by_local_APT_index')
    control = subprocess.run(['dpkg-deb','--ctrl-tarfile',str(args.package)],check=True,capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(control)) as handle:
        member = next(item for item in handle if item.name.lstrip('./') == 'md5sums')
        sums = handle.extractfile(member).read().decode()
    expected = next(line.split()[0] for line in sums.splitlines() if line.split()[-1] in ('bin/ls','usr/bin/ls'))
    archive = subprocess.run(['dpkg-deb', '--fsys-tarfile', str(args.package)],
                             check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as handle:
        member = next(item for item in handle.getmembers() if item.name.lstrip('./') in ('bin/ls', 'usr/bin/ls'))
        if not member.isfile():
            raise ValueError('ls_must_be_a_regular_package_file')
        data = handle.extractfile(member).read()
    if hashlib.md5(data).hexdigest() != expected or not data.startswith(b'\x7fELF'):
        raise ValueError('binary_does_not_match_verified_package_metadata')
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output/'ls'
    if target.exists() and target.read_bytes() != data:
        raise ValueError('refuse_to_replace_a_different_qualified_binary')
    target.write_bytes(data)
    target.chmod(0o755)
    manifest = {
        'source': str(args.package), 'package_sha256': package_sha,
        'verification': 'Cached package SHA256 matches local APT index; binary MD5 matches its package control metadata.',
        'package_version': version, 'package_file_md5': expected,
        'binary_sha256': hashlib.sha256(data).hexdigest(),
        'binary_bytes': len(data), 'host_ls_sha256': hashlib.sha256(Path('/usr/bin/ls').read_bytes()).hexdigest(),
        'scope': 'Compatibility overlay shared by every v0.2 condition; no host binary or proc isolation change.',
    }
    (args.output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
