"""Command-line entry points: run, replay, synth, evaluate, check-config."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import ServiceConfig, load_config, redact_url
from .logutil import setup_logging

log = logging.getLogger("anpr_service")


def _parse_videos(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--video expects CAMERA_ID=path, got {item!r}")
        cid, path = item.split("=", 1)
        if not Path(path).exists():
            raise SystemExit(f"video not found: {path}")
        out[cid.strip()] = path
    return out


def cmd_run(args: argparse.Namespace) -> int:
    from .service import Supervisor

    cfg = load_config(args.config)
    return Supervisor(cfg, log_level=args.log_level).run()


def cmd_replay(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if args.events_jsonl:
        cfg.emitter.events_jsonl = args.events_jsonl
    if args.recognizer:
        cfg.recognizer.kind = args.recognizer
    from .runner import resolve_videos

    videos = resolve_videos(cfg, _parse_videos(args.video))
    if args.inline:
        from .runner import InlineReplayRunner
        from .service import build_sink

        sink, _backend = build_sink(cfg)
        if sink is None:
            log.warning("no backend.url / events_jsonl configured: events stay in %s", cfg.outbox_path)
        res = InlineReplayRunner(cfg, videos, sink=sink).run()
        for ev in res.events:
            print(json.dumps(ev))
        log.info("replay done in %.1fs: %d events, frames=%s, aggregation=%s, emit latency=%s",
                 res.wall_s, len(res.events), res.frames, res.aggregation, res.emit_latency_ms)
        log.info("per-stage timings: %s", json.dumps(res.timings))
        return 0
    from .service import Supervisor

    return Supervisor(cfg, replay_videos=videos, realtime=args.realtime, loop=args.loop,
                      log_level=args.log_level, drain_timeout_s=args.drain_timeout).run()


def cmd_synth(args: argparse.Namespace) -> int:
    from .synth import generate

    width, height = (2560, 1440) if args.full_res else (args.width, args.height)
    cfg_path = generate(args.out, gates=args.gates, width=width, height=height, fps=args.fps,
                        compact=args.compact, overview=not args.no_overview, ext=args.ext, seed=args.seed,
                        wrong_way=args.wrong_way)
    print(f"synthetic clips + ground_truth.json written under {Path(args.out).resolve()}")
    print(f"replay with:  python -m anpr_service replay --config {cfg_path}")
    print(f"evaluate:     python -m anpr_service evaluate --labels {Path(args.out).resolve()}")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from .evaluate import evaluate, format_report

    base = load_config(args.config) if args.config else None
    if args.recognizer:
        base = base or ServiceConfig()
        base.recognizer.kind = args.recognizer
    report = evaluate(args.labels, base)
    print(format_report(report))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nfull report: {args.out}")
    return 0


def cmd_check_config(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    for g in cfg.gates:
        print(f"gate {g.id} ({g.name}) direction={g.direction.value}")
        for c in g.cameras:
            src = redact_url(c.rtsp_url or c.gstreamer_pipeline or c.replay_file) or "-"
            print(f"  {c.id:<8} {c.role.value:<8} side={c.effective_side.value:<8} span={c.effective_gate_span} src={src}")
    print(f"recognizer={cfg.recognizer.kind} backend={cfg.backend.url or '-'} image_root={cfg.image_root}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="anpr_service", description="ANPR edge service")
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--log-json", action="store_true", help="JSON log lines")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run live (RTSP) with one worker process per camera")
    r.add_argument("--config", required=True)
    r.set_defaults(func=cmd_run)

    rp = sub.add_parser("replay", help="process recorded video files instead of RTSP")
    rp.add_argument("--config", required=True)
    rp.add_argument("--video", action="append", metavar="CAMERA_ID=PATH",
                    help="video per camera (repeatable); default: replay_file from the config")
    rp.add_argument("--realtime", action="store_true", help="pace frames at the video frame rate")
    rp.add_argument("--loop", action="store_true", help="loop the videos forever")
    rp.add_argument("--inline", action="store_true", help="single process, deterministic (prints events)")
    rp.add_argument("--recognizer", choices=["classical", "onnx", "commercial"])
    rp.add_argument("--events-jsonl", help="also append delivered events to this JSONL file")
    rp.add_argument("--drain-timeout", type=float, default=30.0)
    rp.set_defaults(func=cmd_replay)

    s = sub.add_parser("synth", help="generate synthetic replay videos with ground truth")
    s.add_argument("--out", default="demo")
    s.add_argument("--gates", nargs="+", default=["G1", "G2"])
    s.add_argument("--width", type=int, default=1280)
    s.add_argument("--height", type=int, default=720)
    s.add_argument("--full-res", action="store_true", help="2560x1440 like the 4 MP ANPR cameras")
    s.add_argument("--fps", type=float, default=25.0)
    s.add_argument("--compact", action="store_true", help="shorter scene (6 vehicles)")
    s.add_argument("--no-overview", action="store_true")
    s.add_argument("--ext", choices=[".mp4", ".avi"], default=".mp4")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--wrong-way", action="store_true", help="add a bike riding the wrong way at the end")
    s.set_defaults(func=cmd_synth)

    e = sub.add_parser("evaluate", help="accuracy / read-rate report over labelled clips")
    e.add_argument("--labels", required=True, help="directory containing ground_truth.json label sets")
    e.add_argument("--config", help="base service config (recogniser, pipeline settings)")
    e.add_argument("--recognizer", choices=["classical", "onnx", "commercial"])
    e.add_argument("--out", help="write the full JSON report here")
    e.set_defaults(func=cmd_evaluate)

    c = sub.add_parser("check-config", help="validate and print a config")
    c.add_argument("--config", required=True)
    c.set_defaults(func=cmd_check_config)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level, args.log_json)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
