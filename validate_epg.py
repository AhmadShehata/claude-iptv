#!/usr/bin/env python3
"""
EPG Validator for IPTV M3U8 Playlists
Validates that tvg-id values in the playlist match channel IDs in the EPG XML.
"""

import re
import sys
import gzip
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from difflib import get_close_matches
from pathlib import Path


# ── ANSI colours ────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

TICK  = f"{GREEN}✓{RESET}"
CROSS = f"{RED}✗{RESET}"
WARN  = f"{YELLOW}?{RESET}"


# ── Playlist parsing ─────────────────────────────────────────────────────────

def parse_playlist(path: str) -> tuple[str | None, list[dict]]:
    """Return (epg_url, channels) from an M3U8 file."""
    epg_url = None
    channels = []

    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Header — grab EPG URL
        if line.startswith("#EXTM3U"):
            m = re.search(r'x-tvg-url="([^"]+)"', line)
            if m:
                epg_url = m.group(1)

        # Channel entry
        elif line.startswith("#EXTINF"):
            tvg_id   = _attr(line, "tvg-id")
            tvg_name = _attr(line, "tvg-name")
            group    = _attr(line, "group-title")
            display  = re.search(r',(.+)$', line)
            display  = display.group(1).strip() if display else tvg_name or "Unknown"

            # Next non-blank, non-comment line is the stream URL
            url = ""
            j = i + 1
            while j < len(lines):
                candidate = lines[j].strip()
                if candidate and not candidate.startswith("#"):
                    url = candidate
                    break
                j += 1

            channels.append({
                "tvg_id":   tvg_id or "",
                "tvg_name": tvg_name or display,
                "group":    group or "",
                "display":  display,
                "url":      url,
                "line":     i + 1,
            })

        i += 1

    return epg_url, channels


def _attr(line: str, name: str) -> str | None:
    m = re.search(rf'{name}="([^"]*)"', line)
    return m.group(1) if m else None


# ── EPG download & parse ─────────────────────────────────────────────────────

def fetch_epg(url: str) -> tuple[set[str], dict[str, str]]:
    """
    Download and decompress the EPG XML.
    Accepts HTTP/HTTPS URLs or local file paths.
    Returns (set_of_channel_ids, dict_id->display_name).
    """
    print(f"\n{CYAN}Fetching EPG from:{RESET} {url}")

    # Local file
    if not url.startswith("http://") and not url.startswith("https://"):
        try:
            raw = Path(url).read_bytes()
        except OSError as e:
            print(f"{RED}ERROR: Could not read local EPG file — {e}{RESET}")
            sys.exit(1)
    else:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (IPTV EPG Validator)"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.URLError as e:
            print(f"{RED}ERROR: Could not download EPG — {e}{RESET}")
            sys.exit(1)

    # Decompress if gzip
    if url.endswith(".gz") or raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass  # Already plain XML

    print(f"  Downloaded {len(raw):,} bytes — parsing XML …")

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"{RED}ERROR: Invalid XML — {e}{RESET}")
        sys.exit(1)

    ids   = {}
    for ch in root.findall("channel"):
        cid = ch.get("id", "").strip()
        if not cid:
            continue
        name_el = ch.find("display-name")
        name = name_el.text.strip() if name_el is not None and name_el.text else cid
        ids[cid] = name

    return set(ids.keys()), ids


# ── Validation ───────────────────────────────────────────────────────────────

def validate(channels: list[dict], epg_ids: set[str], epg_names: dict[str, str]) -> dict:
    matched   = []
    missing   = []   # tvg-id present but not in EPG
    no_tvg_id = []   # no tvg-id set at all

    epg_ids_lower = {cid.lower(): cid for cid in epg_ids}

    for ch in channels:
        tid = ch["tvg_id"].strip()

        if not tid:
            no_tvg_id.append(ch)
            continue

        if tid in epg_ids:
            # Exact match
            ch["epg_name"] = epg_names[tid]
            matched.append(ch)
        elif tid.lower() in epg_ids_lower:
            # Case-insensitive match — flag as warning
            real_id = epg_ids_lower[tid.lower()]
            ch["suggestion"]  = real_id
            ch["epg_name"]    = epg_names[real_id]
            ch["case_issue"]  = True
            missing.append(ch)
        else:
            # Try fuzzy match
            suggestions = get_close_matches(tid, epg_ids, n=3, cutoff=0.5)
            ch["suggestions"] = suggestions
            missing.append(ch)

    return {"matched": matched, "missing": missing, "no_tvg_id": no_tvg_id}


# ── Report ───────────────────────────────────────────────────────────────────

