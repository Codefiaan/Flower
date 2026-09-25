from backend import reports
from backend.providers import sec


def test_html_to_text_strips_markup():
    raw = b"<html><style>x{}</style><body><p>Revenue grew&nbsp;10%</p><script>alert(1)</script><div>Outlook</div></body></html>"
    text = reports.html_to_text(raw)
    assert "Revenue grew 10%" in text and "Outlook" in text and "alert" not in text


def test_select_passages_prefers_relevant_chunks():
    filler = "Lorem ipsum dolor sit amet. " * 400
    key = "Our outlook and guidance for next year: revenue growth and margin expansion; key risk is competition. " * 20
    text = filler + key + filler
    out = reports.select_passages(text, 5000)
    assert len(out) < len(text)
    assert "guidance" in out


def test_rank_results_prefers_company_pdf():
    results = [{"title": "Wiki", "url": "https://en.wikipedia.org/wiki/Siemens"},
               {"title": "Annual Report 2025", "url": "https://assets.new.siemens.com/annual-report-2025.pdf"},
               {"title": "News", "url": "https://news.example.com/siemens"}]
    ranked = reports.rank_results(results, "https://www.siemens.com")
    assert ranked[0]["url"].endswith(".pdf")
    assert ranked[-1]["url"].startswith("https://en.wikipedia")


def test_pdf_text_extraction():
    content = b"BT /F1 12 Tf 72 720 Td (Net sales increased strongly) Tj ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    pdf, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    assert "Net sales increased strongly" in reports.to_text(pdf, "https://x/report.pdf")


def test_sec_annual_series_filters_quarters_and_keeps_latest_filing():
    facts = {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": [
        {"start": "2022-01-01", "end": "2022-12-31", "val": 5.0, "form": "10-K", "filed": "2023-02-01"},
        {"start": "2022-01-01", "end": "2022-12-31", "val": 5.1, "form": "10-K", "filed": "2024-02-01"},  # restated
        {"start": "2022-10-01", "end": "2022-12-31", "val": 1.2, "form": "10-K", "filed": "2023-02-01"},  # quarter
        {"start": "2023-01-01", "end": "2023-12-31", "val": 6.0, "form": "10-Q", "filed": "2024-02-01"},  # wrong form
    ]}}}}}
    series, cur = sec.annual_series(facts, sec.EPS_TAGS)
    assert series == {"2022-12-31": 5.1}
    assert cur == "USD"


def test_sec_skips_non_us_symbols():
    assert sec.cik_for("SAP.DE") is None
    assert sec.cik_for("^GSPC") is None
