"""
pytest-bdd binding for scenario 70 (lead-eqao, 3rd F3 cycle,
@scenario_hash:3222fe1396f1ff53): the REAL committed CA-validation script the
launch execs must classify the materialized CA by the BEGIN-CERTIFICATE marker
(valid -> accept + clone proceeds; genuinely marker-less -> reject fail-loud),
with NO validation-internal grep-option error misjudging a valid cert.

Companion pin to scenario 69 (@scenario_hash:09f871cf8b99a34b); additive,
retires nothing.  Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_ca_validation_grep_fidelity.feature")
