"""Voice command dispatcher.

A held command hotkey records audio, which the main loop transcribes via
Whisper and hands to `dispatch(text)`. This module does three things:

1. Normalize and regex-match the transcription against known intents.
2. Resolve target names through a small Russian → English alias map so
   e.g. "таск кил ревит" ends up as `target="revit"` despite Whisper
   transcribing it in Cyrillic.
3. Execute the action via a narrowly-scoped PowerShell call. The target
   is sanitized to `[a-z0-9_-]` before interpolation so the utterance
   can't escape the PS single-quoted wildcard.

Adding a new command: write a parser branch in `parse_command` and an
executor, then wire them both into `dispatch`.
"""

import re
import subprocess

# Trailing words Whisper likes to tack on ("Task, kill Revit please.") —
# stripped from the end of the target so "revit please" -> "revit".
_TRAILING_FILLER = {
    "please", "now", "all", "the", "it", "already",
    "пожалуйста", "всё", "все", "сейчас", "уже", "блин",
}


def _normalize(text: str) -> str:
    """Lowercase + punctuation → spaces + whitespace collapse.

    Whisper transcribes stop words with commas ("Task, kill, Revit.");
    without this the regex would fail to see `task` followed by a space
    before `kill`.
    """
    t = text.lower()
    t = re.sub(r"[.,!?;:\-—–()\"'«»]+", " ", t)
    return " ".join(t.split())

# Russian-Cyrillic → Windows image-name roots. Process names are matched
# as `*<root>*` so "revit" also catches Revit.exe, RevitAccelerator.exe, etc.
RU_EN_APPS = {
    "ревит":       "revit",
    "ревита":      "revit",
    "ревите":      "revit",
    "ревиты":      "revit",
    "ворд":        "winword",
    "эксель":      "excel",
    "эксел":       "excel",
    "паверпоинт":  "powerpnt",
    "повер пойнт": "powerpnt",
    "поверпойнт":  "powerpnt",
    "оутлук":      "outlook",
    "аутлук":      "outlook",
    "тимс":        "teams",
    "хром":        "chrome",
    "фаерфокс":    "firefox",
    "блокнот":     "notepad",
    "нотпад":      "notepad",
    "дискорд":     "discord",
    "телеграм":    "telegram",
    "слак":        "slack",
    "навис":       "roamer",       # Navisworks Roamer is the actual exe
    "навизворкс":  "roamer",
    "автокад":     "acad",
    "райно":       "rhino",
    "райна":       "rhino",
    "райну":       "rhino",
    "райне":       "rhino",
    "рино":        "rhino",
    "рина":        "rhino",
    "рину":        "rhino",
    "рине":        "rhino",
    "рано":        "rhino",
    "райнок":      "rhino",
    "райнос":      "rhino",
    "navis":       "roamer",
    "navisworks":  "roamer",
    "navis work":  "roamer",
    "navis works": "roamer",
    "nevis":       "roamer",
    "nevisworks":  "roamer",
    "nevis work":  "roamer",
    "nevis works": "roamer",
    "this works":  "roamer",
    "and this works": "roamer",
    "roamer":      "roamer",
    "rhino":       "rhino",
    "rino":        "rhino",
    "ryno":        "rhino",
    "rhyno":       "rhino",
    "rhine":       "rhino",
    "rhinos":      "rhino",
    "rhino 3d":    "rhino",
    "rhino three d": "rhino",
    "mcneel rhino": "rhino",
    "rhinoceros":  "rhino",
    "rhino ceros": "rhino",
    "rhino zeros": "rhino",
    "rhinocrose":  "rhino",
    "rhinocross":  "rhino",
    "rhino crows": "rhino",
    "rhino c rose": "rhino",
    "код":         "code",          # VS Code
    "вс код":      "code",
    "пэйнт":       "mspaint",
    "пайнт":       "mspaint",
    "калькулятор": "calculator",
    "калк":        "calculator",
}


def _resolve_target(raw: str) -> str:
    """Map a Cyrillic spoken name to a canonical English process-name root.

    Tries exact match first, then longest prefix match (handles case-
    endings like "ревита"). Falls back to the raw input unchanged so
    English-as-spoken names ("chrome", "code") pass through.
    """
    raw = raw.strip().lower().rstrip(".,!?")
    if raw in RU_EN_APPS:
        return RU_EN_APPS[raw]
    for ru in sorted(RU_EN_APPS, key=len, reverse=True):
        if raw.startswith(ru):
            return RU_EN_APPS[ru]
    return raw


