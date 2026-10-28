import json
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parent / "data" / "ddxplus"

with (DATA_DIR / "release_evidences.json").open(encoding="utf-8") as evidence_file:
    _evidence = json.load(evidence_file)

with (DATA_DIR / "release_conditions.json").open(encoding="utf-8") as conditions_file:
    DDXPLUS_CONDITIONS = json.load(conditions_file)

DDXPLUS_SYMPTOMS = {
    code: item["question_en"]
    for code, item in _evidence.items()
    if not item["is_antecedent"]
}
