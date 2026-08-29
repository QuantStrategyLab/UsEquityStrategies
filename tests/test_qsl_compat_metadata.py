import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QPK_REVISION = "c9ce15d6db26d75a6fb7285e7288ab4fb83fadd3"
QPK_URL = (
    "quant-platform-kit @ git+https://github.com/QuantStrategyLab/"
    f"QuantPlatformKit.git@{QPK_REVISION}"
)


def test_qsl_compat_metadata_exists_and_bundle() -> None:
    qsl_path = ROOT / "qsl.toml"
    assert qsl_path.exists(), "qsl.toml missing"
    with qsl_path.open("rb") as f:
        data = tomllib.load(f)

    assert data.get("compat", {}).get("bundle") == "2026.08.0", "compat.bundle mismatch"


def test_qpk_pin_lock_and_ci_are_dependency_enabled() -> None:
    with (ROOT / "pyproject.toml").open("rb") as file:
        pyproject = tomllib.load(file)
    with (ROOT / "qsl.toml").open("rb") as file:
        qsl = tomllib.load(file)
    with (ROOT / "uv.lock").open("rb") as file:
        lock = tomllib.load(file)

    assert QPK_URL in pyproject["project"]["dependencies"]
    assert QPK_URL in qsl["compat"]["requires"]

    packages = {package["name"]: package for package in lock["package"]}
    locked_qpk = packages["quant-platform-kit"]
    locked_strategy = packages["us-equity-strategies"]
    assert locked_qpk["source"]["git"] == (
        "https://github.com/QuantStrategyLab/QuantPlatformKit.git"
        f"?rev={QPK_REVISION}#{QPK_REVISION}"
    )
    locked_requirement = next(
        dependency
        for dependency in locked_strategy["metadata"]["requires-dist"]
        if dependency["name"] == "quant-platform-kit"
    )
    assert locked_requirement["git"] == (
        "https://github.com/QuantStrategyLab/QuantPlatformKit.git"
        f"?rev={QPK_REVISION}"
    )

    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "--no-deps" not in ci
    assert "python -m pip install -e ." in ci
    assert "python -m pip check" in ci
