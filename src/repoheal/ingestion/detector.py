"""Ecosystem detection.

Pure function: ``detect(root) -> Ecosystem``. Looks at manifest files,
language extensions, and CI/Docker presence to fingerprint a repo.

Detection is heuristic but deterministic. We never call out to a network
or run code from the repo here.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..core.models import BuildSystem, Ecosystem, Language

# --------------------------------------------------------------------------- #
# Manifest-file → (Language, BuildSystem, optional framework hints)
# --------------------------------------------------------------------------- #

_MANIFEST_TABLE: dict[str, tuple[Language, BuildSystem]] = {
    # Python
    "pyproject.toml": (Language.PYTHON, BuildSystem.PIP),  # refined below
    "setup.py": (Language.PYTHON, BuildSystem.PIP),
    "setup.cfg": (Language.PYTHON, BuildSystem.PIP),
    "Pipfile": (Language.PYTHON, BuildSystem.PIP),
    "requirements.txt": (Language.PYTHON, BuildSystem.PIP),
    "uv.lock": (Language.PYTHON, BuildSystem.UV),
    "poetry.lock": (Language.PYTHON, BuildSystem.POETRY),
    # JS / TS
    "package.json": (Language.JAVASCRIPT, BuildSystem.NPM),  # refined below
    "pnpm-lock.yaml": (Language.JAVASCRIPT, BuildSystem.PNPM),
    "yarn.lock": (Language.JAVASCRIPT, BuildSystem.YARN),
    "tsconfig.json": (Language.TYPESCRIPT, BuildSystem.NPM),
    # Java / Kotlin
    "pom.xml": (Language.JAVA, BuildSystem.MAVEN),
    "build.gradle": (Language.JAVA, BuildSystem.GRADLE),
    "build.gradle.kts": (Language.KOTLIN, BuildSystem.GRADLE),
    "settings.gradle": (Language.JAVA, BuildSystem.GRADLE),
    "settings.gradle.kts": (Language.KOTLIN, BuildSystem.GRADLE),
    # Go
    "go.mod": (Language.GO, BuildSystem.GO_MODULES),
    "go.sum": (Language.GO, BuildSystem.GO_MODULES),
    # Rust
    "Cargo.toml": (Language.RUST, BuildSystem.CARGO),
    "Cargo.lock": (Language.RUST, BuildSystem.CARGO),
    # C / C++
    "CMakeLists.txt": (Language.CPP, BuildSystem.CMAKE),
    # PHP
    "composer.json": (Language.PHP, BuildSystem.COMPOSER),
    "composer.lock": (Language.PHP, BuildSystem.COMPOSER),
    # Ruby
    "Gemfile": (Language.RUBY, BuildSystem.BUNDLER),
    "Gemfile.lock": (Language.RUBY, BuildSystem.BUNDLER),
    # Swift
    "Package.swift": (Language.SWIFT, BuildSystem.SWIFT_PM),
}

# Extension → Language. Used as a tie-breaker / supplemental signal.
_EXTENSION_TABLE: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".java": Language.JAVA,
    ".go": Language.GO,
    ".rs": Language.RUST,
    ".c": Language.C,
    ".h": Language.C,
    ".cc": Language.CPP,
    ".cpp": Language.CPP,
    ".cxx": Language.CPP,
    ".hpp": Language.CPP,
    ".cs": Language.CSHARP,
    ".php": Language.PHP,
    ".kt": Language.KOTLIN,
    ".kts": Language.KOTLIN,
    ".swift": Language.SWIFT,
    ".rb": Language.RUBY,
}

_TEST_FRAMEWORK_FILES: dict[str, str] = {
    "pytest.ini": "pytest",
    "tox.ini": "tox",
    "noxfile.py": "nox",
    "jest.config.js": "jest",
    "jest.config.ts": "jest",
    "vitest.config.ts": "vitest",
    "vitest.config.js": "vitest",
    "phpunit.xml": "phpunit",
    "spec_helper.rb": "rspec",
}

_FRAMEWORK_MARKERS: dict[str, tuple[str, ...]] = {
    "django": ("manage.py", "settings.py"),
    "flask": ("app.py", "wsgi.py"),
    "fastapi": (),  # detected from package.json/pyproject deps in a future pass
    "next": ("next.config.js", "next.config.mjs"),
    "react": (),  # weak signal from a single file
    "rails": ("config/environment.rb",),
    "spring-boot": ("src/main/java/",),
}


class EcosystemDetector:
    """Detect a repository's ecosystem from its on-disk layout."""

    # Tunable: how deep we descend to count source files. Most monorepos
    # have their language signal in the top 4 levels; going deeper is
    # wasteful for the detection step (the walker handles deep traversal
    # for parsing).
    _MAX_SCAN_DEPTH = 4

    def detect(self, root: Path) -> Ecosystem:
        if not root.exists() or not root.is_dir():
            return Ecosystem()

        languages_from_manifests: list[Language] = []
        build_systems: list[BuildSystem] = []
        test_frameworks: list[str] = []
        package_managers: list[str] = []
        frameworks: list[str] = []

        # --- top-level manifest pass ----------------------------------
        for name, (lang, bs) in _MANIFEST_TABLE.items():
            if (root / name).exists():
                languages_from_manifests.append(lang)
                build_systems.append(bs)

        # Refine: pyproject.toml -> poetry vs uv vs pip
        py_proj = root / "pyproject.toml"
        if py_proj.exists():
            content = _safe_read_text(py_proj)
            if "[tool.poetry]" in content:
                build_systems.append(BuildSystem.POETRY)
            if "[tool.uv]" in content or (root / "uv.lock").exists():
                build_systems.append(BuildSystem.UV)

        # Refine: package.json package manager
        if (root / "package.json").exists():
            if (root / "pnpm-lock.yaml").exists():
                package_managers.append("pnpm")
            elif (root / "yarn.lock").exists():
                package_managers.append("yarn")
            else:
                package_managers.append("npm")

        # --- file-extension pass --------------------------------------
        ext_counts = self._count_extensions(root)
        languages_from_files = [
            _EXTENSION_TABLE[ext] for ext, _ in ext_counts.most_common() if ext in _EXTENSION_TABLE
        ]

        # --- frameworks / test frameworks -----------------------------
        for fname, name in _TEST_FRAMEWORK_FILES.items():
            if (root / fname).exists():
                test_frameworks.append(name)

        py_text = _safe_read_text(py_proj) if py_proj.exists() else ""
        if "pytest" in py_text:
            test_frameworks.append("pytest")

        for fw, markers in _FRAMEWORK_MARKERS.items():
            if any((root / m).exists() for m in markers):
                frameworks.append(fw)

        # --- compose --------------------------------------------------
        languages = _ordered_unique(
            list(_dedup(languages_from_manifests)) + list(_dedup(languages_from_files))
        )

        return Ecosystem(
            languages=tuple(languages),
            build_systems=tuple(_dedup(build_systems)),
            frameworks=tuple(_dedup(frameworks)),
            test_frameworks=tuple(_dedup(test_frameworks)),
            package_managers=tuple(_dedup(package_managers)),
            has_dockerfile=(root / "Dockerfile").exists()
            or any(root.glob("Dockerfile.*")),
            has_ci=(root / ".github" / "workflows").is_dir()
            or (root / ".gitlab-ci.yml").exists()
            or (root / "Jenkinsfile").exists()
            or (root / ".circleci" / "config.yml").exists(),
        )

    def _count_extensions(self, root: Path) -> Counter[str]:
        counts: Counter[str] = Counter()
        skip_dirs = {".git", "node_modules", ".venv", "venv", "target", "build", "dist", "__pycache__"}

        def walk(d: Path, depth: int) -> None:
            if depth > self._MAX_SCAN_DEPTH:
                return
            try:
                entries = list(d.iterdir())
            except (OSError, PermissionError):
                return
            for entry in entries:
                if entry.is_dir():
                    if entry.name in skip_dirs or entry.name.startswith("."):
                        continue
                    walk(entry, depth + 1)
                else:
                    suffix = entry.suffix.lower()
                    if suffix in _EXTENSION_TABLE:
                        counts[suffix] += 1

        walk(root, 0)
        return counts


# --- helpers ---------------------------------------------------------------


def _safe_read_text(path: Path, max_bytes: int = 64 * 1024) -> str:
    try:
        with path.open("rb") as f:
            return f.read(max_bytes).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _dedup(seq: list) -> list:
    """Order-preserving dedup."""
    seen: set = set()
    out = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _ordered_unique(items: list[Language]) -> list[Language]:
    return _dedup(items)
