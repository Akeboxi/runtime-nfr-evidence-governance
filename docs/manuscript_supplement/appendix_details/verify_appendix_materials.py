"""Verify migrated appendix records with the Python standard library."""
import csv
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'MANIFEST.json').read_text(encoding='utf-8'))
bad = [p for p, h in manifest['files'].items()
       if not (root / p).is_file() or hashlib.sha256((root / p).read_bytes()).hexdigest() != h]
assert not bad, bad
def rows(name):
    with (root / name).open(encoding='utf-8', newline='') as f:
        return list(csv.reader(f))[1:]
expected = {'evaluation_freeze_timeline.csv': 7, 'questionnaire_mapping_full.csv': 5,
            'formative_repair_cards.csv': 3, 'blinded_evaluation_metrics.csv': 7,
            'interface_conformance_cases.csv': 14, 'appendix_numbering.csv': 14}
for name, count in expected.items():
    assert len(rows(name)) == count, name
assert sum(int(row[1]) for row in rows('formative_repair_cards.csv')) == 24
assert '58/96' in rows('blinded_evaluation_metrics.csv')[0][1]
assert all(row[3] == '是' for row in rows('interface_conformance_cases.csv'))
mapping = {row[0]: row[1] for row in rows('appendix_numbering.csv')}
assert [mapping['表A'+str(i)] for i in range(7,14)] == ['表A'+str(i) for i in range(3,10)]
print(json.dumps({'files_checked': len(manifest['files']), 'mismatches': bad,
                  'record_counts': expected, 'preserved_review_tables': 7}, ensure_ascii=False, indent=2))
