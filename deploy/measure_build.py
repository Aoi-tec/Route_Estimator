"""既存の構築処理を使い、cgroup v2とmacOSのメモリ実測を保存する。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import threading
import time

if __package__:
    from . import setup as deployment
else:
    import setup as deployment


def capture(args, timeout=15):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": str(exc)}


def parse_cgroup(raw):
    values = {}
    section = None
    for line in raw.splitlines():
        if line.startswith("@@"):
            section = line[2:]
        elif section and line.strip():
            if section in ("memory.current", "memory.peak", "memory.swap.current", "memory.swap.peak"):
                try:
                    values[section] = int(line)
                except ValueError:
                    pass
            else:
                parts = line.split()
                if len(parts) == 2 and parts[1].isdigit():
                    values.setdefault(section, {})[parts[0]] = int(parts[1])
    return values


class MeasuredRunner(deployment.Runner):
    def __init__(self, root, output, interval):
        super().__init__(root)
        self.output = output
        self.interval = interval
        self.records = []

    def run(self, args, **kwargs):
        args = [str(arg) for arg in args]
        if not kwargs.get("log"):
            return super().run(args, **kwargs)
        # 前処理だけを計測。既存の配信コンテナには変更を加えない。
        name = args[args.index("--name") + 1]
        image_index = next(i for i, arg in enumerate(args) if arg.startswith("ghcr.io/project-osrm/"))
        create = [args[0], "create", *[arg for arg in args[2:image_index + 1] if arg != "--rm"]]
        # 子がOOM終了してもシェルが生き残れば、最終peak/eventsを取得する猶予を作る。
        create += ["sh", "-c", '"$@"; result=$?; sleep 10; exit "$result"', "sh", *args[image_index + 1:]]
        created = super().run(create, capture=True)
        container_id = created.stdout.strip()
        if len(container_id) != 64 or any(c not in "0123456789abcdef" for c in container_id):
            raise deployment.SetupError("計測対象コンテナIDを確認できません")
        stage = next(arg.removeprefix("osrm-") for arg in args[image_index + 1:] if arg.startswith("osrm-"))
        stage_dir = self.output / stage
        stage_dir.mkdir()
        started = time.monotonic()
        record = {"stage": stage, "container_id": container_id, "command": args,
                  "started_at": deployment.now(), "samples": 0, "max_memory_current_bytes": 0,
                  "max_cgroup_memory_peak_bytes": 0, "max_swap_current_bytes": 0,
                  "max_cgroup_swap_peak_bytes": 0}
        stopped = threading.Event()
        files = "memory.current memory.peak memory.swap.current memory.swap.peak memory.events memory.stat cpu.stat"
        shell = ('for f in ' + files + '; do printf "@@%s\\n" "$f"; cat "/sys/fs/cgroup/$f" 2>/dev/null; done; '
                 'printf "@@vm_meminfo\\n"; cat /proc/meminfo')

        def sample():
            result = capture([args[0], "exec", container_id, "sh", "-c", shell])
            values = parse_cgroup(result.get("stdout", ""))
            item = {"at": deployment.now(), "elapsed_s": round(time.monotonic() - started, 3),
                    "cgroup": values, "probe": result}
            for source, target in (("memory.current", "max_memory_current_bytes"),
                                   ("memory.peak", "max_cgroup_memory_peak_bytes"),
                                   ("memory.swap.current", "max_swap_current_bytes"),
                                   ("memory.swap.peak", "max_cgroup_swap_peak_bytes")):
                record[target] = max(record[target], values.get(source, 0))
            if values:
                record["samples"] += 1
                record["last_cgroup"] = values
                record["last_sample_elapsed_s"] = item["elapsed_s"]
            with (stage_dir / "memory.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")

        def monitor():
            count = 0
            while not stopped.wait(self.interval):
                sample()
                if count % max(1, round(30 / self.interval)) == 0:
                    item = {"at": deployment.now(), "vm_stat": capture(["vm_stat"]),
                            "memory_pressure": capture(["memory_pressure"]),
                            "docker_stats": capture([args[0], "stats", "--no-stream", "--format", "{{json .}}"])}
                    with (stage_dir / "host.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(item, ensure_ascii=False) + "\n")
                count += 1

        worker = threading.Thread(target=monitor, daemon=True)
        try:
            worker.start()
            result = super().run([args[0], "start", "--attach", container_id], **dict(kwargs, check=False))
            record["returncode"] = result.returncode
        except KeyboardInterrupt:
            record["interrupted"] = True
            capture([args[0], "stop", "--time", "15", container_id], timeout=25)
            raise
        finally:
            stopped.set()
            worker.join(timeout=50)
            record["finished_at"] = deployment.now()
            record["elapsed_s"] = round(time.monotonic() - started, 3)
            inspection = capture([args[0], "inspect", "--format", "{{json .State}}", container_id])
            record["inspection"] = inspection
            try:
                record["container_state"] = json.loads(inspection.get("stdout", ""))
            except ValueError:
                pass
            deployment.atomic_json(stage_dir / "summary.json", record)
            self.records.append(record)
            capture([args[0], "rm", container_id])
        print("[実測] " + json.dumps(record, ensure_ascii=False), flush=True)
        if kwargs.get("check", True) and result.returncode:
            raise deployment.SetupError(f"{stage}終了{result.returncode}。計測記録: {stage_dir}")
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=deployment.ROOT / "deploy/setup.json")
    parser.add_argument("--build-memory-gib", type=int, default=5)
    parser.add_argument("--serve-memory-gib", type=int, default=3)
    parser.add_argument("--cpus", type=int, default=2)
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    if not 1 <= args.interval <= 60:
        parser.error("interval must be 1..60 seconds")
    output = deployment.ROOT / "state/setup/measurements" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output.mkdir(parents=True)
    config = json.loads(args.settings.read_text(encoding="utf-8-sig"))
    config.update(build_memory_gib=args.build_memory_gib, serve_memory_gib=args.serve_memory_gib, cpus=args.cpus)
    temporary = output / "settings.json"
    deployment.atomic_json(temporary, config)
    runner = MeasuredRunner(deployment.ROOT, output, args.interval)
    tool = deployment.Provisioner(deployment.ROOT, temporary, runner=runner)
    summary = {"started_at": deployment.now(), "measurement_directory": str(output),
               "settings": config, "host_before": capture(["memory_pressure"]),
               "note": "cgroup current includes page cache; peak is kernel-recorded when readable. Sampling can miss final peak if the whole container is killed. macOS host snapshots are not container RSS."}
    print("[計測開始] " + str(output), flush=True)
    code = 2
    try:
        with deployment.operation_lock(tool.state / "operation.lock"):
            ready = tool.build()
            summary["ready"] = ready
        summary["success"] = True
        code = 0
    except KeyboardInterrupt:
        summary["error"] = "interrupted"
        code = 130
    except (deployment.SetupError, OSError, ValueError, KeyError) as exc:
        summary["error"] = str(exc)
        print("[計測停止] " + str(exc), flush=True)
    finally:
        summary.update(finished_at=deployment.now(), stages=runner.records, exit_code=code,
                       host_after=capture(["memory_pressure"]))
        deployment.atomic_json(output / "summary.json", summary)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
