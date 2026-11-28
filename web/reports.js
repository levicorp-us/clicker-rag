// Report pages share the application session, dialogs, data viewer and job controls.
let reportView;
const reportDrafts = new Map();
const rpButton = (act, label, value = "", primary = false) =>
  '<button type="button" data-rp="' +
  esc(act) +
  '" data-value="' +
  esc(value) +
  '" class="' +
  (primary ? "primary" : "") +
  '">' +
  esc(label) +
  "</button>";
const rpLink = (url, label) =>
  '<a class="report-link" href="' + esc(url) + '">' + esc(label) + "</a>";
const rpTags = (tags) =>
  tags.map((t) => '<span class="badge">' + esc(t) + "</span>").join(" ");
const rpForm = (name, content, label) =>
  '<form data-rp-form="' +
  name +
  '">' +
  content +
  '<button type="submit" class="primary">' +
  label +
  "</button></form>";
const activeReportView = (v) => reportView === v && location.hash === v.hash;

async function reportWorkspace(page, id) {
  const v = (reportView = { page, id, hash: location.hash });
  $("#main").innerHTML = "<p>불러오는 중…</p>";
  if (page === "reports" || page === "report-data") {
    v.catalog = await api("reports");
    if (page === "report-data") v.materials = await api("reports/materials");
    if (!activeReportView(v)) return;
    const data = page === "report-data";
    $("#main").innerHTML =
      '<div class="head"><h1>' +
      (data ? "보고서 자료" : "보고서 작성") +
      '</h1><div class="bar">' +
      rpButton("upload-template", "양식 등록") +
      (data
        ? rpButton("material", "업무 자료 등록", "", true)
        : rpButton("new", "새 보고서", "", true)) +
      "</div></div>" +
      '<div class="bar">' +
      rpLink(
        data ? "#reports" : "#report-data",
        data ? "보고서 목록" : "양식 · 업무 자료 조회",
      ) +
      "</div>" +
      '<label>검색<input id="rp-search" type="search" placeholder="이름 또는 태그"></label><div id="rp-results"></div>';
    return reportList(v, "");
  }
  v.data = await api(
    page === "template" ? "reports/templates/" + id : "reports/" + id,
  );
  if (!activeReportView(v)) return;
  if (page === "template") {
    v.fields = new Map(v.data.fields.map((f) => [f.key, { ...f }]));
    v.key = v.data.slots[0]?.key;
  } else {
    v.fields = new Map(v.data.fields.map((f) => [f.key, f]));
    v.key = v.data.fields[0]?.key;
  }
  const review = page === "template" && !v.data.ready;
  $("#main").innerHTML =
    '<div class="head"><div>' +
    rpLink("#reports", "← 보고서 목록") +
    "<h1>" +
    esc(v.data.title || v.data.name) +
    '</h1></div><div class="bar">' +
    (review
      ? rpButton("toggle-preview", "비운 양식 미리보기") +
        rpButton("confirm", "양식 등록 완료", "", true)
      : page === "template"
        ? rpLink("/api/reports/templates/" + id + "/file", "빈 양식 다운로드") +
          rpButton("new", "이 양식으로 작성", id, true)
        : rpButton("export", "DOCX 다운로드", "", true)) +
    '</div></div><div class="report-work"><aside class="panel"><h2>' +
    (review ? "원본 문단" : "양식 항목") +
    '</h2><div id="rp-slots"></div></aside>' +
    '<section class="report-paper"><div id="rp-preview-status" role="status">양식을 불러오고 있습니다.</div><iframe title="보고서 양식 미리보기" id="rp-frame" sandbox="allow-same-origin"></iframe></section>' +
    '<aside class="panel"><div id="rp-meta"></div><div id="rp-editor"></div></aside></div>';
  if (review) {
    $("#rp-meta").innerHTML =
      "<p>바뀌는 내용만 입력 칸으로 지정하세요. 선택하지 않은 문구와 그림은 양식에 남습니다.</p>" +
      field("rp-name", "양식 이름", v.data.name) +
      field("rp-tags", "양식 태그 (쉼표로 구분)", v.data.tags.join(", ")) +
      '<div id="rp-analysis"></div>';
    showAnalysis(v);
  } else $("#rp-meta").innerHTML = rpTags(v.data.tags);
  reportControls(v);
  await reportPreview(v);
}
function reportList(v, search) {
  const match = (x) =>
    [x.name, x.title, ...(x.tags || [])]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(search.toLowerCase());
  const section = (name, rows, none) =>
    '<section class="report-section"><h2>' +
    name +
    '</h2><div class="grid">' +
    (rows.join("") || '<p class="muted">' + none + "</p>") +
    "</div></section>";
  const card = (url, title, meta, tags) =>
    '<a class="card" href="' +
    esc(url) +
    '"><h3>' +
    esc(title) +
    "</h3><small>" +
    esc(meta) +
    "</small><div>" +
    rpTags(tags) +
    "</div></a>";
  const templates = v.catalog.templates
    .filter(match)
    .map((t) =>
      card(
        "#template/" + t.id,
        t.name,
        t.ready ? "등록된 양식" : "입력 칸 확인 필요",
        t.tags,
      ),
    );
  $("#rp-results").innerHTML =
    (v.page === "reports"
      ? section(
          "내 보고서",
          v.catalog.reports
            .filter(match)
            .map((r) => card("#report/" + r.id, r.title, r.name, r.tags)),
          "작성 중인 보고서가 없습니다.",
        )
      : "") +
    section("보고서 양식", templates, "등록한 양식이 없습니다.") +
    (v.page === "report-data"
      ? section(
          "업무 자료",
          v.materials
            .filter(match)
            .map(
              (d) =>
                '<div class="card"><h3>' +
                esc(d.name) +
                "</h3><small>" +
                esc(d.status) +
                "</small><div>" +
                rpTags(d.tags) +
                "</div>" +
                rpButton("source", "원문 보기", d.id) +
                "</div>",
            ),
          "등록된 업무 자료가 없습니다.",
        )
      : "");
}
function showAnalysis(v) {
  const job = v.data.analysis;
  $("#rp-analysis").innerHTML =
    "<small>" +
    esc(
      job
        ? "칸·태그 자동 분석 · " +
            job.status +
            (job.error ? " · " + job.error : "")
        : "",
    ) +
    "</small>" +
    (job?.status === "완료" && v.suggestions
      ? rpButton("suggest", "분석 결과 적용")
      : "");
}
function localCell(v, key) {
  return reportDrafts.get(v.id + "/" + key) || v.data.cells[key];
}
function reportControls(v) {
  const review = v.page === "template" && !v.data.ready;
  const slots = review ? v.data.slots : v.data.fields;
  if (!slots.some((s) => s.key === v.key)) v.key = slots[0]?.key;
  $("#rp-slots").innerHTML = slots
    .map((s) => {
      const f = v.fields.get(s.key);
      const title = f?.label || s.text?.slice(0, 30) || s.location;
      return (
        '<button class="slot-button ' +
        (s.key === v.key ? "selected" : "") +
        '" data-rp="slot" data-value="' +
        esc(s.key) +
        '">' +
        esc(title) +
        (review
          ? "<small>" +
            esc(s.location) +
            " · " +
            (f ? "입력 칸" : "고정") +
            "</small>"
          : v.page === "report"
            ? "<small>" +
              (localCell(v, s.key).body ? "작성됨" : "빈칸") +
              "</small>"
            : "") +
        "</button>"
      );
    })
    .join("");
  const f = v.fields.get(v.key);
  if (review) {
    const slot = v.data.slots.find((s) => s.key === v.key);
    $("#rp-editor").innerHTML =
      "<h3>" +
      esc(slot.location) +
      '</h3><p class="original-snippet">' +
      esc(slot.text || "(빈 문단)") +
      "</p>" +
      check("rp-enabled", "이 위치를 입력 칸으로 사용", !!f) +
      (f
        ? field("rp-label", "칸 이름", f.label) +
          field(
            "rp-prefix",
            "문단 앞에 남길 고정 문구",
            f.prefix,
            "text",
            false,
          ) +
          (slot.text.trim()
            ? check(
                "rp-source",
                "기존 내용을 업무 자료로 보관",
                f.keep_as_source !== false,
              )
            : "")
        : "");
  } else if (v.page === "report") {
    const c = localCell(v, v.key);
    $("#rp-editor").innerHTML =
      "<h2>" +
      esc(f.label) +
      "</h2>" +
      area("rp-body", "이 칸의 내용", c.body, false) +
      area("rp-instruction", "추가 지시사항", c.instruction, false) +
      '<div class="bar">' +
      rpButton("save", "칸 저장") +
      rpButton("generate", c.body ? "이 칸 재작성" : "이 칸 생성", "", true) +
      '</div><div id="rp-job"></div>' +
      (c.refs || [])
        .map((r) => button("ref", r.name + " · " + r.location, r.id))
        .join("");
    reportJobState(v);
  } else
    $("#rp-editor").innerHTML =
      "<h3>" +
      esc(f?.label || "") +
      "</h3><p>이 양식으로 보고서를 만들면 칸별로 작성할 수 있습니다.</p>";
  paintReport(v);
}
function reportJobState(v) {
  const j = v.data.jobs.find((j) => j.key === v.key);
  const draft = reportDrafts.get(v.id + "/" + v.key);
  $("#rp-job").innerHTML =
    (draft ? "<small>저장 전 변경 사항</small> " : "") +
    (j
      ? "<small>" +
        esc(j.status + (j.error ? " · " + j.error : "")) +
        "</small>"
      : "") +
    (draft && draft.version !== v.data.cells[v.key].version
      ? rpButton("reload-cell", "서버에 저장된 내용 불러오기")
      : "");
}
async function reportPreview(v) {
  const frame = $("#rp-frame"),
    template = v.data.template || v.id;
  const ticket = (v.previewTicket = (v.previewTicket || 0) + 1);
  const current = () => activeReportView(v) && ticket === v.previewTicket;
  v.frame = null;
  const response = v.blankPreview
    ? await fetch("/api/reports/templates/" + template + "/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify([...v.fields.values()]),
      })
    : await fetch("/api/reports/templates/" + template + "/file?marked=true");
  if (!current()) return;
  if (!response.ok)
    throw Error(
      "양식 미리보기를 열 수 없습니다. 입력 칸과 고정 문구를 확인하세요.",
    );
  const data = await response.blob();
  if (!current()) return;
  const loaded = new Promise((resolve) =>
    frame.addEventListener("load", resolve, { once: true }),
  );
  frame.srcdoc =
    '<html lang="ko"><head><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; img-src data: blob:; font-src data: blob:"></head><body style="margin:0"><div id="paper"></div></body></html>';
  await loaded;
  if (!current()) return;
  const doc = frame.contentDocument,
    host = doc.getElementById("paper");
  await window.docx.renderAsync(data, host, null, {
    inWrapper: true,
    useBase64URL: true,
    renderAltChunks: false,
    renderComments: false,
  });
  if (!current()) return;
  const walker = doc.createTreeWalker(host, NodeFilter.SHOW_TEXT),
    nodes = [];
  while (walker.nextNode())
    if (walker.currentNode.nodeValue.includes("⟦CLICKER:"))
      nodes.push(walker.currentNode);
  for (const node of nodes) {
    const pieces = node.nodeValue.split(/(⟦CLICKER:s\d+p\d+⟧)/);
    const fragment = doc.createDocumentFragment();
    for (const piece of pieces) {
      const key = /^⟦CLICKER:(s\d+p\d+)⟧$/.exec(piece)?.[1];
      if (!key) {
        fragment.append(piece);
        continue;
      }
      const span = doc.createElement("span");
      span.dataset.slot = key;
      const p = node.parentElement.closest("p");
      if (p) p.dataset.key = key;
      fragment.append(span);
    }
    node.replaceWith(fragment);
  }
  const style = doc.createElement("style");
  style.textContent =
    ".docx-wrapper{padding:14px!important;background:#e9ede8!important;align-items:flex-start!important}p[data-key]{cursor:pointer}p[data-key].active{outline:2px solid #438469;outline-offset:2px}span[data-slot]{white-space:pre-wrap}.field-empty{background:#edf3ea;color:#466957;font:12px sans-serif}";
  doc.head.append(style);
  doc.addEventListener("click", (e) => {
    e.preventDefault();
    const key = e.target.closest("[data-key]")?.dataset.key;
    if (key) chooseSlot(v, key, false);
  });
  const fit = () => {
    const page = host.querySelector("section.docx");
    if (page?.offsetWidth)
      host.style.zoom = Math.min(
        1,
        (frame.clientWidth - 4) / (page.offsetWidth + 28),
      );
  };
  frame.contentWindow.addEventListener("resize", fit);
  fit();
  v.frame = doc;
  paintReport(v);
}
function paintReport(v) {
  if (!v.frame) return;
  v.frame.querySelectorAll("[data-slot]").forEach((span) => {
    const key = span.dataset.slot,
      f = v.fields.get(key);
    const body = v.page === "report" ? localCell(v, key)?.body : "";
    const text =
      v.page === "template" && !v.data.ready
        ? f
          ? " 〔입력 칸〕"
          : ""
        : body || (f ? "〔" + f.label + "〕" : "");
    if (span.textContent !== text) span.textContent = text;
    span.classList.toggle("field-empty", !!f && !body);
    span.closest("p")?.classList.toggle("active", key === v.key);
  });
  const pages = [...v.frame.querySelectorAll("section.docx")];
  const overflow = pages.some(
    (p) =>
      p.scrollWidth > p.clientWidth + 2 ||
      (p.style.minHeight &&
        p.offsetHeight >
          parseFloat(v.frame.defaultView.getComputedStyle(p).minHeight) + 4),
  );
  $("#rp-preview-status").textContent =
    (v.blankPreview ? "비운 양식 미리보기" : "양식 미리보기") +
    (overflow
      ? " · 쪽 크기를 넘는 내용이 있습니다. Word에서 페이지 배치를 확인하세요."
      : "");
}
function chooseSlot(v, key, scroll = true) {
  v.key = key;
  reportControls(v);
  if (scroll)
    v.frame
      ?.querySelector('[data-key="' + CSS.escape(key) + '"]')
      ?.scrollIntoView({ block: "nearest" });
}
async function saveReportCell(v, key, generate) {
  const c = localCell(v, key);
  const r = await api("reports/" + v.id + "/cells/" + key, "PUT", {
    body: c.body,
    instruction: c.instruction,
    version: c.version,
    generate,
  });
  v.data.cells[key] = { ...c, version: r.version };
  const draft = reportDrafts.get(v.id + "/" + key);
  if (draft === c) reportDrafts.delete(v.id + "/" + key);
  else if (draft) draft.version = r.version;
  if (r.job)
    v.data.jobs = [
      { key, id: r.job, status: "대기" },
      ...v.data.jobs.filter((j) => j.key !== key),
    ];
}
document.addEventListener("input", (e) => {
  const v = reportView,
    el = e.target;
  if (!v || !activeReportView(v)) return;
  if (el.id === "rp-search") return reportList(v, el.value);
  if (el.name === "rp-enabled") {
    if (el.checked)
      v.fields.set(v.key, {
        key: v.key,
        label: v.data.slots.find((s) => s.key === v.key).location,
        prefix: "",
      });
    else v.fields.delete(v.key);
    v.edited = true;
    reportControls(v);
    if (v.blankPreview) reportPreview(v).catch((e) => toast(e.message));
  }
  if (["rp-label", "rp-prefix", "rp-tags", "rp-name"].includes(el.name)) {
    v.edited = true;
    if (el.name === "rp-label") v.fields.get(v.key).label = el.value;
    if (el.name === "rp-prefix") v.fields.get(v.key).prefix = el.value;
    if (el.name === "rp-prefix" && v.blankPreview) {
      clearTimeout(v.previewTimer);
      v.previewTimer = setTimeout(() => {
        if (activeReportView(v))
          reportPreview(v).catch((e) => toast(e.message));
      }, 500);
    }
  }
  if (el.name === "rp-source") {
    v.fields.get(v.key).keep_as_source = el.checked;
    v.edited = true;
  }
  if (["rp-body", "rp-instruction"].includes(el.name)) {
    const c = localCell(v, v.key);
    reportDrafts.set(v.id + "/" + v.key, {
      ...c,
      body: $('[name="rp-body"]').value,
      instruction: $('[name="rp-instruction"]').value,
      refs: el.name === "rp-body" ? [] : c.refs,
    });
    paintReport(v);
    reportJobState(v);
  }
});
async function reportAction(act, id, v) {
  if (act === "toggle-preview") {
    v.blankPreview = !v.blankPreview;
    $('[data-rp="toggle-preview"]').textContent = v.blankPreview
      ? "원본 미리보기"
      : "비운 양식 미리보기";
    return reportPreview(v);
  }
  if (act === "slot") return chooseSlot(v, id);
  if (act === "source") {
    await refresh();
    return actions("doc", id);
  }
  if (act === "upload-template")
    return modal(
      "보고서 양식 등록",
      rpForm(
        "upload",
        '<label>기존 보고서 또는 빈 양식 (.docx)<input required name="file" type="file" accept=".docx"></label><small>등록할 입력 칸과 보존할 문구를 다음 화면에서 확인합니다.</small>',
        "양식 열기",
      ),
    );
  if (act === "material")
    return modal(
      "업무 자료 등록",
      rpForm(
        "material",
        '<label>자료 파일<input name="file" type="file" accept=".txt,.md,.docx,.pdf,.pptx,.html,.htm,.markdown"></label>' +
          area("text", "또는 업무 내용 붙여넣기", "", false),
        "자료 등록",
      ),
    );
  if (act === "new") {
    const data = await api("reports");
    const ready = data.templates.filter((t) => t.ready);
    if (!ready.length)
      return modal(
        "새 보고서",
        "<p>먼저 양식을 등록하고 입력 칸을 확인하세요.</p>" +
          rpButton("upload-template", "양식 등록"),
      );
    return modal(
      "새 보고서",
      rpForm(
        "new",
        select(
          "template",
          "보고서 양식",
          ready
            .map((t) => option(t.id, t.name + " · " + t.tags.join(", "), id))
            .join(""),
        ) + field("title", "보고서 제목"),
        "작성 시작",
      ),
    );
  }
  if (act === "confirm") {
    const fields = [...v.fields.values()],
      name = $('[name="rp-name"]').value.trim(),
      tags = splitTags($('[name="rp-tags"]').value);
    if (!fields.length || !name || !tags.length)
      throw Error(
        "입력 칸을 하나 이상 지정하고 양식 이름과 태그를 입력하세요.",
      );
    await api("reports/templates/" + v.id + "/confirm", "POST", {
      name,
      tags,
      fields,
    });
    toast("빈 양식을 등록했습니다.");
    location.hash = "reports";
    return;
  }
  if (act === "suggest") {
    v.fields = new Map(v.suggestions.fields.map((f) => [f.key, { ...f }]));
    $('[name="rp-tags"]').value = v.suggestions.tags.join(", ");
    v.suggestions = null;
    showAnalysis(v);
    reportControls(v);
    if (v.blankPreview) await reportPreview(v);
    return;
  }
  if (act === "reload-cell") {
    reportDrafts.delete(v.id + "/" + v.key);
    return reportControls(v);
  }
  if (act === "export") {
    for (const f of v.data.fields)
      if (reportDrafts.has(v.id + "/" + f.key))
        await saveReportCell(v, f.key, false);
    const a = document.createElement("a");
    a.href = "/api/reports/" + v.id + "/file";
    a.download = "";
    a.click();
    return;
  }
  if (act === "save" || act === "generate") {
    await saveReportCell(v, v.key, act === "generate");
    if (activeReportView(v)) reportControls(v);
    toast(
      act === "save"
        ? "칸을 저장했습니다."
        : "선택한 칸의 작성을 요청했습니다.",
    );
  }
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-rp]");
  if (!b) return;
  b.disabled = true;
  try {
    await reportAction(b.dataset.rp, b.dataset.value, reportView);
  } catch (error) {
    toast(error.message);
  } finally {
    b.disabled = false;
  }
});
document.addEventListener("submit", async (e) => {
  const f = e.target;
  if (!f.dataset.rpForm) return;
  e.preventDefault();
  const b = f.querySelector("[type=submit]");
  b.disabled = true;
  try {
    const data = new FormData(f);
    if (f.dataset.rpForm === "upload") {
      const r = await api("reports/templates", "POST", data);
      close();
      location.hash = "template/" + r.id;
    } else if (f.dataset.rpForm === "new") {
      const r = await api("reports", "POST", Object.fromEntries(data));
      close();
      location.hash = "report/" + r.id;
    } else {
      if (!data.get("file")?.size) data.delete("file");
      await api("reports/materials", "POST", data);
      close();
      await reportWorkspace("report-data");
      toast("업무 자료를 등록했습니다.");
    }
  } catch (error) {
    toast(error.message);
  } finally {
    b.disabled = false;
  }
});
setInterval(async () => {
  const v = reportView;
  if (!v || !activeReportView(v) || v.polling) return;
  v.polling = true;
  try {
    if (v.page === "report-data" && v.materials) {
      const rows = await api("reports/materials");
      if (activeReportView(v) && JSON.stringify(rows) !== JSON.stringify(v.materials)) {
        v.materials = rows;
        reportList(v, $("#rp-search").value);
      }
      return;
    }
    if (!v.data || !$("#rp-editor")) return;
    if (
      v.page === "template" &&
      !v.data.ready &&
      v.data.analysis && v.data.analysis.status !== "완료"
    ) {
      const j = await api("jobs/" + v.data.analysis.id);
      if (!activeReportView(v)) return;
      v.data.analysis = { ...v.data.analysis, ...j };
      if (j.status === "완료") {
        const latest = await api("reports/templates/" + v.id);
        if (!activeReportView(v)) return;
        if (v.edited) v.suggestions = latest;
        else {
          v.fields = new Map(latest.fields.map((f) => [f.key, { ...f }]));
          $('[name="rp-tags"]').value = latest.tags.join(", ");
          reportControls(v);
          if (v.blankPreview) await reportPreview(v);
        }
      }
      showAnalysis(v);
    } else if (v.page === "report") {
      const next = await api("reports/" + v.id);
      if (!activeReportView(v)) return;
      const changed = next.cells[v.key].version !== v.data.cells[v.key].version;
      v.data = next;
      if (changed && !reportDrafts.has(v.id + "/" + v.key)) reportControls(v);
      else {
        paintReport(v);
        reportJobState(v);
      }
    }
  } catch (error) {
    if (activeReportView(v)) toast(error.message);
  } finally {
    v.polling = false;
  }
}, 3000);
window.addEventListener("beforeunload", (e) => {
  if (reportDrafts.size) {
    e.preventDefault();
    e.returnValue = "";
  }
});
