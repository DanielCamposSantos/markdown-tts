from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Caminho inseguro no backup: {value}")
    return path


def _safe_file(root: Path, relative: Path) -> Path:
    candidate = root / relative
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Caminho escapa da raiz: {relative.as_posix()}") from exc
    if candidate.is_symlink() or any(parent.is_symlink() for parent in candidate.parents if parent != root.parent):
        raise ValueError(f"Symlink não permitido: {relative.as_posix()}")
    return candidate


@dataclass(frozen=True)
class BackupVerification:
    healthy: bool
    errors: tuple[str, ...]
    manifest: dict | None = None


def verify_backup(backup_path: Path) -> BackupVerification:
    root = Path(backup_path).resolve()
    errors: list[str] = []
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1:
            errors.append("schema_version não suportada")
        seen = set()
        for item in manifest.get("files", []):
            relative = _safe_relative(item["path"])
            if relative.as_posix() in seen:
                errors.append(f"entrada duplicada: {relative.as_posix()}")
                continue
            seen.add(relative.as_posix())
            path = _safe_file(root, relative)
            if not path.is_file() or path.is_symlink():
                errors.append(f"arquivo ausente/inválido: {relative.as_posix()}")
            elif path.stat().st_size != item["size_bytes"] or _hash(path) != item["sha256"]:
                errors.append(f"hash/tamanho divergente: {relative.as_posix()}")
        actual = {
            path.relative_to(root).as_posix() for path in root.rglob("*")
            if path.is_file() and path.name != "manifest.json"
        }
        if actual != seen:
            errors.append("payload contém arquivos não manifestados ou omite arquivos")
        db = root / "database.sqlite3"
        if db.is_file():
            with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as connection:
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    errors.append("SQLite quick_check falhou")
        else:
            errors.append("database.sqlite3 ausente")
        voice = root / "voices" / "narrator_reference.wav"
        identity = manifest.get("voice_identity", {})
        if voice.is_file() and _hash(voice) != identity.get("sha256"):
            errors.append("voz não corresponde à identidade do manifesto")
    except Exception as exc:
        return BackupVerification(False, (str(exc),), None)
    return BackupVerification(not errors, tuple(errors), manifest)