def _sanitize_process_name(name: str) -> str | None:
    """Strip anything a PowerShell wildcard literal can't safely hold."""
    safe = re.sub(r"[^a-z0-9\-_]", "", name.lower())
    if len(safe) < 2 or len(safe) > 64:
        return None
    return safe


# Accepts both Cyrillic ("таск кил[л...]") and Latin ("task kill") forms,
# with any mix between the two halves.
_KILL_RE = re.compile(
    r"(?:таск|такс|task|tasks|that\s+s|thats|test|desk)\s+"
    r"(?:кил\w*|kill\w*)\s+(.+)$",
    re.IGNORECASE,
)

# Whisper often turns short command words into nearby English words
# ("PASC, KIL", "TASK, HEAL"). Keep the parser permissive, while the
# target sanitizer still limits what can be executed.
_KILL_RE = re.compile(
    r"(?:\u0442\u0430\u0441\u043a|\u0442\u0430\u043a\u0441|"
    r"task|tasks|that\s+s|thats|test|desk|pasc|pass|ask)\s+"
    r"(?:\u043a\u0438\u043b\w*|kill\w*|kil\w*|heal\w*)\s+(.+)$",
    re.IGNORECASE,
)


def parse_command(text: str):
    """Return `(action, params)` or `None` if nothing matches."""
    t = _normalize(text)
    m = _KILL_RE.search(t)
    if not m:
        return None
    tokens = [tok for tok in m.group(1).split() if tok]
    # Strip trailing filler ("… please", "… пожалуйста") so it doesn't
    # get glued into the process-name wildcard.
    while tokens and tokens[-1] in _TRAILING_FILLER:
        tokens.pop()
    if not tokens:
        return None
    spoken = " ".join(tokens)
    resolved = _resolve_target(spoken)
    # Multi-word targets like "beam vision" → "beamvision" when no alias
    # hit — Whisper often splits compound product names.
    if resolved == spoken and len(tokens) > 1:
        resolved = "".join(tokens)
    safe = _sanitize_process_name(resolved)
    if safe:
        return ("kill_process", {
            "target": safe,
            "spoken": spoken,
            "resolved": resolved,
        })
    return None


def kill_process(target: str) -> tuple[int, list[str]]:
    """Kill every process whose image-name contains `target` (case-insensitive).

    Returns `(count, names)`. `target` MUST already be sanitized.
    """
    ps_cmd = (
        "Get-Process | "
        f"Where-Object {{ $_.ProcessName -like '*{target}*' }} | "
        r"ForEach-Object { '{0}|{1}' -f $_.Id, $_.ProcessName }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
        capture_output=True, text=True, timeout=10,
    )
    matches = []
    seen = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        pid_raw, name = line.split("|", 1)
        try:
            pid = int(pid_raw)
        except ValueError:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        matches.append((pid, name.strip()))

    names = []
    for pid, name in matches:
        kill = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, text=True, timeout=10,
        )
        output = f"{kill.stdout}\n{kill.stderr}"
        if kill.returncode == 0 or "SUCCESS:" in output:
            names.append(name)
    return len(names), names


def dispatch(text: str) -> dict:
    """Parse `text` and run the matched action.

    Returns a dict `{matched, action, message}` the caller can log and
    surface in the UI. Exceptions from the executor are caught and
    surfaced in `message` rather than propagated, so one bad command
    doesn't kill the worker thread.
    """
    parsed = parse_command(text)
    if parsed is None:
        return {
            "matched": False,
            "ok": False,
            "action": None,
            "command_id": None,
            "message": f"no command matched: {text!r}",
        }
    action, params = parsed
    if action == "kill_process":
        target = params["target"]
        spoken = params["spoken"]
        command_id = f"task-kill-{target}"
        try:
            count, names = kill_process(target)
        except Exception as e:
            return {
                "matched": True,
                "ok": False,
                "action": action,
                "command_id": command_id,
                "message": f"kill_process target={target!r} "
                           f"(spoken={spoken!r}) failed: {e}",
            }
        if count == 0:
            return {
                "matched": True,
                "ok": False,
                "action": action,
                "command_id": command_id,
                "message": f"kill_process target={target!r} "
                           f"(spoken={spoken!r}): no matching process",
            }
        return {
            "matched": True,
            "ok": True,
            "action": action,
            "command_id": command_id,
            "message": f"kill_process target={target!r} "
                       f"(spoken={spoken!r}): killed {count} "
                       f"({', '.join(names)})",
        }
    return {
        "matched": False,
        "ok": False,
        "action": None,
        "command_id": None,
        "message": f"unknown action: {action}",
    }
