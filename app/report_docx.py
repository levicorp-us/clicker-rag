"""Edit selected Word paragraphs in place while copying the rest of the package."""

import re
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from lxml import etree as ET

NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{" + NS + "}"
SPACE = "{http://www.w3.org/XML/1998/namespace}space"
MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_FILE = 25 * 1024 * 1024
PART = re.compile(r"word/(document|header\d+|footer\d+)\.xml$")
TOKEN = {"tab": "\t", "br": "\n", "cr": "\n"}


class WordForm:
    def __init__(self, data):
        self.data = bytes(data)
        self.parts = {}
        self.changed = set()
        self.slots = {}
        try:
            with ZipFile(BytesIO(self.data)) as archive:
                names = archive.namelist()
                if (
                    len(names) != len(set(names))
                    or len(names) > 3000
                    or sum(x.file_size for x in archive.infolist()) > 100 * 1024 * 1024
                ):
                    raise ValueError(
                        "압축을 푼 문서가 너무 크거나 중복 항목이 있습니다."
                    )
                if "word/document.xml" not in names:
                    raise ValueError("Word DOCX 문서를 선택해 주세요.")
                if any(
                    n.startswith(
                        (
                            "word/comments",
                            "word/embeddings/",
                            "word/vbaProject",
                            "_xmlsignatures/",
                        )
                    )
                    for n in names
                ):
                    raise ValueError(
                        "메모·첨부 개체·매크로·서명이 있는 문서는 정리한 사본을 등록해 주세요."
                    )
                for idx, name in enumerate(
                    sorted(n for n in names if PART.fullmatch(n))
                ):
                    root = ET.fromstring(
                        archive.read(name),
                        ET.XMLParser(resolve_entities=False, no_network=True),
                    )
                    if root.getroottree().docinfo.doctype or any(
                        root.find(".//" + W + k) is not None
                        for k in (
                            "ins",
                            "del",
                            "moveFrom",
                            "moveTo",
                            "altChunk",
                            "dataBinding",
                            "txbxContent",
                        )
                    ):
                        raise ValueError(
                            "변경 추적·연결된 콘텐츠·텍스트 상자는 일반 문단이나 표로 변환한 사본을 등록해 주세요."
                        )
                    self.parts[name] = root
                    section = (
                        "본문"
                        if "document" in name
                        else "머리말"
                        if "header" in name
                        else "꼬리말"
                    )
                    for i, p in enumerate(root.iter(W + "p")):
                        # Field codes and linked text can regenerate old values in Word.
                        if any(
                            p.find(".//" + W + k) is not None
                            for k in ("fldChar", "instrText", "fldSimple", "hyperlink")
                        ):
                            continue
                        runs = list(p.iter(W + "r"))
                        tokens = [
                            n
                            for run in runs
                            for n in run
                            if n.tag in (W + "t", W + "tab", W + "br", W + "cr")
                            and n.get(W + "type", "textWrapping") == "textWrapping"
                        ]
                        text = "".join(self.value(n) for n in tokens)
                        loc = f"{section} {i + 1}"
                        cell = next(p.iterancestors(W + "tc"), None)
                        if cell is not None:
                            row = cell.getparent()
                            loc += (
                                f" · 표 {row.getparent().findall(W + 'tr').index(row) + 1}행 {row.findall(W + 'tc').index(cell) + 1}칸"
                                if row.tag == W + "tr"
                                and row.getparent().tag == W + "tbl"
                                else " · 표"
                            )
                        self.slots[f"s{idx}p{i}"] = (p, tokens, text, loc)
                if not self.slots or len(self.slots) > 2000:
                    raise ValueError(
                        "편집 가능한 문단이 1~2,000개인 양식을 등록해 주세요."
                    )
        except (BadZipFile, ET.XMLSyntaxError, RuntimeError, KeyError) as exc:
            raise ValueError(
                "DOCX 문서를 읽을 수 없습니다. Word에서 새 사본으로 저장해 주세요."
            ) from exc

    @staticmethod
    def value(node):
        if node.tag == W + "t":
            return node.text or ""
        return TOKEN[ET.QName(node).localname]

    def inventory(self):
        slots = []
        for key, (p, _, text, loc) in self.slots.items():
            style = p.find(W + "pPr/" + W + "pStyle")
            fixed = style is not None and style.get(W + "val", "").lower().startswith(
                ("title", "subtitle", "heading")
            )
            label = ""
            cell = next(p.iterancestors(W + "tc"), None)
            if cell is not None and cell.getparent().tag == W + "tr":
                row = cell.getparent()
                cells = row.findall(W + "tc")
                col = cells.index(cell)
                header_table = (
                    row.getparent().find(W + "tr/" + W + "trPr/" + W + "tblHeader")
                    is not None
                )
                if row.find(W + "trPr/" + W + "tblHeader") is not None:
                    fixed = True
                if (
                    not header_table
                    and col == 0
                    and len(cells) == 2
                    and 0 < len(text.strip()) <= 30
                ):
                    fixed = True
                if col and len(cells) == 2 and not header_table:
                    sibling = "".join(cells[0].itertext(W + "t")).strip()
                    if 0 < len(sibling) <= 40:
                        label = sibling
            slots.append(
                {
                    "key": key,
                    "text": text,
                    "location": loc,
                    "fixed_hint": fixed,
                    "label_hint": label,
                }
            )
        return slots

    def replace(self, key, prefix, body, anchor=None):
        p, tokens, original, _ = self.slots[key]
        if not original.startswith(prefix):
            raise ValueError("고정 문구는 해당 문단의 시작 부분과 일치해야 합니다.")
        runs = list(p.iter(W + "r"))
        if body or prefix != original:
            self.changed.add(p.getroottree().getroot())
        left = len(prefix)
        chosen = None
        for node in tokens:
            value = self.value(node)
            if left >= len(value) and value:
                left -= len(value)
                continue
            if chosen is None:
                chosen = node.getparent()
            if node.tag == W + "t":
                node.text = value[:left]
                node.set(SPACE, "preserve")
            else:
                node.getparent().remove(node)
            left = 0
        if anchor is None:
            anchor = runs.index(chosen) if chosen is not None else max(0, len(runs) - 1)
        if body:
            run = runs[anchor] if runs else ET.SubElement(p, W + "r")
            # Insert before trailing drawings or page breaks in the same run.
            position = next(
                (
                    i
                    for i, n in enumerate(run)
                    if n.tag in (W + "drawing", W + "br") and n not in tokens
                ),
                len(run),
            )
            for piece in re.split(r"(\n|\t)", body):
                if piece in ("\n", "\t"):
                    node = ET.Element(W + ("br" if piece == "\n" else "tab"))
                else:
                    node = ET.Element(W + "t")
                    node.set(SPACE, "preserve")
                    node.text = piece
                run.insert(position, node)
                position += 1
        return anchor

    def mark(self, keys):
        for key in keys:
            paragraph = self.slots[key][0]
            self.changed.add(paragraph.getroottree().getroot())
            run = ET.SubElement(paragraph, W + "r")
            ET.SubElement(run, W + "t").text = "⟦CLICKER:" + key + "⟧"

    def pack(self):
        out = BytesIO()
        with ZipFile(BytesIO(self.data)) as src, ZipFile(out, "w") as dest:
            for item in src.infolist():
                name = item.filename
                if name in self.parts and self.parts[name] in self.changed:
                    data = ET.tostring(
                        self.parts[name],
                        encoding="UTF-8",
                        xml_declaration=True,
                        standalone=True,
                    )
                elif name.startswith("docProps/thumbnail."):
                    # An Office thumbnail may still show the filled source document.
                    continue
                elif name in ("_rels/.rels", "[Content_Types].xml"):
                    root = ET.fromstring(src.read(name))
                    for rel in list(root):
                        if rel.get("Type", "").endswith("/thumbnail") or rel.get(
                            "PartName", ""
                        ).startswith("/docProps/thumbnail."):
                            root.remove(rel)
                    data = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
                else:
                    data = src.read(name)
                dest.writestr(item, data)
        return out.getvalue()
