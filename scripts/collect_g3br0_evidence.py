"""Export an allowlist of local proof artifacts; never export the private lab."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, output = args.lab.resolve(), args.output.resolve()
    if output.exists() or output == source or source in output.parents:
        raise ValueError('Use a new export directory outside the private lab')
    output.mkdir(mode=0o700, parents=True)
    secrets = set()
    def inspect(value, key=''):
        if isinstance(value, dict):
            for k, v in value.items():
                inspect(v, k)
        elif isinstance(value, list):
            for v in value:
                inspect(v, key)
        elif isinstance(value, str) and any(x in key.lower() for x in ('password', 'secret', 'signing_key', '_dsn')):
            secrets.add(value)
    for config in source.glob('*config.json'):
        inspect(json.loads(config.read_text()))

    def redact(text):
        for secret in sorted(secrets, key=len, reverse=True):
            if secret:
                text = text.replace(secret, '[REDACTED]')
        text = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[JWT_REDACTED]', text)
        text = re.sub(r'postgres(?:ql)?://[^\s\"\x27]+', '[DATABASE_URL_REDACTED]', text)
        text = re.sub(r'/v1/(uploads|downloads)/[^\s\"\x27<>]+', r'/v1/\1/[CAPABILITY_REDACTED]', text)
        return text

    # No configs, credentials/ready files, Auth logs, DB dumps, quarantine or
    # private object trees are candidates for export, even on a failed run.
    for name in ('local-tests.log', 'browser-tests.log', 'teardown-state.json'):
        target = source / name
        if target.is_file():
            (output / name).write_text(redact(target.read_text(errors='replace')))
    logs = source / 'logs'
    if logs.is_dir():
        for name in ('api-events.jsonl', 'worker-events.jsonl', 'resource-samples.jsonl'):
            file = logs / name
            if file.is_file():
                (output / name).write_text(redact(file.read_text(errors='replace')))
    browser = source / 'browser'
    if browser.is_dir():
        dest = output / 'browser'
        dest.mkdir(mode=0o700)
        for file in browser.iterdir():
            if file.is_file() and not file.is_symlink() and file.suffix in {'.json', '.png', '.mp4', '.md'}:
                if file.suffix in {'.json', '.md'}:
                    (dest / file.name).write_text(redact(file.read_text()))
                else:
                    shutil.copyfile(file, dest / file.name)
    for name in ('concurrency-evidence.json', 'recovery-evidence.json', 'independent-negative-summary.json'):
        file = source / name
        if file.is_file():
            (output / name).write_text(redact(file.read_text()))
    versions = {}
    for key, command in {'python': ['python3', '--version'], 'node': ['node', '--version'],
                         'ffmpeg': ['ffmpeg', '-version'], 'ffprobe': ['ffprobe', '-version']}.items():
        versions[key] = subprocess.check_output(command, text=True).splitlines()[0]
    versions['git_head'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    versions['working_tree_clean'] = not subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()
    versions['scope'] = 'LOCAL_ONLY; cloud NOT_VALIDATED; monetary cost UNKNOWN'
    (output / 'environment.json').write_text(json.dumps(versions, indent=2) + '\n')
    checksums = {str(file.relative_to(output)): hashlib.sha256(file.read_bytes()).hexdigest()
                 for file in sorted(output.rglob('*')) if file.is_file()}
    (output / 'SHA256SUMS.json').write_text(json.dumps(checksums, indent=2) + '\n')
    print(json.dumps({'export': str(output), 'files': len(checksums), 'private_lab_exported': False}))


if __name__ == '__main__':
    main()
