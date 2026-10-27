QUOTE_EXTRACTION = '''Extract the supplier quote into the provided structured schema.
For every populated field return value, page number and verbatim_span copied
CHARACTER FOR CHARACTER from the document text. Never paraphrase or invent a
source span. Do not guess missing values. Return null for absent values.
verbatim_span must be the SHORTEST exact text that contains the value: normally the
single table cell or the single line it appears in (e.g. "$148.50", "Currency: USD").
Never join text from different lines or rows (e.g. a table header plus a data row).
Table rows are shown with cells separated by " | ".
Do not claim fields are grounded: only the local grounding verifier may set that.
stated_total is the quote's total amount as printed (the grand total, or the only total).
Use page 0 for an email body. Every line item must include a part number if present.
For every populated field also set raw_confidence: your probability, from 0.0 to 1.0, that the value
is correct (e.g. 0.98 when it is printed plainly, lower when you inferred or converted it). Leave it
null for absent fields. It is recorded for analysis only; it never decides anything.'''
