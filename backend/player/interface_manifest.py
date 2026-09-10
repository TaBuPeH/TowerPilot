"""Read-only screen catalogue; narrated geometry is never execution proof."""
import hashlib

from player.bootstrap_layout import manifest
from player.manifest_driver import catalogue


def coverage(template_path, entries):
    """Bind shipped knowledge to this account's current, screen-specific cuts.

    A failed rescan supersedes an older success. A reference rectangle is not proof.
    """
    latest = {e['rel']: e for e in entries if e.get('rel')}
    screens = []
    for name, spec in catalogue().items():
        controls = []
        for key, control in spec.get('controls', {}).items():
            rel = control.get('template')
            state = 'documented'
            if rel:
                try:
                    digest = hashlib.sha256(template_path(rel).read_bytes()).hexdigest()
                except OSError:
                    digest = None
                proof = latest.get(rel, {})
                if digest:
                    state = 'captured'
                    if (proof.get('verified') and proof.get('screen') == name
                            and proof.get('image_sha256') == digest):
                        state = 'verified'
            controls.append(dict(id=key, label=control['label'], status=state,
                                 optional=control.get('optional', False), template=rel))
        verified = sum(c['status'] == 'verified' for c in controls)
        screens.append(dict(id=name, label=spec['label'], acquisition=spec['acquisition'],
                            controls=controls, verified=verified, total=len(controls),
                            status='verified' if controls and verified == len(controls) else 'incomplete',
                            rules=spec.get('rules', [])))
    return dict(version=manifest()['version'], screens=screens,
                verified=sum(s['verified'] for s in screens),
                total=sum(s['total'] for s in screens))
