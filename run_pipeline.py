#!/usr/bin/env python3
"""One-command wrapper for the Lecture-to-Notes Synthesizer.

Runs the whole pipeline in order, video + captions in, finished study-guide
PDF out:

  1. board-extractor/extract_boards.py     video      -> boards/<name>/*.png
  2. transcript-engine/process_transcript  captions   -> output/<name>.json
  3. latex-converter/convert_lecture.py     boards     -> output/<name>.fragments.json
  4. latex-converter/fuse_lecture.py        json+frags -> output/<name>-fused.bodies.json
  5. latex-converter/finish_lecture.py      bodies     -> output/<name>-final.pdf

Stages 1-4 run free on local Ollama; stage 5 makes one Claude CLI pass.
Every stage is resumable, so re-running after an interruption continues
where it left off.

Examples:
  # full run from a video + captions
  python3 run_pipeline.py --name mylecture --title "My Lecture" \
      --video lecture.mp4 --captions lecture.srt

  # boards already extracted (skip stage 1)
  python3 run_pipeline.py --name mylecture --title "My Lecture" \
      --boards boards/mylecture --captions lecture.srt

On Windows, invoke with `py -3.12 run_pipeline.py ...` from the repo root.
"""

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def extractor_python(override: str | None) -> str:
    """Python interpreter that has OpenCV (for board extraction). Prefer a
    project-local board-extractor/.venv if present, else the current one."""
    if override:
        return override
    for cand in (
        os.path.join(ROOT, "board-extractor", ".venv", "Scripts", "python.exe"),
        os.path.join(ROOT, "board-extractor", ".venv", "bin", "python"),
    ):
        if os.path.exists(cand):
            return cand
    return sys.executable


def run(label: str, cmd: list[str]) -> None:
    print(f"\n{'='*68}\n>> {label}\n   {' '.join(cmd)}\n{'='*68}", flush=True)
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"\nstage failed: {label} (exit {r.returncode}). "
              f"Fix the issue and re-run — completed stages are cached.",
              file=sys.stderr)
        sys.exit(r.returncode)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True,
                    help="short slug used for all output filenames")
    ap.add_argument("--title", required=True, help="document title")
    ap.add_argument("--captions", required=True,
                    help="caption file (.srt/.vtt/.txt)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", help="lecture video — runs board extraction")
    src.add_argument("--boards",
                     help="existing folder of board-*.png (skip extraction)")
    ap.add_argument("--board-model", default="qwen2.5vl:7b",
                    help="Ollama vision model for board transcription")
    ap.add_argument("--fuse-model", default="gemma3:12b",
                    help="Ollama text model for fusion")
    ap.add_argument("--finish-model", default=None,
                    help="claude model alias (default: the CLI's default)")
    ap.add_argument("--ollama-host", default="http://localhost:11434")
    ap.add_argument("--extractor-python", default=None,
                    help="python interpreter with OpenCV for board extraction")
    args = ap.parse_args()

    py = sys.executable
    os.makedirs(os.path.join(ROOT, "output"), exist_ok=True)
    out_base = os.path.join(ROOT, "output", args.name)
    boards_dir = args.boards or os.path.join(ROOT, "boards", args.name)
    conv = os.path.join(ROOT, "latex-converter")

    # 1. board snapshots (skipped when --boards is given)
    if args.video:
        run("1/5  extract board snapshots",
            [extractor_python(args.extractor_python),
             os.path.join(ROOT, "board-extractor", "extract_boards.py"),
             args.video, "-o", boards_dir])
    else:
        print(f"using existing boards in {boards_dir} (skipping extraction)")

    # 2. transcript -> structured JSON
    run("2/5  process transcript",
        [py, os.path.join(ROOT, "transcript-engine", "process_transcript.py"),
         args.captions, "-o", out_base, "--title", args.title])

    # 3. boards -> LaTeX fragments  (local vision model)
    run("3/5  transcribe boards",
        [py, os.path.join(conv, "convert_lecture.py"), boards_dir,
         "-o", out_base, "--model", args.board_model,
         "--ollama-host", args.ollama_host, "--transcribe-only"])

    # 4. fuse transcript + fragments -> rough draft  (local text model)
    fused = out_base + "-fused"
    run("4/5  fuse transcript + boards",
        [py, os.path.join(conv, "fuse_lecture.py"),
         out_base + ".json", out_base + ".fragments.json",
         "-o", fused, "--model", args.fuse_model,
         "--ollama-host", args.ollama_host])

    # 5. polish into the final PDF  (one Claude pass)
    finish = [py, os.path.join(conv, "finish_lecture.py"),
              fused + ".bodies.json", "-o", out_base + "-final",
              "--title", args.title]
    if args.finish_model:
        finish += ["--model", args.finish_model]
    run("5/5  polish with claude", finish)

    print(f"\nDone -> {out_base}-final.pdf")
    return 0


if __name__ == "__main__":
    sys.exit(main())
