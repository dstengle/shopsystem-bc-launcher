"""Meta-tests: keep the step-definition layer from regrowing a monolith."""
import ast
import re
from pathlib import Path

STEPS_DIR = Path(__file__).parent
CONFTEST = STEPS_DIR.parent / "conftest.py"


def _step_patterns(path):
    """AST-extract (pattern, funcname) for every @given/@when/@then in a module."""
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            name = getattr(dec.func, "id", getattr(dec.func, "attr", ""))
            if name not in ("given", "when", "then") or not dec.args:
                continue
            arg = dec.args[0]
            if isinstance(arg, ast.Call) and arg.args:
                arg = arg.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.append((name, arg.value, node.name))
    return out


def test_conftest_defines_no_steps():
    assert not _step_patterns(CONFTEST), (
        "conftest.py must not define BDD steps; add them to "
        "tests/steps/<domain>.py (auto-registered)."
    )


def test_no_duplicate_step_patterns():
    owners = {}
    dupes = []
    for mod in sorted(STEPS_DIR.glob("*.py")):
        if mod.stem.startswith(("_", "test_")):
            continue
        for kind, pat, func in _step_patterns(mod):
            key = (kind, pat)
            if key in owners:
                dupes.append((key, owners[key], f"{mod.name}:{func}"))
            else:
                owners[key] = f"{mod.name}:{func}"
    assert not dupes, (
        "identical step pattern defined more than once (later registration "
        f"shadows earlier): {dupes}"
    )
