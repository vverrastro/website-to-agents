"""Guards against committing secrets or local artefacts."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)  # noqa: S607


def tracked() -> list[str]:
    out = git("ls-files")
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    return [f for f in out.stdout.splitlines() if (ROOT / f).is_file()]


def test_env_files_are_ignored_but_the_example_is_not() -> None:
    for name in (".env", ".env.local", ".env.production", "services/api/.env"):
        assert git("check-ignore", "-q", name).returncode == 0, f"{name} must be ignored"
    assert git("check-ignore", "-q", ".env.example").returncode != 0, ".env.example must be tracked"


def test_no_env_or_key_files_are_tracked() -> None:
    bad = [
        f for f in tracked() if re.search(r"(^|/)\.env($|\.(?!example$))|\.pem$|\.key$|id_rsa", f)
    ]
    assert bad == []


def test_no_generated_artifacts_are_tracked() -> None:
    bad = [
        f for f in tracked() if re.search(r"(^|/)(node_modules|\.venv|\.next|dist|__pycache__)/", f)
    ]
    assert bad == []


SECRET_PATTERNS = {
    "anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "voyage key": re.compile(r"\bpa-[A-Za-z0-9]{30,}"),
    "private key block": re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "aws access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
}


def test_no_secret_shaped_strings_in_tracked_files() -> None:
    hits: list[str] = []
    for f in tracked():
        if f.endswith((".png", ".ico", ".lock", "package-lock.json")):
            continue
        try:
            text = (ROOT / f).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits += [f"{f}: {name}" for name, rx in SECRET_PATTERNS.items() if rx.search(text)]
    assert hits == []


def test_env_example_contains_only_placeholders() -> None:
    values = {}
    for line in (ROOT / ".env.example").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, rest = line.partition("=")
            values[key.strip()] = rest.split("#")[0].strip()
    for key in ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY"):
        assert values[key] == "", f"{key} must be blank in .env.example"
    assert "change-me" in values["POSTGRES_PASSWORD"]
