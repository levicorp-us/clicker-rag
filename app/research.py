"""Find public company sources and register evidence from pages the worker actually read."""

import ipaddress
import re
import socket
from datetime import date
from typing import Literal
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx
from lxml import etree, html
from pydantic import BaseModel, Field

from . import models
from .core import one
from .documents import store_doc
from .job_control import check_running


class Selection(BaseModel):
    ids: list[int] = Field(max_length=5)


class SourceExcerpt(BaseModel):
    kind: Literal["공고·직무", "사업", "인재상", "문화"]
    lines: list[int] = Field(max_length=5)


def page_url(value):
    url = httpx.URL(urldefrag(value)[0])
    if (
        url.scheme not in ("https", "http")
        or not url.host
        or url.userinfo
        or url.port not in (None, 80, 443)
    ):
        raise ValueError("공개 웹페이지 주소가 아닙니다.")
    return url


def download(address, limit=2_500_000):
    # Connect to the checked IP and preserve the original TLS hostname, including on redirects.
    for _ in range(5):
        check_running()
        url = page_url(address)
        ips = [
            r[4][0]
            for r in socket.getaddrinfo(
                url.host,
                url.port or (443 if url.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        ]
        addresses = [ipaddress.ip_address(ip) for ip in ips]
        if not addresses or any(
            not ip.is_global or ip.is_multicast for ip in addresses
        ):
            raise ValueError("공개 웹페이지 주소가 아닙니다.")
        with (
            httpx.Client(timeout=7, trust_env=False) as client,
            client.stream(
                "GET",
                url.copy_with(host=ips[0]),
                headers={
                    "Host": url.netloc.decode(),
                    "User-Agent": "Mozilla/5.0 (compatible; ClickerResearch/1.0)",
                    "Accept-Encoding": "identity",
                },
                extensions={"sni_hostname": url.host},
            ) as response,
        ):
            if response.is_redirect and response.headers.get("location"):
                address = urljoin(str(url), response.headers["location"])
                continue
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes(32768):
                check_running()
                if len(content) + len(chunk) > limit:
                    raise ValueError("페이지 크기 제한을 초과했습니다.")
                content.extend(chunk)
            return (
                str(url),
                bytes(content),
                response.headers.get("content-type", ""),
            )
    raise ValueError("웹페이지 이동 횟수를 초과했습니다.")


PAGE_ERRORS = (httpx.HTTPError, OSError, ValueError, etree.LxmlError, httpx.InvalidURL)


def search(company, role):
    query = f"{company} {role} 직무 사업"
    hits = {}
    try:
        url = httpx.URL(
            "https://search.naver.com/search.naver",
            params={"where": "web", "query": query},
        )
        _, body, _ = download(str(url), 4_000_000)
        for anchor in html.fromstring(body.decode("utf-8", "replace")).xpath(
            '//a[contains(@class,"fds-anchor-layout")][@href]'
        ):
            href = anchor.get("href")
            title = " ".join(anchor.text_content().replace("새 창 열림", "").split())
            if (
                not href.startswith(("https://", "http://"))
                or len(title) < 10
                or "›" in title
                or not re.search(r"[^0-9.\s]", title)
            ):
                continue
            host = urlsplit(href).hostname or ""
            if host.endswith((".naver.com", ".pstatic.net")) and host not in (
                "news.naver.com",
                "n.news.naver.com",
            ):
                continue
            if (
                re.fullmatch(r"[a-zA-Z0-9.\s:/-]+", title)
                and "." in title
                and " " not in title.strip()
            ):
                continue
            hits.setdefault(urldefrag(href)[0], title[:140])
            if len(hits) == 16:
                break
    except PAGE_ERRORS:
        pass
    try:
        url = httpx.URL(
            "https://www.bing.com/search",
            params={"q": query, "format": "rss", "setlang": "ko-KR"},
        )
        _, body, _ = download(str(url))
        tree = etree.fromstring(
            body, parser=etree.XMLParser(resolve_entities=False, no_network=True)
        )
        for item in tree.findall(".//item")[:10]:
            href, title = item.findtext("link"), item.findtext("title")
            if href and title and href.startswith(("https://", "http://")):
                hits.setdefault(urldefrag(href)[0], title[:140])
    except PAGE_ERRORS:
        pass
    if not hits:
        raise ValueError(
            "웹 검색 결과를 가져오지 못했습니다. 네트워크 연결을 확인한 뒤 재시도해 주세요."
        )
    return [{"url": url, "title": title} for url, title in hits.items()]


def read_page(address, terms):
    url, body, mime = download(address)
    if "html" not in mime.lower():
        raise ValueError("본문을 읽을 수 없는 형식입니다.")
    charset = re.search(r"charset=([\w-]+)", mime, re.IGNORECASE)
    try:
        tree = html.fromstring(body.decode(charset[1] if charset else "utf-8"))
    except (UnicodeError, LookupError):
        tree = html.fromstring(body)
    title = " ".join(tree.xpath("string(//title)").split())[:140]
    for node in tree.xpath(
        "//head|//script|//style|//nav|//header|//footer|//template|//noscript|//form"
    ):
        node.drop_tree()
    root = (
        tree.xpath("//main")
        or tree.xpath("//article")
        or tree.xpath("//body")
        or [tree]
    )[0]
    # Preserve inline phrases and break only at block boundaries.
    for element in root.iter(
        "p", "div", "section", "li", "h1", "h2", "h3", "td", "br", "dd"
    ):
        element.text = "\n" + (element.text or "")
        element.tail = "\n" + (element.tail or "")
    lines = list(
        dict.fromkeys(
            x
            for line in root.text_content().splitlines()
            if len(x := " ".join(line.split())[:450]) >= 12
        )
    )
    if not lines:
        raise ValueError("읽을 수 있는 본문이 없습니다.")
    words = [w.casefold() for w in terms if len(w) > 1]
    ranked = sorted(
        range(len(lines)), key=lambda i: -sum(w in lines[i].casefold() for w in words)
    )
    sample = []
    for i in ranked:
        if sum(map(len, sample)) + len(lines[i]) > 3200:
            break
        sample.append(lines[i])
        if len(sample) == 28:
            break
    return url, title, sample


def research_company(db, owner, args):
    aid = args["application"]
    app = one(
        db,
        "SELECT company,role FROM applications WHERE owner=%s AND id=%s",
        (owner, aid),
    )
    db.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("research:" + aid,)
    )
    candidates = search(app["company"], app["role"])
    selection = models.structured(
        "research",
        "회사와 직무를 조사할 출처를 관련도순으로 최대 5개 고른다. "
        "공식 채용·직무 소개, 공식 사업·기술 페이지를 우선한다. 관련 없는 출처는 제외한다. ids에 목록의 번호만 넣는다.",
        {**app, "후보": {i: row for i, row in enumerate(candidates, 1)}},
        Selection,
    )
    registered, duplicates = [], 0
    for index in dict.fromkeys(selection.ids):
        check_running()
        if not 1 <= index <= len(candidates):
            continue
        candidate = candidates[index - 1]
        try:
            url, title, lines = read_page(
                candidate["url"], (app["company"] + " " + app["role"]).split()
            )
        except PAGE_ERRORS:
            continue
        exists = db.execute(
            """SELECT id FROM documents WHERE owner=%s AND app=%s AND active
            AND metadata->>'web_url'=%s""",
            (owner, aid, url),
        ).fetchone()
        if exists:
            duplicates += 1
            continue
        excerpt = models.structured(
            "research",
            "제목과 본문을 보고 해당 회사의 사업·채용·직무에 직접 관련된 "
            "사실을 뒷받침하는 본문 번호를 1~5개 고른다. 직무 소개·담당 업무·요건은 공고·직무로, 제품·사업 현황은 사업으로 분류한다. 광고, 메뉴, 타 기업, 무관한 내용만 있으면 lines를 비운다.",
            {**app, "페이지": title, "본문": dict(enumerate(lines, 1))},
            SourceExcerpt,
        )
        selected = [
            lines[n - 1] for n in sorted(set(excerpt.lines)) if 1 <= n <= len(lines)
        ]
        if not selected:
            continue
        source_title = title or candidate["title"]
        kind = excerpt.kind
        if re.search(r"직무\s*(소개|인터뷰|편)|채용\s*공고", source_title):
            kind = "공고·직무"
        doc = store_doc(
            db,
            owner,
            source_title,
            "웹 조사.txt",
            " ".join(selected).encode("utf-8"),
            kind,
            aid,
            {
                "web_url": url,
                "source": url,
                "checked": date.today().isoformat(),
                "role": app["role"],
            },
        )
        registered.append(str(doc["id"]))
        if len(registered) == 3:
            break
    if not registered and not duplicates:
        raise ValueError(
            "확인 가능한 회사·직무 관련 본문을 찾지 못했습니다. 조사 결과를 등록하지 않았습니다."
        )
    return {"documents": registered, "duplicates": duplicates}
