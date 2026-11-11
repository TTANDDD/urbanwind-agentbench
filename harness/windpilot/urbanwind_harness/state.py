from __future__ import annotations

import re
from pathlib import Path

from .common import digest


STAGES = ("geometry", "configuration", "mesh", "solve", "postprocess", "deliver")
DEPENDENCIES = {"geometry": (), "configuration": (), "mesh": ("geometry",),
                "solve": ("mesh", "configuration"), "postprocess": ("solve",),
                "deliver": ("postprocess",)}


def descendants(changed):
    affected = set(changed)
    for stage in STAGES:
        if any(dep in affected for dep in DEPENDENCIES[stage]):
            affected.add(stage)
    return affected


def fingerprint(case: Path) -> dict:
    groups = {"geometry": [], "configuration": [], "mesh": [], "solve": [], "postprocess": [], "unverified_dependencies": []}
    folders=[case/name for name in ("system", "constant", "0")]
    for processor in case.iterdir():
        if re.fullmatch(r'processor\d+',processor.name) and processor.is_dir():
            if processor.is_symlink():
                groups['unverified_dependencies'].append(processor.name)
            else:
                folders.extend(processor/name for name in ('system','constant','0'))
    for root in folders:
        if root.is_symlink():
            groups['unverified_dependencies'].append(root.relative_to(case).as_posix())
            continue
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.resolve().is_relative_to(case.resolve()):
                groups['unverified_dependencies'].append(path.relative_to(case).as_posix())
                continue
            if not path.is_file():
                continue
            relative = path.relative_to(case).as_posix()
            if "/polyMesh/" in relative:
                stage = "mesh"
            elif "triSurface/" in relative or path.name in ("blockMeshDict", "snappyHexMeshDict"):
                stage = "geometry"
            elif path.name in ("sampleDict", "probes", "surfaces"):
                stage = "postprocess"
            else:
                stage = "configuration"
            groups[stage].append((relative, digest(path)))
            if stage == 'configuration' and path.stat().st_size < 4*1024*1024:
                if re.search(r'#include(?!Etc)|#codeStream|codedFixedValue|codedMixed',path.read_text(errors='replace')):
                    groups['unverified_dependencies'].append(relative+':dynamic_or_external_include')
    # Solver products are represented by their configuration binding in the ledger,
    # not inferred valid merely because a time directory exists.
    return groups


def case_directories(root: Path):
    found = []
    for control in root.rglob("controlDict"):
        if control.parent.name != "system" or control.is_symlink():
            continue
        case = control.parent.parent
        if "local_assets" in case.relative_to(root).parts:
            continue
        if root.resolve() not in case.resolve().parents:
            continue
        found.append(case)
    return sorted(set(found))


class EvidenceState:
    def __init__(self):
        self.cases = {}

    def refresh(self, root: Path):
        events = []
        live = set()
        for case in case_directories(root):
            identity = case.relative_to(root).as_posix()
            live.add(identity)
            current = fingerprint(case)
            old = self.cases.get(identity)
            if old:
                changed = {stage for stage in current if current[stage] != old["fingerprints"].get(stage)}
                if 'unverified_dependencies' in changed:
                    changed.add('configuration')
                stale = descendants(changed) if changed else set()
                old["stale"].update(stale)
                if changed:
                    events.append({"case": identity, "changed": sorted(changed), "invalidated": sorted(stale)})
                old["fingerprints"] = current
            else:
                self.cases[identity] = {"fingerprints": current, "stale": set(), "verified": set()}
        for identity in set(self.cases) - live:
            self.cases[identity]["stale"].update(STAGES)
            events.append({"case": identity, "invalidated": list(STAGES), "reason": "case_removed"})
        return events

    def verified(self, identity, stages):
        entry = self.cases[identity]
        for stage in stages:
            entry["stale"].discard(stage)
            entry["verified"].add(stage)

    def snapshot(self):
        return {case: {"stale": sorted(item["stale"]), "execution_recorded_stages": sorted(item["verified"])}
                for case, item in self.cases.items()}


def recovery_plan(stale, interrupted=False, checkpoint_complete=False,
                  unchanged_configuration=False, restart_allowed=False):
    stale = set(stale)
    if interrupted and checkpoint_complete and unchanged_configuration and restart_allowed:
        return {"action": "resume_checkpoint", "must_verify": ["configuration", "mesh", "checkpoint", "time_window"]}
    if stale & {"geometry", "mesh"}:
        return {"action": "rebuild_mesh_and_rerun", "stages": ["mesh", "solve", "postprocess", "deliver"]}
    if "configuration" in stale or "solve" in stale:
        return {"action": "rerun_case", "stages": ["solve", "postprocess", "deliver"]}
    if stale & {"postprocess", "deliver"}:
        return {"action": "redo_postprocessing", "stages": ["postprocess", "deliver"]}
    return {"action": "inspect_evidence", "reason": "no_certified_reusable_solution"}
