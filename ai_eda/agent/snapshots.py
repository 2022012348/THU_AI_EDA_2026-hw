"""Versioned RTL snapshots and byte-level verification of run artifacts."""

import json
from hashlib import sha256
from pathlib import Path


def fingerprint(path: Path) -> dict:
    digest = sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"sha256": digest.hexdigest(), "bytes": size}


def resolve_artifact(root: Path, reference: str) -> Path:
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError("artifact 引用不能为空")
    root = root.resolve()
    path = (root / reference).resolve()
    if root not in path.parents:
        raise ValueError("artifact 引用越出运行目录")
    return path


def save_snapshot(runtime, files: dict[str, str], attempt: int, parent: str | None = None) -> str:
    while (runtime.artifacts_dir / f"rtl_manifest_attempt{attempt}.json").exists():
        attempt += 1
    entries = []
    for path, content in files.items():
        ref = runtime.write_text_artifact(f"snapshots/{attempt:02d}/{path}", content)
        entries.append({"path": path, "artifact": ref, **fingerprint(runtime.run_dir / ref)})
    return runtime.write_json_artifact(
        f"rtl_manifest_attempt{attempt}.json",
        {"schema_version": 1, "attempt": attempt, "parent": parent, "files": entries},
    )


def load_snapshot(runtime, reference: str) -> dict[str, str]:
    return load_snapshot_at(runtime.run_dir, reference)


def load_snapshot_at(run_dir: Path, reference: str) -> dict[str, str]:
    manifest = json.loads(resolve_artifact(run_dir, reference).read_text(encoding="utf-8"))
    entries = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("RTL manifest 缺少文件")
    files = {}
    for entry in entries:
        path, ref = entry["path"], entry["artifact"]
        if path in files:
            raise ValueError(f"RTL manifest 文件重复：{path}")
        data = resolve_artifact(run_dir, ref).read_bytes()
        if "sha256" in entry:
            actual = {"sha256": sha256(data).hexdigest(), "bytes": len(data)}
            if any(entry.get(key) != value for key, value in actual.items()):
                raise ValueError(f"RTL snapshot 哈希不匹配：{path}")
        files[path] = data.decode("utf-8")
    return files
