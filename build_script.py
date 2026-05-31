from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ORT_DISTRIBUTIONS = (
    "onnxruntime",
    "onnxruntime-directml",
    "onnxruntime-gpu",
)


@dataclass(frozen=True)
class PackageFile:
    source: str
    target: str


@dataclass(frozen=True)
class BackendConfig:
    name: str
    distribution: str
    target_name: str
    providers: tuple[str, ...]


@dataclass(frozen=True)
class FreezeConfig:
    project_name: str
    version: str
    description: str
    script: str
    gui_base: bool
    include_msvcr: bool
    build_root: str
    env_root: str
    common_packages: tuple[str, ...]
    include_files: tuple[str, ...]
    create_directories: tuple[str, ...]
    zip_exclude_packages: tuple[str, ...]
    package_files: tuple[PackageFile, ...]
    backends: dict[str, BackendConfig]


def _require_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"Expected a non-empty string for {field_name}.")
    return value


def _as_tuple_of_str(values: Any, field_name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, list):
        raise SystemExit(f"Expected {field_name} to be a TOML array.")
    result: list[str] = []
    for index, item in enumerate(values):
        result.append(_require_str(item, f"{field_name}[{index}]"))
    return tuple(result)


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _command_text(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def _run_command(command: list[str], *, cwd: Path, dry_run: bool) -> None:
    print(_command_text(command))
    if dry_run:
        return
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except FileNotFoundError as exc:
        raise SystemExit(f"Command not found: {command[0]}") from exc


def _remove_tree(path: Path, *, dry_run: bool) -> None:
    if not path.exists():
        return
    print(f"remove {path}")
    if dry_run:
        return
    shutil.rmtree(path)


def _find_uv() -> str:
    uv_executable = shutil.which("uv")
    if uv_executable is None:
        raise SystemExit("uv is required to create isolated cx_Freeze build environments.")
    return uv_executable


def _env_python(env_dir: Path) -> Path:
    if sys.platform == "win32":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def load_freeze_config(pyproject_path: Path) -> FreezeConfig:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    try:
        project = data["project"]
        freeze = data["tool"]["wuwa_inventory_kamera"]["freeze"]
    except KeyError as exc:
        raise SystemExit(
            "Missing [tool.wuwa_inventory_kamera.freeze] configuration in pyproject.toml."
        ) from exc

    backends_table = freeze.get("backends", {})
    if not isinstance(backends_table, dict) or not backends_table:
        raise SystemExit("No freeze backends are configured in pyproject.toml.")

    package_files: list[PackageFile] = []
    raw_package_files = freeze.get("package_files", [])
    if not isinstance(raw_package_files, list):
        raise SystemExit("Expected package_files to be a TOML array.")
    for index, item in enumerate(raw_package_files):
        if not isinstance(item, dict):
            raise SystemExit(f"Expected package_files[{index}] to be an inline table.")
        package_files.append(
            PackageFile(
                source=_require_str(item.get("source"), f"package_files[{index}].source"),
                target=_require_str(item.get("target"), f"package_files[{index}].target"),
            )
        )

    backends: dict[str, BackendConfig] = {}
    for backend_name, item in backends_table.items():
        if not isinstance(item, dict):
            raise SystemExit(f"Expected backend {backend_name!r} to be a TOML table.")
        providers = _as_tuple_of_str(item.get("providers"), f"backends.{backend_name}.providers")
        if not providers:
            raise SystemExit(f"Backend {backend_name!r} must define at least one provider.")
        backends[backend_name] = BackendConfig(
            name=backend_name,
            distribution=_require_str(
                item.get("distribution"),
                f"backends.{backend_name}.distribution",
            ),
            target_name=_require_str(
                item.get("target_name"),
                f"backends.{backend_name}.target_name",
            ),
            providers=providers,
        )

    return FreezeConfig(
        project_name=_require_str(project.get("name"), "project.name"),
        version=_require_str(project.get("version"), "project.version"),
        description=_require_str(project.get("description"), "project.description"),
        script=_require_str(freeze.get("script"), "tool.wuwa_inventory_kamera.freeze.script"),
        gui_base=bool(freeze.get("gui_base", True)),
        include_msvcr=bool(freeze.get("include_msvcr", sys.platform == "win32")),
        build_root=_require_str(freeze.get("build_root"), "tool.wuwa_inventory_kamera.freeze.build_root"),
        env_root=_require_str(freeze.get("env_root"), "tool.wuwa_inventory_kamera.freeze.env_root"),
        common_packages=_as_tuple_of_str(
            freeze.get("common_packages"),
            "tool.wuwa_inventory_kamera.freeze.common_packages",
        ),
        include_files=_as_tuple_of_str(
            freeze.get("include_files"),
            "tool.wuwa_inventory_kamera.freeze.include_files",
        ),
        create_directories=_as_tuple_of_str(
            freeze.get("create_directories"),
            "tool.wuwa_inventory_kamera.freeze.create_directories",
        ),
        zip_exclude_packages=_as_tuple_of_str(
            freeze.get("zip_exclude_packages"),
            "tool.wuwa_inventory_kamera.freeze.zip_exclude_packages",
        ),
        package_files=tuple(package_files),
        backends=backends,
    )


def build_parser(config: FreezeConfig) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build cx_Freeze executables for configured ONNX Runtime backends.",
    )
    parser.add_argument(
        "backend",
        choices=[*config.backends.keys(), "all"],
        help="Backend variant to build.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete the selected backend build output before rebuilding.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned commands and resolved config without running them.",
    )
    parser.add_argument(
        "--use-current-env",
        action="store_true",
        help="Freeze with the current Python environment instead of creating an isolated backend env.",
    )
    parser.add_argument(
        "--skip-provider-check",
        action="store_true",
        help="Skip the ONNX Runtime provider validation before freezing.",
    )
    parser.add_argument(
        "--output-root",
        help="Override the build output root from pyproject.toml.",
    )
    parser.add_argument(
        "--env-root",
        help="Override the isolated environment root from pyproject.toml.",
    )
    parser.add_argument("--inner", action="store_true", help=argparse.SUPPRESS)
    return parser


