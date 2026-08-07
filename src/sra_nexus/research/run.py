"""Minimal command-line entry point for offline historical SRA research."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sra_nexus.research.experiment import ResearchExperimentSpec, ResearchOutputConfig
from sra_nexus.research.historical_runner import HistoricalResearchRunner


def load_experiment(path: Path) -> ResearchExperimentSpec:
    """Load JSON and resolve local file/output paths relative to the spec file."""
    resolved = path.expanduser().resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("experiment JSON root must be an object")
    _resolve_local_paths(payload, resolved.parent)
    return ResearchExperimentSpec.model_validate(payload)


def main(argv: list[str] | None = None) -> int:
    """Inspect or execute one experiment and return nonzero on any failure."""
    parser = argparse.ArgumentParser(description="Run frozen historical SRA research")
    parser.add_argument("--experiment", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--output-root",
        type=Path,
        help="override only the artifact location; it does not change ExperimentHash",
    )
    arguments = parser.parse_args(argv)
    try:
        spec = load_experiment(arguments.experiment)
        if arguments.output_root is not None:
            spec = spec.model_copy(
                update={
                    "output": ResearchOutputConfig(
                        root_directory=str(arguments.output_root.expanduser().resolve()),
                        reuse_identical_completed_run=(spec.output.reuse_identical_completed_run),
                    )
                }
            )
        runner = HistoricalResearchRunner(spec)
        if arguments.dry_run:
            print(
                json.dumps(
                    runner.dry_run().model_dump(mode="json"),
                    sort_keys=True,
                    indent=2,
                )
            )
        else:
            artifacts = runner.run()
            print(artifacts.output_directory)
            print(
                f"processed {artifacts.report.events_processed} events and "
                f"{artifacts.report.observations_generated} observations in "
                f"{artifacts.elapsed_processing_seconds:.6f}s",
                file=sys.stderr,
            )
    except (OSError, ValueError, ValidationError) as error:
        print(f"historical research failed: {error}", file=sys.stderr)
        return 1
    return 0


def _resolve_local_paths(payload: dict[str, Any], parent: Path) -> None:
    sources = payload.get("sources")
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, dict):
                continue
            adapter = source.get("adapter")
            if not isinstance(adapter, dict):
                continue
            paths = adapter.get("source_paths")
            if isinstance(paths, list):
                adapter["source_paths"] = [
                    str(_relative_path(parent, value)) for value in paths if isinstance(value, str)
                ]
    output = payload.get("output")
    if isinstance(output, dict) and isinstance(output.get("root_directory"), str):
        output["root_directory"] = str(_relative_path(parent, output["root_directory"]))


def _relative_path(parent: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (parent / path).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
