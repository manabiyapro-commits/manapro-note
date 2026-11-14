#!/usr/bin/env python3
"""Read-only note sync; run metadata lives outside the archived brand files."""
import argparse
import json
import os
from pathlib import Path
import note_tool


def run(brand, report_dir):
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {"brand": brand, "started_at": note_tool.now_iso(),
              "status": "failed", "run_url": os.environ.get("RUN_URL", "")}
    code = 1
    try:
        result = note_tool.cmd_fetch(argparse.Namespace(
            brand=brand, delay=5.0, limit=0, refresh=False))
        report.update(result)
        report["status"] = "partial_failure" if result["failed"] else (
            "updated" if result["saved"] else "no_change")
        # Only a completed RSS check counts as last_checked_at.
        report["last_checked_at"] = note_tool.now_iso()
        code = int(bool(result["failed"]))
    except (Exception, SystemExit) as exc:
        report["error"] = str(exc)
        print(f"同期失敗: {exc}", flush=True)
    finally:
        report["finished_at"] = note_tool.now_iso()
        (report_dir / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary = ("## note archive\n\n```json\n" +
                   json.dumps(report, ensure_ascii=False, indent=2) + "\n```\n")
        (report_dir / "summary.md").write_text(summary, encoding="utf-8")
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(summary)
    return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--brand", default="manabiya-project")
    parser.add_argument("--report-dir", type=Path, default=Path("run-report"))
    args = parser.parse_args()
    raise SystemExit(run(args.brand, args.report_dir))