def _prepare_backend_env(
    root: Path,
    config: FreezeConfig,
    backend: BackendConfig,
    *,
    env_root_override: str | None,
    clean: bool,
    dry_run: bool,
) -> Path:
    uv_executable = _find_uv()
    env_root = _resolve_path(root, env_root_override or config.env_root)
    env_dir = env_root / backend.name
    python_exe = _env_python(env_dir)

    if clean:
        _remove_tree(env_dir, dry_run=dry_run)

    if not python_exe.exists():
        _run_command(
            [uv_executable, "venv", "--python", sys.executable, str(env_dir)],
            cwd=root,
            dry_run=dry_run,
        )

    _run_command(
        [uv_executable, "pip", "install", "--python", str(python_exe), "-e", ".[app,build]"],
        cwd=root,
        dry_run=dry_run,
    )
    _run_command(
        [uv_executable, "pip", "uninstall", "--python", str(python_exe), *ORT_DISTRIBUTIONS],
        cwd=root,
        dry_run=dry_run,
    )
    _run_command(
        [uv_executable, "pip", "install", "--python", str(python_exe), backend.distribution],
        cwd=root,
        dry_run=dry_run,
    )
    return python_exe


def _invoke_inner_build(
    root: Path,
    python_exe: Path,
    backend_name: str,
    *,
    output_root_override: str | None,
    clean: bool,
    dry_run: bool,
    skip_provider_check: bool,
) -> None:
    command = [str(python_exe), str(root / "build_script.py"), backend_name, "--inner"]
    if output_root_override:
        command.extend(["--output-root", output_root_override])
    if clean:
        command.append("--clean")
    if dry_run:
        command.append("--dry-run")
    if skip_provider_check:
        command.append("--skip-provider-check")
    _run_command(command, cwd=root, dry_run=dry_run)


def _validate_provider(backend: BackendConfig, *, skip_provider_check: bool) -> list[str]:
    if skip_provider_check:
        return []

    import onnxruntime as ort

    providers = list(ort.get_available_providers())
    expected_provider = backend.providers[0]
    if expected_provider not in providers:
        raise SystemExit(
            f"Backend {backend.name!r} requires provider {expected_provider!r}, "
            f"but the active onnxruntime exposes {providers}. "
            f"Requested distribution: {backend.distribution!r}."
        )
    return providers


def _build_include_files(root: Path, config: FreezeConfig) -> list[tuple[str, str]]:
    include_files: list[tuple[str, str]] = []

    for rel_path in config.include_files:
        source = _resolve_path(root, rel_path)
        if not source.exists():
            raise SystemExit(f"Configured include_files entry does not exist: {source}")
        include_files.append((str(source), rel_path.replace("\\", "/")))

    for package_file in config.package_files:
        source = _resolve_path(root, package_file.source)
        if not source.exists():
            raise SystemExit(f"Configured package file does not exist: {source}")
        include_files.append((str(source), package_file.target.replace("\\", "/")))

    return include_files


def _collect_windows_ucrt_include_files(existing_targets: set[str]) -> list[tuple[str, str]]:
    """Collect UCRT DLLs that are sometimes missing from older/minimal Windows installs.

    cx_Freeze's include_msvcr flag covers VC runtime DLLs but does not always place the
    api-ms-win-crt-* forwarder DLLs next to the executable. Bundling them from the host
    (preferring System32, then System32/downlevel) avoids runtime launch failures.
    """

    if sys.platform != "win32":
        return []

    windows_dir = Path(os.environ.get("WINDIR", "C:/Windows"))
    system32 = windows_dir / "System32"
    downlevel = system32 / "downlevel"

    patterns = ("api-ms-win-crt-*.dll", "ucrtbase.dll")
    include_files: list[tuple[str, str]] = []
    seen_targets = set(existing_targets)

    for pattern in patterns:
        for filename in sorted({p.name for root in (system32, downlevel) for p in root.glob(pattern)}):
            target = filename.replace("\\", "/")
            target_key = target.lower()
            if target_key in seen_targets:
                continue

            source_path: Path | None = None
            for root in (system32, downlevel):
                candidate = root / filename
                if candidate.exists():
                    source_path = candidate
                    break

            if source_path is None:
                continue

            include_files.append((str(source_path), target))
            seen_targets.add(target_key)

    return include_files


