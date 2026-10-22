"""Stop hook: when the interface changed since the last check, Claude may not finish before verifying it is
keyboard first (CLAUDE.md §4). Blocks once per change; the fingerprint of the interface files is kept in
.claude/keyboard-check.state (ignored by git)."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".claude" / "keyboard-check.state"
WATCHED = [*sorted((ROOT / "arca" / "ui").glob("*.py")), *sorted((ROOT / "arca" / "ui").glob("*.tcss")),
           ROOT / "arca" / "i18n.py"]

CHECKLIST = """Keyboard-first check (CLAUDE.md §4): the interface changed since the last check.
Before finishing, verify each point and say in your reply, briefly, what you checked:
1. Every new or changed action works with the keyboard alone (arrows, Enter, Esc, space/Tab, Del), not only with a
   click or a letter shortcut.
2. While exploring (a tree, a list), Enter and the arrows only open and move. Whatever saves, adds, runs or deletes is
   its own line, a confirmation or Ctrl+S, never the key that explores.
3. A Pilot test drives the change with pilot.press() only (no clicks, no direct method calls) and passes.
4. The screen's hint line names the keys that really work there and fits on one line at 110 columns.
5. You looked at a screenshot of the change (save_screenshot -> PNG).
Fix anything that fails now, then finish."""


def fingerprint() -> str:
    digest = hashlib.sha256()
    for path in WATCHED:
        if path.exists():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        event = {}
    if event.get("stop_hook_active"):
        return  # already continuing because of this check: never loop
    current = fingerprint()
    if STATE.exists() and STATE.read_text(encoding="utf-8").strip() == current:
        return
    STATE.write_text(current, encoding="utf-8")
    print(json.dumps({"decision": "block", "reason": CHECKLIST}))


if __name__ == "__main__":
    main()
