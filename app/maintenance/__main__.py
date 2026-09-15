from __future__ import annotations

import argparse
import json

from app.config import LIBRARY_DIR, REFERENCE_AUDIO, ROOT
from app.maintenance.backup import BackupService, RestoreService, verify_backup
from app.maintenance.retention import RetentionService


def services():
    backup = BackupService(ROOT, LIBRARY_DIR, ROOT / "backups", REFERENCE_AUDIO, ROOT / "voices/narrator_reference.manifest.json")
    return backup, RetentionService(LIBRARY_DIR)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manutenção segura do Markdown TTS")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backup")
    verify = sub.add_parser("verify-backup"); verify.add_argument("path")
    sub.add_parser("retention-scan")
    cleanup = sub.add_parser("cleanup-safe"); cleanup.add_argument("--apply", action="store_true")
    restore = sub.add_parser("restore"); restore.add_argument("path"); restore.add_argument("--apply", action="store_true"); restore.add_argument("--restore-voice", action="store_true")
    args = parser.parse_args()
    backup, retention = services()
    if args.command == "backup":
        print(backup.create()); return 0
    if args.command == "verify-backup":
        result = verify_backup(args.path); print(json.dumps({"healthy": result.healthy, "errors": result.errors}, ensure_ascii=False, indent=2)); return 0 if result.healthy else 1
    if args.command == "retention-scan":
        print(json.dumps(retention.scan().to_dict(), ensure_ascii=False, indent=2)); return 0
    if args.command == "cleanup-safe":
        print(json.dumps(retention.cleanup_safe(apply=args.apply).to_dict(), ensure_ascii=False, indent=2)); return 0
    if not args.apply:
        parser.error("restore exige --apply")
    print(json.dumps(RestoreService(backup).restore(args.path, restore_voice=args.restore_voice), ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
