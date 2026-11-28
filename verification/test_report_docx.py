from io import BytesIO
from zipfile import ZipFile

import pytest
from docx import Document

from app.report_api import blank_fields
from app.report_docx import WordForm
from app.report_jobs import Slot


def form_fixture():
    doc = Document()
    doc.sections[0].header.paragraphs[0].text = "운영팀"
    doc.add_heading("주간보고서", 0)
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "보고 항목"
    table.cell(1, 0).text = "주요 실적"
    p = table.cell(1, 1).paragraphs[0]
    p.add_run("실적: ").bold = True
    p.add_run("지난 주 실적").italic = True
    doc.add_paragraph("작성 예시")
    stream = BytesIO()
    doc.save(stream)
    return stream.getvalue()


def test_blank_round_trip_keeps_layout_and_writes_only_selected_runs():
    raw = form_fixture()
    original = WordForm(raw)
    slots = {s["text"]: s for s in original.inventory()}
    target = slots["실적: 지난 주 실적"]
    assert target["label_hint"] == "주요 실적"
    assert slots["주요 실적"]["fixed_hint"] and slots["주간보고서"]["fixed_hint"]
    fields, sources = blank_fields(
        original,
        [
            Slot(key=target["key"], label="주요 실적", prefix="실적: "),
            Slot(key=slots["작성 예시"]["key"], label="예시", keep_as_source=False),
        ],
    )
    assert sources == ["주요 실적 · 지난 주 실적"]
    cleared = original.pack()
    assert "지난 주 실적" not in [s["text"] for s in WordForm(cleared).inventory()]
    draft = WordForm(cleared)
    draft.replace(
        target["key"], "실적: ", "이번 주 실적\n세부\t내역", fields[0]["anchor"]
    )
    data = draft.pack()
    doc = Document(BytesIO(data))
    cell = doc.tables[0].cell(1, 1)
    assert cell.text == "실적: 이번 주 실적\n세부\t내역"
    assert cell.paragraphs[0].runs[0].bold and cell.paragraphs[0].runs[1].italic
    assert doc.tables[0].cell(0, 0)._tc is doc.tables[0].cell(0, 1)._tc
    assert doc.sections[0].header.paragraphs[0].text == "운영팀"
    with ZipFile(BytesIO(raw)) as src, ZipFile(BytesIO(data)) as out:
        assert src.read("word/styles.xml") == out.read("word/styles.xml")
        assert src.read("word/header1.xml") == out.read("word/header1.xml")
        assert not any(n.startswith("docProps/thumbnail.") for n in out.namelist())


def test_blank_form_accepts_empty_cell_and_rejects_incorrect_prefix():
    doc = Document()
    doc.add_table(rows=1, cols=1)
    stream = BytesIO()
    doc.save(stream)
    form = WordForm(stream.getvalue())
    key = form.inventory()[0]["key"]
    with pytest.raises(ValueError):
        form.replace(key, "다른 문구", "")
    fields, evidence = blank_fields(form, [Slot(key=key, label="실적")])
    assert not evidence
    blank = WordForm(form.pack())
    blank.replace(key, "", "작성 완료", fields[0]["anchor"])
    assert Document(BytesIO(blank.pack())).tables[0].cell(0, 0).text == "작성 완료"