def print_report(result: dict, epg_ids: set[str]):
    matched   = result["matched"]
    missing   = result["missing"]
    no_tvg_id = result["no_tvg_id"]
    total     = len(matched) + len(missing) + len(no_tvg_id)

    print(f"\n{'='*60}")
    print(f"{BOLD}EPG VALIDATION REPORT{RESET}")
    print(f"{'='*60}")
    print(f"  Total channels :  {total}")
    print(f"  {TICK} Matched       :  {GREEN}{len(matched)}{RESET}")
    print(f"  {CROSS} Not in EPG   :  {RED}{len(missing)}{RESET}")
    print(f"  {WARN} No tvg-id    :  {YELLOW}{len(no_tvg_id)}{RESET}")
    print(f"  EPG channel IDs:  {len(epg_ids):,}")
    print(f"{'='*60}\n")

    # ── Matched ──────────────────────────────────────────────────────────────
    if matched:
        print(f"{BOLD}{GREEN}MATCHED ({len(matched)}){RESET}")
        for ch in matched:
            print(f"  {TICK}  [{ch['group']}] {ch['display']}  "
                  f"{CYAN}→{RESET} EPG: {ch['epg_name']}")

    # ── Missing / wrong case ─────────────────────────────────────────────────
    if missing:
        print(f"\n{BOLD}{RED}NOT MATCHED ({len(missing)}){RESET}")
        for ch in missing:
            tid = ch["tvg_id"]
            if ch.get("case_issue"):
                print(f"  {CROSS}  [{ch['group']}] {ch['display']}")
                print(f"        tvg-id : \"{tid}\"  (wrong case)")
                print(f"        Fix to : \"{ch['suggestion']}\"  "
                      f"(EPG name: {ch['epg_name']})")
            else:
                sugg = ch.get("suggestions", [])
                print(f"  {CROSS}  [{ch['group']}] {ch['display']}  (tvg-id=\"{tid}\")")
                if sugg:
                    print(f"        {YELLOW}Closest EPG IDs:{RESET} "
                          + ", ".join(f'"{s}"' for s in sugg))
                else:
                    print(f"        {YELLOW}No close match found in EPG{RESET}")

    # ── No tvg-id ────────────────────────────────────────────────────────────
    if no_tvg_id:
        print(f"\n{BOLD}{YELLOW}NO TVG-ID SET ({len(no_tvg_id)}){RESET}")
        for ch in no_tvg_id:
            print(f"  {WARN}  [{ch['group']}] {ch['display']}  "
                  f"(line {ch['line']})")

    # ── Summary recommendation ────────────────────────────────────────────────
    print(f"\n{'='*60}")
    pct = int(len(matched) / total * 100) if total else 0
    if pct == 100:
        print(f"{GREEN}{BOLD}All channels have EPG data — playlist is fully functional!{RESET}")
    elif pct >= 70:
        print(f"{YELLOW}{BOLD}EPG coverage: {pct}% — update tvg-id values above to improve.{RESET}")
    else:
        print(f"{RED}{BOLD}EPG coverage: {pct}% — many channels need tvg-id corrections.{RESET}")
    print(f"{'='*60}\n")


# ── Auto-fix ─────────────────────────────────────────────────────────────────

def auto_fix(playlist_path: str, result: dict) -> int:
    """
    Apply case-insensitive fixes automatically.
    Returns the number of fixes applied.
    """
    fixes = [ch for ch in result["missing"] if ch.get("case_issue")]
    if not fixes:
        return 0

    content = Path(playlist_path).read_text(encoding="utf-8")
    for ch in fixes:
        old = f'tvg-id="{ch["tvg_id"]}"'
        new = f'tvg-id="{ch["suggestion"]}"'
        content = content.replace(old, new)

    Path(playlist_path).write_text(content, encoding="utf-8")
    return len(fixes)


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    playlist_path = sys.argv[1] if len(sys.argv) > 1 else "playlist.m3u8"

    if not Path(playlist_path).exists():
        print(f"{RED}ERROR: File not found — {playlist_path}{RESET}")
        sys.exit(1)

    print(f"{BOLD}IPTV EPG Validator{RESET}")
    print(f"Playlist : {playlist_path}")

    # 1. Parse playlist
    epg_url, channels = parse_playlist(playlist_path)
    print(f"Channels found: {len(channels)}")

    if not epg_url:
        print(f"{RED}ERROR: No x-tvg-url found in playlist header.{RESET}")
        sys.exit(1)

    # Allow overriding EPG URL via second CLI argument
    if len(sys.argv) > 2:
        epg_url = sys.argv[2]
        print(f"EPG URL override: {epg_url}")

    # 2. Fetch EPG
    epg_ids, epg_names = fetch_epg(epg_url)

    # 3. Validate
    result = validate(channels, epg_ids, epg_names)

    # 4. Print report
    print_report(result, epg_ids)

    # 5. Auto-fix case issues
    fixed = auto_fix(playlist_path, result)
    if fixed:
        print(f"{GREEN}Auto-fixed {fixed} tvg-id case issue(s) in {playlist_path}.{RESET}\n")

    # Exit code: 0 = all matched, 1 = some unmatched
    sys.exit(0 if not result["missing"] and not result["no_tvg_id"] else 1)


if __name__ == "__main__":
    main()