class BackupService:
    def __init__(self, project_root: Path, library_root: Path, backup_root: Path, voice_path: Path, voice_manifest_path: Path, *, now=None) -> None:
        self.project_root, self.library_root = Path(project_root).resolve(), Path(library_root).resolve()
        self.backup_root, self.voice_path, self.voice_manifest_path = Path(backup_root).resolve(), Path(voice_path).resolve(), Path(voice_manifest_path).resolve()
        self.now = now or (lambda: datetime.now(timezone.utc))

    def create(self) -> Path:
        stamp = self.now().strftime("%Y%m%dT%H%M%S.%fZ")
        final = self.backup_root / f"backup-{stamp}"
        staging = self.backup_root / f".backup-{stamp}-{uuid.uuid4().hex}.tmp"
        if final.exists():
            raise FileExistsError(final)
        try:
            staging.mkdir(parents=True)
            (staging / "library").mkdir()
            db_out = staging / "database.sqlite3"
            with closing(sqlite3.connect(self.library_root / "library.db")) as source, closing(sqlite3.connect(db_out)) as destination:
                source.backup(destination)
            paths = self._referenced_paths(db_out)
            for relative in paths:
                source = _safe_file(self.library_root, _safe_relative(relative))
                if source.is_symlink() or not source.is_file():
                    raise FileNotFoundError(f"Artefato referenciado ausente/inseguro: {relative}")
                target = staging / "library" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            fixed = [
                (self.voice_path, Path("voices/narrator_reference.wav")),
                (self.voice_manifest_path, Path("voices/narrator_reference.manifest.json")),
                (self.project_root / "app/pronunciation/pt_br.py", Path("app/pronunciation/pt_br.py")),
                (self.project_root / "app/persistence/migrations.py", Path("app/persistence/migrations.py")),
            ]
            for source, relative in fixed:
                if source.is_symlink() or not source.is_file():
                    raise FileNotFoundError(source)
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            files = []
            for path in sorted(x for x in staging.rglob("*") if x.is_file()):
                files.append({"path": path.relative_to(staging).as_posix(), "size_bytes": path.stat().st_size, "sha256": _hash(path)})
            with closing(sqlite3.connect(db_out)) as connection:
                counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("documents", "generations", "generation_jobs")}
                schema = connection.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
            voice_manifest = json.loads(self.voice_manifest_path.read_text(encoding="utf-8"))
            manifest = {"schema_version": 1, "created_at": self.now().isoformat(), "database_schema_version": schema, "counts": counts, "voice_identity": {"sha256": voice_manifest["sha256"], "size_bytes": voice_manifest["size_bytes"]}, "files": files}
            (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            if not verify_backup(staging).healthy:
                raise RuntimeError("Autoverificação do backup falhou")
            self.backup_root.mkdir(parents=True, exist_ok=True)
            os.replace(staging, final)
            self._audit("backup_created", {"backup": final.name})
            return final
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _referenced_paths(self, db_path: Path) -> set[str]:
        result: set[str] = set()
        with closing(sqlite3.connect(db_path)) as connection:
            for (path,) in connection.execute("SELECT markdown_path FROM documents"):
                result.add(path)
            for audio, metadata in connection.execute("SELECT audio_path,metadata_path FROM generations WHERE status='completed'"):
                for value in (audio, metadata):
                    if value:
                        result.add(value)
                if metadata:
                    data = json.loads((self.library_root / _safe_relative(metadata)).read_text(encoding="utf-8"))
                    result.update(data.get("unit_artifacts", []))
        return result

    def _audit(self, action: str, details: dict) -> None:
        target = self.backup_root / "audit.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"timestamp": self.now().isoformat(), "action": action, **details}, ensure_ascii=False) + "\n")


class RestoreService:
    def __init__(self, backup_service: BackupService) -> None:
        self.backups = backup_service

    def restore(self, backup_path: Path, *, restore_voice: bool = False, skip_safety_backup: bool = False, failure_hook=None) -> dict:
        verification = verify_backup(backup_path)
        if not verification.healthy:
            raise ValueError("Backup inválido: " + "; ".join(verification.errors))
        db = self.backups.library_root / "library.db"
        with closing(sqlite3.connect(db)) as connection:
            active = connection.execute("SELECT COUNT(*) FROM generation_jobs WHERE status IN ('queued','running','cancelling')").fetchone()[0]
        if active:
            raise RuntimeError("Restore recusado: existem jobs ativos.")
        safety = None if skip_safety_backup else self.backups.create()
        root = self.backups.library_root
        staged = root.parent / f".{root.name}.restore-{uuid.uuid4().hex}.tmp"
        old = root.parent / f".{root.name}.restore-old-{uuid.uuid4().hex}"
        voice_old = None
        voice_manifest_old = None
        try:
            shutil.copytree(Path(backup_path) / "library", staged)
            shutil.copy2(Path(backup_path) / "database.sqlite3", staged / "library.db")
            with closing(sqlite3.connect(staged / "library.db")) as connection:
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok": raise RuntimeError("SQLite restaurado inválido")
            os.replace(root, old)
            os.replace(staged, root)
            if restore_voice:
                voice_old = self.backups.voice_path.with_name(f".{self.backups.voice_path.name}.restore-old")
                voice_manifest_old = self.backups.voice_manifest_path.with_name(f".{self.backups.voice_manifest_path.name}.restore-old")
                shutil.copy2(self.backups.voice_path, voice_old)
                shutil.copy2(self.backups.voice_manifest_path, voice_manifest_old)
                shutil.copy2(Path(backup_path) / "voices/narrator_reference.wav", self.backups.voice_path)
                shutil.copy2(Path(backup_path) / "voices/narrator_reference.manifest.json", self.backups.voice_manifest_path)
            if failure_hook: failure_hook()
            shutil.rmtree(old)
            if voice_old: voice_old.unlink(missing_ok=True)
            if voice_manifest_old: voice_manifest_old.unlink(missing_ok=True)
            self.backups._audit("restore_completed", {"backup": Path(backup_path).name, "voice_restored": restore_voice})
            return {"restored": True, "safety_backup": str(safety) if safety else None, "voice_restored": restore_voice}
        except Exception:
            shutil.rmtree(staged, ignore_errors=True)
            if old.exists():
                if root.exists(): shutil.rmtree(root)
                os.replace(old, root)
            if voice_old and voice_old.exists(): os.replace(voice_old, self.backups.voice_path)
            if voice_manifest_old and voice_manifest_old.exists(): os.replace(voice_manifest_old, self.backups.voice_manifest_path)
            raise
