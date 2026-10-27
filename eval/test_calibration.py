"""Confidence calibration (analysis only): the maths, and that confidence never reaches rules or the app's tables."""
from eval.metrics import calculate, calibration
from extract.extractors import _clamp_confidence

def test_perfectly_calibrated_confidence_has_zero_error():
    # 10 answers at 0.75 confidence, 7.5 right on average -> use 0.8 with 8 of 10 right in one bin.
    pairs = [(0.85, True)] * 17 + [(0.85, False)] * 3
    c = calibration(pairs)
    assert c['ece'] == 0.0 and c['right'] == 17 and c['wrong'] == 3

def test_overconfidence_is_measured():
    pairs = [(0.99, True)] * 5 + [(0.99, False)] * 5
    c = calibration(pairs)
    assert c['ece'] == 0.49 and c['overconfident_errors'] == 5
    assert c['brier'] == round((5 * 0.01 ** 2 + 5 * 0.99 ** 2) / 10, 4)
    assert c['auroc'] == 0.5  # same confidence for right and wrong: no signal

def test_confidence_that_separates_right_from_wrong():
    c = calibration([(0.95, True), (0.9, True), (0.4, False), (0.3, False)])
    assert c['auroc'] == 1.0 and c['mean_confidence_wrong'] == 0.35

def test_no_wrong_answers_means_no_auroc():
    c = calibration([(0.9, True), (0.95, True)])
    assert c['auroc'] is None and c['wrong'] == 0
    assert calibration([]) is None

def test_calibration_uses_the_claimed_value_before_grounding():
    golden = [{'document_id': 'd', 'fields': [{'name': 'currency', 'value': 'USD', 'verbatim_span': 'USD'},
                                              {'name': 'incoterms', 'value': 'FCA', 'verbatim_span': 'FCA'}]}]
    predicted = [{'fields': [
        # Rejected by grounding (value emptied) but the model's claim was right: counts as right for calibration.
        {'name': 'currency', 'value': None, 'claimed_value': 'USD', 'raw_confidence': 0.6, 'grounded': False, 'ungrounded_reason': 'no_bbox'},
        {'name': 'incoterms', 'value': 'FCA', 'claimed_value': 'FCA', 'raw_confidence': 0.95, 'grounded': True, 'verbatim_span': 'FCA'}]}]
    report = calculate(golden, predicted)
    assert report['calibration']['right'] == 2 and report['fields']['currency']['accuracy'] == 0  # accuracy is unaffected
    assert report['fields']['incoterms']['confidence_coverage'] == 1.0

def test_confidence_is_clamped_to_zero_one():
    data = _clamp_confidence({'a': {'raw_confidence': 95}, 'b': [{'raw_confidence': 0.7}], 'c': {'raw_confidence': 'high'},
                              'd': {'raw_confidence': -1}})
    assert data['a']['raw_confidence'] == 0.95 and data['b'][0]['raw_confidence'] == 0.7
    assert data['c']['raw_confidence'] is None and data['d']['raw_confidence'] is None

def test_confidence_stays_out_of_rules_and_the_results_page(tmp_path, monkeypatch):
    import json
    import ui.server as server
    graph = json.loads(open('rules/quote_flags.json').read())
    assert 'confidence' not in json.dumps([n.get('content', {}).get('inputs') for n in graph['nodes']])
    report = {'run_id': 'r', 'extractor': 'current', 'document_count': 1, 'labellers': [], 'datasets': [],
              'fields': {'currency': {'label': 'Currency', 'accuracy': 1.0, 'grounded_rate': 1.0, 'ungrounded_rate': 0.0,
                                      'missing_rate': 0.0, 'delta': None, 'worst_doc': None,
                                      'calibration': {'ece': 0.123456}, 'confidence_coverage': 0.987654}},
              'calibration': {'ece': 0.123456}}
    monkeypatch.setattr(server, 'REPORTS', tmp_path)
    (tmp_path / 'latest.json').write_text(json.dumps(report))
    page = server.render_results({})
    assert '0.123456' not in page and '0.987654' not in page and 'confidence' not in page.lower().replace('never model confidence', '')
