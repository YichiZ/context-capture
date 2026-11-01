#!/usr/bin/env python3
"""Record Claude Code's /v1/messages request bodies and summarize them.

mitmproxy loads this file as an addon. Run it directly for the commands:
  python3 capture.py proxy | summary | tools FILE
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

CAPTURES = Path(__file__).resolve().parent / "captures"
UPSTREAM = "https://api.anthropic.com"
HOST = "127.0.0.1"
PORT = "8080"
ESTIMATE_NOTE = "Tokens are estimates (characters / 4)"
log = logging.getLogger(__name__)


# mitmproxy hooks


def request(flow):
    req = flow.request
    upstream_key = os.environ.get("UPSTREAM_API_KEY")
    if upstream_key:
        req.headers["x-api-key"] = upstream_key  # Docker: the agent holds no real key
    if req.method != "POST" or req.path.split("?")[0] != "/v1/messages":
        return
    try:
        body = json.loads(req.content)  # .content is already decompressed
    except ValueError:
        log.warning("capture skipped: body was not decodable JSON")
        return
    _save(body)


def responseheaders(flow):
    flow.response.stream = True  # pass tokens through live instead of buffering


def _save(body):
    CAPTURES.mkdir(mode=0o700, exist_ok=True)
    numbers = [int(p.stem) for p in CAPTURES.glob("*.json") if p.stem.isdigit()]
    path = CAPTURES / f"{max(numbers, default=0) + 1:04d}.json"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(body, f, indent=2)


# Summaries


def _tokens(value):
    if value is None:
        return 0
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) // 4


def _load(path):
    try:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, RecursionError):
        raise ValueError("not valid JSON") from None
    if not isinstance(body, dict):
        raise ValueError("not a JSON object")
    for key in ("tools", "messages"):
        if not isinstance(body.get(key, []), list):
            raise ValueError(f"{key} is not a list")
    return body


def summarize_capture(path):
    body = _load(path)
    return {
        "file": Path(path).name,
        "model": body.get("model", "?"),
        "messages": len(body.get("messages", [])),
        "tools": len(body.get("tools", [])),
        "system_tokens": _tokens(body.get("system")),
        "tools_tokens": _tokens(body.get("tools")),
        "messages_tokens": _tokens(body.get("messages")),
    }


def _tool_name(tool):
    name = tool.get("name") if isinstance(tool, dict) else None
    return name if isinstance(name, str) and name else "?"


def tool_sizes(path):
    sizes = [(_tool_name(t), _tokens(t)) for t in _load(path).get("tools", [])]
    return sorted(sizes, key=lambda item: item[1], reverse=True)


# Commands


def _print_summary(directory):
    paths = sorted(Path(directory).glob("*.json"))
    if not paths:
        print(f"No captures in {directory}")
        return 0
    print(ESTIMATE_NOTE + "\n")
    print(f"{'file':<12}{'model':<28}{'msgs':>6}{'tools':>7}{'~system':>9}{'~tools':>9}{'~messages':>11}")
    for path in paths:
        try:
            r = summarize_capture(path)
        except (ValueError, OSError) as error:
            reason = error if isinstance(error, ValueError) else "unreadable"
            print(f"{path.name:<12}ERROR: {reason}")
            continue
        print(
            f"{r['file']:<12}{str(r['model']):<28}{r['messages']:>6}{r['tools']:>7}"
            f"{r['system_tokens']:>9}{r['tools_tokens']:>9}{r['messages_tokens']:>11}"
        )
    return 0


def _print_tools(path):
    try:
        sizes = tool_sizes(path)
    except OSError:
        print(f"error: cannot read {path}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"error: {path}: {error}", file=sys.stderr)
        return 1
    print(f"Tool sizes for {Path(path).name}. {ESTIMATE_NOTE}\n")
    print(f"{'tool':<40}{'~tokens':>8}")
    for name, tokens in sizes:
        print(f"{name:<40}{tokens:>8}")
    return 0


def _run_proxy():
    command = [
        "mitmdump",
        "--mode", f"reverse:{UPSTREAM}",
        "--listen-host", HOST,
        "--listen-port", PORT,
        "-s", str(Path(__file__).resolve()),
    ]
    try:
        os.execvp(command[0], command)
    except FileNotFoundError:
        print("error: mitmdump not found. Run: uv tool install mitmproxy", file=sys.stderr)
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dir", type=Path, default=CAPTURES, help="captures folder")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("proxy", help=f"start the proxy on {HOST}:{PORT}")
    commands.add_parser("summary", help="one row per capture")
    commands.add_parser("tools", help="per-tool sizes for one capture").add_argument("file")
    args = parser.parse_args(argv)

    if args.command == "proxy":
        return _run_proxy()
    if args.command == "summary":
        return _print_summary(args.dir)
    return _print_tools(args.dir / args.file)


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="backslashreplace")  # lone surrogates in captures
    sys.exit(main())
