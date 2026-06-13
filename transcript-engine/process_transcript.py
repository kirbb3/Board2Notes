#!/usr/bin/env python3
"""Transcript engine (Phase 2).

Takes a lecture caption file (.srt — Panopto saves them as .txt sometimes)
and produces a clean, structured transcript:

- parses the cues and their timestamps
- strips verbal filler ("um", "uh", stutters) and caption boilerplate
- merges choppy caption fragments into readable paragraphs
- flags exam-priority moments (★) when the professor says things like
  "this will be on the exam", "make sure you know", "classic midterm problem"
- writes a Markdown file for humans and a JSON file for the later
  fusion stage (timestamps let board snapshots anchor to speech)

Deterministic — no model calls. Usage:
    python3 process_transcript.py lecture.srt -o output/lecture
"""

import argparse
import json
import os
import re
import sys

CUE_TIME_RE = re.compile(
    r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)"
)

BOILERPLATE = [
    re.compile(r"\[Auto-generated transcript[^\]]*\]", re.I),
    re.compile(r"\[(?:music|applause|laughter|inaudible)\]", re.I),
]

# Conservative filler removal: only unambiguous verbal tics.
FILLER_RE = re.compile(
    r"\b(?:um+|uh+|uhm+|erm*|hmm+|ah)\b[,.]?\s*", re.I
)
REPEAT_RE = re.compile(r"\b(\w+)(?:[,.]?\s+\1\b)+", re.I)  # "that that"

# ★ patterns: high-precision only — explicit references to assessment, or
# unmistakable memorization directives. Generic lecturing emphasis
# ("this is important", "make sure you...") fires far too often to be ★.
STAR_PATTERNS = [
    (re.compile(r"\bon the (?:exam|final|midterm|test|quiz)\b", re.I), "exam mention"),
    (re.compile(r"\b(?:exam|final|midterm|test|quiz) (?:question|problem|material)\b", re.I), "exam mention"),
    (re.compile(r"\bwill be (?:on|in) the\b.{0,20}\b(?:exam|final|midterm|test|quiz)\b", re.I), "exam mention"),
    (re.compile(r"\b(?:common|popular|typical|classic) (?:exam|test|midterm|final) \w+", re.I), "exam mention"),
    (re.compile(r"\bfor the (?:exam|final|midterm|test|quiz)\b", re.I), "exam mention"),
    (re.compile(r"\byou should definitely know\b", re.I), "must-know"),
    # "write this down" must be directed at students — professors also say
    # "let me write this down" about their own chalk.
    (re.compile(r"\b(?:memorize|you (?:should |all )?write this down)\b", re.I), "must-know"),
    (re.compile(r"\bclassic (?:problem|question)\b", re.I), "classic problem"),
]

# Softer emphasis signals: recorded in the JSON for the fusion stage (an
# LLM can judge them in context) but not shouted as ★ in the Markdown.
EMPHASIS_PATTERNS = [
    re.compile(r"\bmake sure (?:you|that you)\b", re.I),
    re.compile(r"\byou (?:should|need to|have to|must) (?:know|remember|understand)\b", re.I),
    re.compile(r"\b(?:very |really |extremely )?important\b", re.I),
    re.compile(r"\bremember this\b", re.I),
]

PARAGRAPH_GAP = 4.0     # seconds of silence that starts a new paragraph
PARAGRAPH_MAX = 75.0    # max paragraph length in seconds


def parse_srt(path: str) -> list[dict]:
    """Parse .srt (or .vtt) into [{start, end, text}], in seconds."""
    with open(path, encoding="utf-8-sig") as f:
        content = f.read()

    cues = []
    for block in re.split(r"\n\s*\n", content):
        m = CUE_TIME_RE.search(block)
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(g) for g in m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        # Text = everything after the timestamp line.
        lines = block.splitlines()
        ti = next(i for i, ln in enumerate(lines) if CUE_TIME_RE.search(ln))
        text = " ".join(ln.strip() for ln in lines[ti + 1:] if ln.strip())
        if text:
            cues.append({"start": start, "end": end, "text": text})
    return cues


def clean_text(text: str) -> str:
    for pat in BOILERPLATE:
        text = pat.sub("", text)
    text = FILLER_RE.sub("", text)
    text = REPEAT_RE.sub(r"\1", text)
    text = re.sub(r"\s+", " ", text)
    # Tidy artifacts the filler removal leaves behind: " , " and ",."
    text = re.sub(r"\s+([,.?!])", r"\1", text)
    text = re.sub(r"([,.?!])[,.]+", r"\1", text)
    return text.strip()


def find_stars(text: str) -> list[str]:
    labels = []
    for pat, label in STAR_PATTERNS:
        if pat.search(text) and label not in labels:
            labels.append(label)
    return labels


def find_emphasis(text: str) -> bool:
    return any(pat.search(text) for pat in EMPHASIS_PATTERNS)


def merge_paragraphs(cues: list[dict]) -> list[dict]:
    """Merge cleaned cues into paragraphs by pause and duration."""
    paragraphs = []
    cur = None
    for cue in cues:
        text = clean_text(cue["text"])
        if not text:
            continue
        new_para = (
            cur is None
            or cue["start"] - cur["end"] > PARAGRAPH_GAP
            or cue["end"] - cur["start"] > PARAGRAPH_MAX
        )
        if new_para:
            if cur:
                paragraphs.append(cur)
            cur = {"start": cue["start"], "end": cue["end"], "text": text}
        else:
            cur["text"] += " " + text
            cur["end"] = cue["end"]
    if cur:
        paragraphs.append(cur)

    for p in paragraphs:
        p["stars"] = find_stars(p["text"])
        p["emphasis"] = find_emphasis(p["text"])
    return paragraphs


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def write_markdown(paragraphs: list[dict], path: str, title: str) -> None:
    lines = [f"# {title}", ""]
    n_starred = sum(1 for p in paragraphs if p["stars"])
    lines += [f"*{len(paragraphs)} paragraphs, {n_starred} flagged ★*", ""]
    for p in paragraphs:
        if p["stars"]:
            lines.append(
                f"> **★ EXAM PRIORITY** ({', '.join(p['stars'])}) "
                f"— [{fmt_ts(p['start'])}]"
            )
            lines.append(f"> {p['text']}")
        else:
            lines.append(f"**[{fmt_ts(p['start'])}]** {p['text']}")
        lines.append("")
    with open(path, "w") as f:
        f.write("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("transcript", help=".srt/.vtt/.txt caption file")
    ap.add_argument("-o", "--out", default="output/transcript",
                    help="output basename (writes <out>.md and <out>.json)")
    ap.add_argument("--title", default=None,
                    help="document title (default: from filename)")
    args = ap.parse_args()

    cues = parse_srt(args.transcript)
    if not cues:
        print("error: no caption cues found — is this an SRT/VTT file?",
              file=sys.stderr)
        return 1

    paragraphs = merge_paragraphs(cues)
    title = args.title or os.path.splitext(
        os.path.basename(args.transcript))[0]

    out_base = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_base) or ".", exist_ok=True)
    write_markdown(paragraphs, out_base + ".md", title)
    with open(out_base + ".json", "w") as f:
        json.dump({"title": title, "paragraphs": paragraphs}, f, indent=1)

    n_starred = sum(1 for p in paragraphs if p["stars"])
    dur = fmt_ts(paragraphs[-1]["end"])
    print(f"{len(cues)} cues → {len(paragraphs)} paragraphs "
          f"({dur} of speech), {n_starred} flagged ★")
    print(f"wrote {out_base}.md and {out_base}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
