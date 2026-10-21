import json
from pathlib import Path
import pytest
from eval.metrics import calculate, match_line_items

def test_metrics_wrong_and_ungrounded():
    golden = [{'fields':[{'name':'supplier_name','value':'Acme','verbatim_span':'Acme'}, {'name':'currency','value':'USD','verbatim_span':'USD'}]}, {'fields':[{'name':'supplier_name','value':'Beta','verbatim_span':'Beta'}, {'name':'currency','value':'EUR','verbatim_span':'EUR'}]}]
    predicted = [{'fields':[{'name':'supplier_name','value':'Acme','verbatim_span':'Acme','grounded':True},{'name':'currency','value':'GBP','verbatim_span':'GBP','grounded':True}]}, {'fields':[{'name':'supplier_name','value':None,'verbatim_span':'invented','grounded':False},{'name':'currency','value':'EUR','verbatim_span':'EUR','grounded':True}]}]
    report = calculate(golden, predicted, [1,3])
    assert report['fields']['supplier_name']['accuracy'] == .5
    assert report['fields']['supplier_name']['ungrounded_rate'] == .5
    assert report['fields']['supplier_name']['grounded_rate'] == .5
    assert report['fields']['currency']['accuracy'] == .5
    assert report['fields']['currency']['grounded_rate'] == 1
    assert report['p95_latency_s'] == 3
    assert report['document_count'] == 2
    assert report['worst_fields'][0] in ('currency', 'supplier_name')

def test_absent_field_is_missing_not_ungrounded():
    golden = [{'fields': [{'name': 'incoterms', 'value': 'FCA', 'verbatim_span': 'FCA'}]}]
    predicted = [{'fields': [{'name': 'incoterms', 'value': None, 'verbatim_span': None, 'grounded': False}]}]
    m = calculate(golden, predicted)['fields']['incoterms']
    assert m['missing_rate'] == 1 and m['ungrounded_rate'] == 0

def test_evidence_match_needs_80_percent_overlap():
    golden = [{'fields': [{'name': 'payment_terms', 'value': 'Net 45', 'verbatim_span': 'Payment Terms: Net 45'}]}] * 2
    predicted = [{'fields': [{'name': 'payment_terms', 'value': 'Net 45', 'verbatim_span': 'Payment Terms: Net 45 days', 'grounded': True}]},
                 {'fields': [{'name': 'payment_terms', 'value': 'Net 45', 'verbatim_span': 'Net 45', 'grounded': True}]}]
    assert calculate(golden, predicted)['fields']['payment_terms']['evidence_match_rate'] == .5

def item(i, pn, qty):
    return [{'name': f'line_items[{i}].part_number_quoted', 'value': pn, 'verbatim_span': pn, 'grounded': True},
            {'name': f'line_items[{i}].quantity', 'value': qty, 'verbatim_span': str(qty), 'grounded': True}]

def test_line_items_matched_by_part_number_before_position():
    golden = [{'fields': item(0, 'A-1', 5) + item(1, 'B-2', 7)}]
    predicted = [{'fields': item(0, 'B-2', 7) + item(1, 'A-1', 5)}]  # same items, other order
    report = calculate(golden, predicted)
    assert report['fields']['line_items[i].quantity']['accuracy'] == 1

def test_line_items_fall_back_to_position():
    assert match_line_items({0: {}, 1: {}}, {0: {}, 1: {}}) == {0: 0, 1: 1}

def test_number_and_currency_normalisation():
    golden = [{'fields': [{'name': 'line_items[0].unit_price', 'value': 4500, 'verbatim_span': '4,500.00'},
                          {'name': 'currency', 'value': 'GBP', 'verbatim_span': 'GBP'}]}]
    predicted = [{'fields': [{'name': 'line_items[0].unit_price', 'value': '4,500.00', 'verbatim_span': '4,500.00', 'grounded': True},
                             {'name': 'currency', 'value': '£', 'verbatim_span': '£', 'grounded': True}]}]
    fields = calculate(golden, predicted)['fields']
    assert fields['line_items[i].unit_price']['accuracy'] == 1 and fields['currency']['accuracy'] == 1

BASELINE = Path(__file__).resolve().parent / 'baseline.json'
from eval.datasets import golden_dataset, path
QUOTES = path(golden_dataset())

@pytest.mark.eval
def test_no_field_regresses_against_baseline(tmp_path):
    """uv run pytest -m eval — needs a model API key and an established baseline."""
    baseline = json.loads(BASELINE.read_text())
    if 'fields' not in baseline:
        pytest.skip('baseline not established: run eval.run --extractor current --write-baseline')
    from eval.run import run
    report = run([QUOTES], baseline['extractor'], out_dir=tmp_path)
    drops = {name: (b['accuracy'], report['fields'].get(name, {}).get('accuracy', 0))
             for name, b in baseline['fields'].items()
             if report['fields'].get(name, {}).get('accuracy', 0) < b['accuracy'] - 0.02}
    assert not drops, f'accuracy dropped more than 2 points: {drops}'

def test_evidence_match_is_coverage_of_the_golden_span_not_similarity():
    from eval.metrics import overlap
    assert overlap('Currency: USD', 'USD')                 # label + value still covers the golden span
    assert overlap('NS-BRG-104 | 25 | $148.50', '$148.50')
    assert not overlap('Net 45', 'Payment Terms: Net 45')  # covers only 6 of 21 characters
    assert not overlap('EUR', 'USD')

def test_evidence_match_needs_the_same_place():
    golden = [{'fields': [{'name': 'line_items[0].moq', 'value': 9, 'verbatim_span': '9', 'page': 1, 'bbox': [497, 234, 502, 243]}]}] * 3
    hit = {'name': 'line_items[0].moq', 'value': 9, 'verbatim_span': '9', 'grounded': True, 'page': 1, 'bbox': [496, 233, 503, 244]}
    wrong_cell = dict(hit, bbox=[223, 234, 246, 243])  # the "9" inside "29.05"
    wrong_page = dict(hit, page=2)
    report = calculate(golden, [{'fields': [hit]}, {'fields': [wrong_cell]}, {'fields': [wrong_page]}])
    assert report['fields']['line_items[i].moq']['evidence_match_rate'] == round(1 / 3, 4)
