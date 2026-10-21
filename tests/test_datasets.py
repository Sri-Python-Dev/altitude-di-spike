from eval.datasets import ROOT, golden_dataset, harness_datasets, path

def test_defaults(monkeypatch):
    monkeypatch.delenv('ALTITUDE_GOLDEN_DATASET', raising=False)
    monkeypatch.delenv('ALTITUDE_EXTRA_DATASETS', raising=False)
    assert harness_datasets() == ['data/golden/quotes-v1', 'data/synthetic/edge-cases', 'data/synthetic']

def test_own_golden_set_alone(monkeypatch):
    monkeypatch.setenv('ALTITUDE_GOLDEN_DATASET', 'data/golden/my-set')
    monkeypatch.setenv('ALTITUDE_EXTRA_DATASETS', '')
    assert golden_dataset() == 'data/golden/my-set' and harness_datasets() == ['data/golden/my-set']
    assert path('data/golden/my-set') == ROOT / 'data/golden/my-set'
