"""Create an owned Ubuntu rootfs from a verified official anonymous OCI image.

No credential or host root filesystem is copied. This is runtime preparation,
not a model trajectory or a numerical benchmark. Run on the Linux host.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path


def request(url, headers=None):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=120)


def bootstrap(destination, root, manifest):
    destination, root = Path(destination), Path(root)
    resolver = root / 'etc/resolv.conf'
    if resolver.is_symlink(): resolver.unlink()
    shutil.copyfile('/etc/resolv.conf', resolver)
    argv = ['bwrap', '--unshare-all', '--share-net', '--uid', '0', '--gid', '0',
            '--bind', str(root), '/', '--dev', '/dev', '--tmpfs', '/proc',
            '--setenv', 'PATH', '/usr/bin:/bin:/usr/sbin:/sbin', '--setenv', 'HOME', '/root',
            '--setenv', 'DEBIAN_FRONTEND', 'noninteractive', '--', '/bin/bash', '-euc',
            'apt-get -o APT::Sandbox::User=root update; '
            'apt-get -o APT::Sandbox::User=root install -y --no-install-recommends '
            'python3 python3-pip python3-numpy python3-scipy python3-matplotlib ca-certificates curl git '
            'build-essential procps rsync slirp4netns; apt-get clean']
    with (destination / 'bootstrap.stdout.txt').open('wb') as out, (destination / 'bootstrap.stderr.txt').open('wb') as err:
        code = subprocess.call(argv, stdout=out, stderr=err)
    layers = [{'sha256': item['digest'].split(':')[1]} for item in manifest['layers']]
    receipt = {'source': manifest.get('source', 'Official library/ubuntu:22.04 amd64 anonymous OCI'), 'layers': layers,
               'bootstrap_exit_code': code, 'guest_uid': 0, 'host_uid': os.getuid(),
               'procfs_limit': 'Host denies procfs mounts inside user namespaces; limited hardware metadata only.',
               'network': 'Shared host network during runtime preparation', 'rootfs': str(root)}
    (destination / 'runtime-manifest.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt), flush=True)
    if code: raise SystemExit(code)


def prepare(destination, cloud=False, bootstrap_existing=False):
    destination = destination.resolve()
    if bootstrap_existing:
        bootstrap(destination, destination / 'rootfs', json.loads((destination / 'oci-manifest.json').read_text()))
        return
    destination.mkdir(parents=True, exist_ok=False)
    cache, root = destination / 'downloads', destination / 'rootfs'
    cache.mkdir(); root.mkdir()
    headers = {}
    if cloud:
        base = 'https://cloud-images.ubuntu.com/minimal/releases/jammy/release/'
        name = 'ubuntu-22.04-minimal-cloudimg-amd64-root.tar.xz'
        checksums = request(base + 'SHA256SUMS').read().decode()
        expected = next(line.split()[0] for line in checksums.splitlines() if line.split()[-1].lstrip('*') == name)
        manifest = {'layers': [{'digest': 'sha256:' + expected, 'url': base + name}],
                    'source': 'Official Ubuntu 22.04 minimal cloud rootfs amd64'}
        (destination / 'source-SHA256SUMS.txt').write_text(checksums)
    else:
        token = json.load(request('https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/ubuntu:pull'))['token']
        headers = {'Authorization': 'Bearer ' + token,
        'Accept': ','.join(['application/vnd.oci.image.index.v1+json', 'application/vnd.oci.image.manifest.v1+json',
                           'application/vnd.docker.distribution.manifest.list.v2+json', 'application/vnd.docker.distribution.manifest.v2+json'])}
        base = 'https://registry-1.docker.io/v2/library/ubuntu/'
        manifest = json.load(request(base + 'manifests/22.04', headers))
        if 'manifests' in manifest:
            item = next(item for item in manifest['manifests'] if item.get('platform', {}).get('architecture') == 'amd64'
                        and item.get('platform', {}).get('os') == 'linux')
            manifest = json.load(request(base + 'manifests/' + item['digest'], headers))
    (destination / 'oci-manifest.json').write_text(json.dumps(manifest, indent=2))
    layers = []
    for item in manifest['layers']:
        archive = cache / (item['digest'].split(':')[1] + '.tar.gz')
        with request(item.get('url', base + 'blobs/' + item['digest']), headers) as source, archive.open('wb') as output:
            shutil.copyfileobj(source, output)
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if 'sha256:' + actual != item['digest']:
            raise ValueError('OCI layer digest mismatch')
        # Extract without preserving external ownership or allowing host escapes.
        with tarfile.open(archive) as tar:
            for member in tar:
                relative = Path(member.name)
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('unsafe OCI path')
                target = root / relative
                if not target.parent.resolve().is_relative_to(root):
                    raise ValueError('OCI parent escaped owned rootfs')
                target.parent.mkdir(parents=True, exist_ok=True)
                if member.isdir():
                    target.mkdir(exist_ok=True)
                elif member.issym():
                    link = member.linkname
                    if link.startswith('/'):
                        link = os.path.relpath(root / link.lstrip('/'), target.parent)
                    if target.is_symlink() or target.exists(): target.unlink()
                    target.symlink_to(link)
                elif member.islnk():
                    original = root / member.linkname.lstrip('/')
                    if not original.resolve().is_relative_to(root): raise ValueError('unsafe hardlink')
                    os.link(original, target)
                elif member.isfile():
                    with tar.extractfile(member) as source, target.open('wb') as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(member.mode & 0o777)
                elif not (member.ischr() or member.isblk() or member.isfifo()):
                    raise ValueError('unsupported rootfs member type')
        layers.append({'sha256': actual, 'bytes': archive.stat().st_size})
    for name in ('proc', 'sys', 'dev', 'tmp', 'run', 'home/user/Desktop'):
        (root / name).mkdir(parents=True, exist_ok=True)
    bootstrap(destination, root, manifest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    parser.add_argument('--cloud', action='store_true')
    parser.add_argument('--bootstrap-existing', action='store_true')
    args = parser.parse_args()
    prepare(args.destination, cloud=args.cloud, bootstrap_existing=args.bootstrap_existing)