def _copy_runtime_files(runtime_files: list[tuple[str, str]], output_dir: Path) -> None:
    for source, target in runtime_files:
        destination = output_dir / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _collect_windows_python_runtime_files(existing_targets: set[str]) -> list[tuple[str, str]]:
    if sys.platform != "win32":
        return []

    runtime_names = (
        "python3.dll",
        f"python{sys.version_info.major}{sys.version_info.minor}.dll",
    )

    search_roots = [
        Path(sys.base_prefix),
        Path(sys.base_exec_prefix),
        Path(sys.executable).resolve().parent,
    ]

    include_files: list[tuple[str, str]] = []
    seen_targets = set(existing_targets)

    for runtime_name in runtime_names:
        target_key = runtime_name.lower()
        if target_key in seen_targets:
            continue

        source_path: Path | None = None
        for root in search_roots:
            candidate = root / runtime_name
            if candidate.exists():
                source_path = candidate
                break

        if source_path is None:
            continue

        include_files.append((str(source_path), runtime_name))
        seen_targets.add(target_key)

    return include_files


def _inner_build(
    root: Path,
    config: FreezeConfig,
    backend: BackendConfig,
    *,
    output_root_override: str | None,
    clean: bool,
    dry_run: bool,
    skip_provider_check: bool,
) -> int:
    output_root = _resolve_path(root, output_root_override or config.build_root)
    output_dir = output_root / backend.name

    if clean:
        _remove_tree(output_dir, dry_run=dry_run)

    providers = _validate_provider(backend, skip_provider_check=skip_provider_check)
    include_files = _build_include_files(root, config)
    runtime_files: list[tuple[str, str]] = []
    if config.include_msvcr:
        runtime_files = _collect_windows_ucrt_include_files({target.lower() for _, target in include_files})
        include_files.extend(runtime_files)
    python_runtime_files = _collect_windows_python_runtime_files(
        {target.lower() for _, target in include_files}
    )
    runtime_files.extend(python_runtime_files)
    include_files.extend(python_runtime_files)
    build_options = {
        "build_exe": str(output_dir),
        "include_files": include_files,
        "include_msvcr": config.include_msvcr,
        "packages": sorted(set(config.common_packages)),
        "zip_exclude_packages": sorted(set(config.zip_exclude_packages)),
    }

    print(f"backend={backend.name}")
    print(f"distribution={backend.distribution}")
    print(f"output_dir={output_dir}")
    if providers:
        print(f"providers={providers}")
    else:
        print("providers=<skipped>")

    if dry_run:
        return 0

    from cx_Freeze import Executable, setup

    base = "gui" if config.gui_base and sys.platform == "win32" else None
    executable = Executable(
        script=str(_resolve_path(root, config.script)),
        target_name=backend.target_name,
        base=base,
    )
    setup(
        name=f"{config.project_name}-{backend.name}",
        version=config.version,
        description=f"{config.description} ({backend.name} backend)",
        options={"build_exe": build_options},
        executables=[executable],
        script_args=["build_exe"],
    )

    if runtime_files:
        _copy_runtime_files(runtime_files, output_dir)

    for directory in config.create_directories:
        (output_dir / directory).mkdir(parents=True, exist_ok=True)

    return 0


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent
    config = load_freeze_config(root / "pyproject.toml")
    parser = build_parser(config)
    args = parser.parse_args(argv)

    if args.inner:
        if args.backend == "all":
            raise SystemExit("--inner can only build one backend at a time.")
        return _inner_build(
            root,
            config,
            config.backends[args.backend],
            output_root_override=args.output_root,
            clean=args.clean,
            dry_run=args.dry_run,
            skip_provider_check=args.skip_provider_check,
        )

    backend_names = list(config.backends) if args.backend == "all" else [args.backend]

    for backend_name in backend_names:
        backend = config.backends[backend_name]
        print(f"==> {backend.name}")
        if args.use_current_env:
            python_exe = Path(sys.executable)
        else:
            python_exe = _prepare_backend_env(
                root,
                config,
                backend,
                env_root_override=args.env_root,
                clean=args.clean,
                dry_run=args.dry_run,
            )
        _invoke_inner_build(
            root,
            python_exe,
            backend.name,
            output_root_override=args.output_root,
            clean=args.clean,
            dry_run=args.dry_run,
            skip_provider_check=args.skip_provider_check,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())