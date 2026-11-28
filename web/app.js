"use strict";
const $ = (s) => document.querySelector(s);
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const by = (list, id) => list.find((x) => x.id === id);
const resultEdits = new Map();
const newBlock = () => ({block_id: crypto.randomUUID(), body: "", paragraph: null});
const selectedKey = q => "clicker:block:" + q.id;
function targetBlock(q, blocks = readBlocks()) {
  return blocks.find(b => b.block_id === sessionStorage.getItem(selectedKey(q))) ||
    blocks.find(b => !b.body.trim() && !b.paragraph);
}
function targetNumber(q) {
  const blocks = readBlocks(), block = targetBlock(q, blocks);
  return block ? blocks.indexOf(block) + 1 : blocks.length + 1;
}
function highlightTarget(q) {
  const id = targetBlock(q)?.block_id;
  document.querySelectorAll(".draft").forEach(t =>
    t.closest(".item").classList.toggle("selected-block", t.dataset.block === id));
  if ($("#writing-target")) $("#writing-target").textContent = targetNumber(q) + "번 문단 작성";
}
function selectBlock(q, id) {
  sessionStorage.setItem(selectedKey(q), id);
  highlightTarget(q);
  syncWritingLimits(q);
  if ($("#result")) drawSelectedResult(q);
  if ($("#library") && tool === "보관함") $("#library").innerHTML = libraryRows($("#filter").value, true);
}
function prepareTarget(q) {
  const blocks = readBlocks();
  let block = targetBlock(q, blocks);
  if (!block) {
    block = newBlock();
    blocks.push(block);
  }
  mark(q, blocks);
  if (!$('.draft[data-block="' + block.block_id + '"]')) drawBlocks(q);
  sessionStorage.setItem(selectedKey(q), block.block_id);
  highlightTarget(q);
  return block.block_id;
}
const jobPriority = (status) => ({ 실행: 0, 대기: 1, 보류: 2 }[status] ?? 3);
const button = (action, label, id = "", primary = false, disabled = false) =>
  '<button type="button" data-action="' +
  action +
  '" data-id="' +
  esc(id) +
  '" class="' +
  (primary ? "primary" : "") +
  '"' + (disabled ? " disabled" : "") + ">" +
  esc(label) +
  "</button>";
const field = (name, title, value = "", type = "text", req = true) =>
  "<label>" +
  esc(title) +
  '<input name="' +
  name +
  '" type="' +
  type +
  '" value="' +
  esc(value) +
  '" ' +
  (req ? "required" : "") +
  "></label>";
const area = (name, title, value = "", req = true) =>
  "<label>" +
  esc(title) +
  '<textarea name="' +
  name +
  '" ' +
  (req ? "required" : "") +
  ">" +
  esc(value) +
  "</textarea></label>";
const option = (id, name, chosen) =>
  '<option value="' +
  esc(id) +
  '" ' +
  (id === chosen ? "selected" : "") +
  ">" +
  esc(name) +
  "</option>";
const select = (name, title, options) =>
  "<label>" +
  esc(title) +
  '<select name="' +
  name +
  '">' +
  options +
  "</select></label>";
const check = (name, title, on = false) =>
  '<label class="inline"><input type="checkbox" name="' +
  name +
  '" ' +
  (on ? "checked" : "") +
  "> " +
  esc(title) +
  "</label>";
const form = (action, html, label, id = "") =>
  '<form data-form="' +
  action +
  '" data-id="' +
  esc(id) +
  '">' +
  html +
  '<button class="primary" type="submit">' +
  esc(label) +
  "</button></form>";
const empty = (title, desc, action = "") =>
  '<div class="empty"><h2>' +
  esc(title) +
  "</h2><p>" +
  esc(desc) +
  '</p><div class="bar">' +
  action +
  "</div></div>";
let S = {
  applications: [],
  questions: [],
  documents: [],
  experiences: [],
  facts: [],
  tags: [],
  links: [],
  paragraphs: [],
  jobs: [],
};
let tab = "원본 문서",
  tool = "생성",
  writingMode = null,
  rewrite = null,
  viewResult = null,
  current = null,
  saveTimer = null,
  saveChain = Promise.resolve(),
  toastTimer;
let dirty = new Map(),
  generationContext = null;
async function api(path, method = "GET", body) {
  let options = { method };
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const r = await fetch("/api/" + path, options);
  const data = await r.json();
  if (!r.ok)
    throw Error(
      typeof data.detail === "string" ? data.detail : "입력값을 확인해 주세요.",
    );
  return data;
}
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.style.display = "block";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.style.display = "none"), 4500);
}
function modal(title, html) {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = html;
  if (!$("#modal").open) $("#modal").showModal();
}
function close() {
  if ($("#modal").open) $("#modal").close();
}
async function refresh() {
  S = await api("state");
  $("#busy").textContent =
    S.jobs.filter((j) => ["대기", "실행"].includes(j.status)).length || "";
}
const route = () => location.hash.slice(1).split("/");
const qNow = () => by(S.questions, route()[1]);
const appOf = (q) => by(S.applications, q.app);
const qs = (aid) => S.questions.filter((q) => q.app === aid);
const chars = (text, spaces) =>
  Array.from((spaces ? text : text.replace(/\s/g, "")).normalize("NFC")).length;
const joined = (q) =>
  (dirty.get(q.id) || q.blocks).map((b) => b.body).join("\n\n");
let modelScreenId = 0, modelTimer, modelRead = 0;
async function openModels() {
  clearTimeout(modelTimer);
  const screen = ++modelScreenId;
  $("#main").innerHTML = '<div class="head"><h1>모델 설정</h1>' +
    button("settings", "새로고침") + '</div><div class="model-layout">' +
    '<section class="panel"><h2>사용할 모델</h2><div id="model-choices" aria-live="polite">모델 목록을 불러오는 중…</div></section>' +
    '<section class="panel"><h2>모델 추가</h2>' +
    form("model-download", field("name", "레지스트리 모델 이름") +
      '<small>예: bge-m3 또는 조직/모델:태그</small>', "다운로드 및 설치") +
    '<details><summary>GGUF 파일로 등록</summary>' +
    form("model-import", field("name", "새 모델 이름") +
      '<label>모델 파일<input type="file" name="file" accept=".gguf" required></label>', "파일 등록") +
    '</details></section></div><section class="panel model-history"><h2>모델 작업</h2><div id="model-jobs"></div></section>';
  await syncModels(screen);
}
async function syncModels(screen) {
  clearTimeout(modelTimer);
  const read = ++modelRead;
  const current = () => screen === modelScreenId && read === modelRead && route()[0] === "models";
  try {
    const data = await api("models");
    if (!current()) return;
    const a = data.active, node = $("#model-choices");
    const busy = j => ["대기", "실행", "보류"].includes(j.status);
    const switching = data.jobs.some(j => j.kind === "configure_models" && busy(j));
    const stamp = JSON.stringify([a, data.installed, data.error, switching]);
    if (node.dataset.stamp !== stamp) {
      const prior = node.querySelector('form');
      const keep = prior && Number(prior.dataset.id) === a.version ? new FormData(prior) : null;
      const choices = (name, same = false) => {
        const normalized = name.replace(/:latest$/, "");
        const missing = normalized && !data.installed.includes(normalized)
          ? '<option value="" disabled selected>' + esc(name + " · 설치 필요") + "</option>" : "";
        return (same ? option("", "작성 모델과 동일", name) : "") + missing +
          data.installed.map(n => option(n, n, normalized)).join("");
      };
      node.innerHTML = '<p class="note">현재 작성 · ' + esc(a.generation) + '<br>분석·태깅 · ' + esc(a.analysis) +
        '<br>자료 검색 · ' + esc(a.embedding) + (a.dimensions ? " (" + esc(a.dimensions) + "차원)" : "") + "<br>pgvector " + esc(data.pgvector) + "</p>" +
        (data.error ? '<p role="alert">' + esc(data.error) + "</p>" : !data.installed.length
          ? '<p>서버에 설치된 모델이 없습니다. 모델을 먼저 추가해 주세요.</p>'
          : form("model-select",
            select("generation", "문단 작성 LLM", choices(keep?.get("generation") || a.generation)) +
            select("analysis", "경험 분석·태그 추출 LLM", choices(keep?.get("analysis") ?? (a.analysis === a.generation ? "" : a.analysis), true)) +
            select("embedding", "자료 검색 임베딩 모델", choices(keep?.get("embedding") || a.embedding)) +
            '<small>검색 모델을 바꾸면 기존 자료를 다시 색인합니다. 준비가 끝나면 새 설정이 적용됩니다.</small>',
            "검증 후 적용", a.version));
      const apply = node.querySelector("[type=submit]");
      if (apply) {
        apply.disabled = switching;
        for (const sel of node.querySelectorAll("select")) {
          if (sel.name !== "analysis") sel.required = true;
        }
      }
      node.dataset.stamp = stamp;
    }
    $("#model-jobs").innerHTML = data.jobs.map(j => {
      const title = j.kind === "install_model" ? "설치 · " + j.args.name :
        "검증 및 적용 · " + [j.args.generation, j.args.analysis, j.args.embedding].filter((n,i,v) => v.indexOf(n) === i).join(" / ");
      const info = j.error || (j.result?.dimensions ? j.result.dimensions + "차원 · 재색인 " + j.result.reindexed + "건" : "");
      return '<div class="item"><b>' + esc(title) + '</b> <span class="badge">' + esc(j.status) +
        '</span>' + (info ? "<p>" + esc(info) + "</p>" : "") + "</div>";
    }).join("") || '<p class="muted">요청한 모델 작업이 없습니다.</p>';
    if (data.jobs.some(busy)) modelTimer = setTimeout(() => { if (current()) syncModels(screen); }, 2000);
  } catch (e) {
    if (current()) $("#model-jobs").textContent = e.message + " · 새로고침으로 다시 확인해 주세요.";
  }
}
function render() {
  const [page, id] = route();
  current = page === "write" ? id : null;
  const inReports = ["reports","report","template","report-data"].includes(page);
  $("#nav-home").className = ["essays","write"].includes(page) ? "active" : "";
  $("#nav-reports").className = inReports ? "active" : "";
  $("#nav-data").href = inReports ? "#report-data" : "#data";
  $("#nav-data").textContent = inReports ? "보고서 자료" : "내 자료";
  $("#nav-data").className = ["data","report-data"].includes(page) ? "active" : "";
  if (inReports) {
    const requested = location.hash;
    return reportWorkspace(page,id).catch(e => {
      if (location.hash === requested) {
        toast(e.message);
        if (!$("#rp-editor")) $("#main").innerHTML = '<p>' + esc(e.message) + '</p><a href="#reports">보고서 목록으로</a>';
      }
    });
  }
  if (!page || page === "home") {
    $("#main").innerHTML = '<div class="head"><h1>문서 작업실</h1></div><div class="workspace-choices">' +
      '<a class="card" href="#essays"><h2>자소서 작성</h2><p>경험과 자료를 바탕으로 지원 문항별 초안을 작성합니다.</p></a>' +
      '<a class="card" href="#reports"><h2>보고서 작성</h2><p>등록한 양식에 업무 자료를 반영해 보고서를 작성합니다.</p></a></div>';
    return;
  }
  if (page === "models") return openModels();
  if (page === "data") return renderData();
  if (page === "write" && qNow()) return renderWriter(qNow());
  $("#main").innerHTML =
    '<div class="head"><h1>내 자소서</h1>' +
    button("new-app", "새 자소서", "", true) +
    "</div>" +
    (S.applications.length
      ? '<div class="grid">' +
        S.applications
          .map((a) => {
            let list = qs(a.id);
            return (
              '<a class="card" href="#write/' +
              list[0]?.id +
              '"><small>' +
              esc(a.company) +
              "</small><h2>" +
              esc(a.role) +
              "</h2><p>" +
              list.length +
              "개 문항 · " +
              list.filter((q) => joined(q).trim()).length +
              "개 작성 중</p></a>"
            );
          })
          .join("") +
        "</div>"
      : empty(
          "첫 자기소개서를 작성하세요",
          "지원할 기업과 직무, 첫 문항을 등록하면 작성 화면이 열립니다.",
          button("new-app", "새 자소서 만들기"),
        ));
}
const tagNames = (kind, id, state) =>
  S.links
    .filter(
      (l) =>
        l.target_type === kind &&
        l.target === id &&
        (state ? l.state === state : l.state !== "제외"),
    )
    .map((l) => by(S.tags, l.tag)?.name)
    .filter(Boolean);
function documentRows(search = "") {
  const query = search.trim().toLowerCase();
  return S.documents
        .filter(d => !d.app && d.kind !== "보고서 자료" && [d.name, d.kind, ...(d.metadata.folder_paths || [])]
          .some(value => value.toLowerCase().includes(query)))
        .map(
          (d) =>
            '<div class="item"><div class="row"><div><b>' +
            esc(d.name) +
            '</b> <span class="badge">' +
            esc(d.kind + " · " + d.status) +
            "</span></div>" +
            button("doc", "원문", d.id) +
            "</div></div>",
        )
        .join("") || (query ? "<p>일치하는 자료가 없습니다.</p>" :
        empty("등록한 원본이 없습니다", "파일이나 폴더를 선택하거나 텍스트를 붙여넣으세요."));
}
function dataList() {
  if (tab === "저장 문단") return library(false);
  if (tab === "원본 문서") {
    const total = S.documents.filter(d => !d.app && d.kind !== "보고서 자료").length;
    return '<label>원본 문서 ' + total + '건<input type="search" id="doc-search" placeholder="파일명, 폴더 또는 자료 유형 검색"></label>' +
      '<div id="document-list">' + documentRows() + '</div>';
  }
  const items = S.facts;
  return (
    items
      .map(
        (f) =>
          '<div class="item"><div class="row"><div><span class="badge">' +
          esc(f.status) +
          "</span> <b>" +
          esc(f.title) +
          "</b></div>" +
          button("fact", "검토·수정", f.id) +
          "</div><p>" +
          esc(f.body) +
          "</p><small>" +
          esc(tagNames("fact", f.id).join(" · ")) +
          "</small></div>",
      )
      .join("") ||
    empty(
      "경험을 모아 두세요",
      "이력서와 과거 자소서를 등록하거나 경험을 직접 입력하세요.",
    )
  );
}
function renderData() {
  $("#main").innerHTML =
    '<div class="head"><h1>내 자료</h1><div class="bar">' +
    button("tags", "태그 관리") +
    button("direct", "경험 직접 입력") +
    button("upload", "문서 등록", "", true) +
    '</div></div><div class="tabs">' +
    ["경험", "원본 문서", "저장 문단"]
      .map((t) => button("tab", t, t, t === tab))
      .join("") +
    '</div><section class="panel">' +
    dataList() +
    "</section>";
}
function renderWriter(q) {
  const a = appOf(q);
  $("#main").innerHTML =
    '<div class="head"><div><small>' +
    esc(a.company) +
    "</small><h1>" +
    esc(a.role) +
    '</h1></div><div class="bar">' +
    button("preview", "원고 미리보기") +
    button("save", "원고 저장", "", true) +
    '</div></div><div class="work"><aside class="panel side"><h2>문항</h2>' +
    qs(a.id)
      .map((it, n) =>
        button("goto", String(n + 1) + ". " + it.title, it.id, it.id === q.id),
      )
      .join("") +
    button("add-q", "+ 문항 추가") +
    '</aside><section class="panel"><h2>' +
    esc(q.title) +
    '</h2><div id="blocks"></div><div class="bar">' +
    button("block-add", "+ 다음 문단 추가") +
    '<small id="count"></small></div><small id="saved"></small></section><aside class="panel right"><div class="tabs">' +
    ["생성", "보관함", "기업 자료"]
      .map((t) => button("tool", t, t, t === tool))
      .join("") +
    '</div><div id="tools"></div></aside></div>';
  drawBlocks(q);
  drawTool(q);
}
function drawBlocks(q) {
  let blocks = dirty.get(q.id) || q.blocks;
  if (!blocks.length) blocks = [newBlock()];
  for (const b of blocks) b.block_id ||= crypto.randomUUID();
  $("#blocks").innerHTML = blocks
    .map(
      (b, n) =>
        '<div class="item draft-block"><label><strong>문단 ' + (n + 1) +
        '</strong><textarea class="draft" data-block="' + esc(b.block_id) + '" aria-label="답변 문단 ' +
        (n + 1) +
        '" data-paragraph="' +
        esc(b.paragraph || "") +
        '" data-minimum="' + esc(b.minimum) + '" data-maximum="' + esc(b.maximum) +
        '" placeholder="이 문항의 답변을 작성하세요.">' +
        esc(b.body) +
        '</textarea></label><div class="bar"><small class="block-count"></small>' +
        button("block-limits", "글자 수 설정", n) +
        (n ? button("up", "위로", n) : "") +
        (n < blocks.length - 1 ? button("down", "아래로", n) : "") +
        button("remove-block", "빼기", n) +
        "</div></div>",
    )
    .join("");
  countView(q);
  highlightTarget(q);
}
function readBlocks() {
  return [...document.querySelectorAll(".draft")].map((t) => ({
    body: t.value,
    block_id: t.dataset.block,
    paragraph: t.dataset.paragraph || null,
    minimum: t.dataset.minimum ? Number(t.dataset.minimum) : null,
    maximum: t.dataset.maximum ? Number(t.dataset.maximum) : null,
  }));
}
function countView(q) {
  const blocks = readBlocks();
  document.querySelectorAll(".block-count").forEach((label, i) => {
    const {body, minimum, maximum} = blocks[i];
    const length = chars(body, q.spaces);
    const target = minimum != null && maximum != null ? minimum + "~" + maximum + "자"
      : maximum != null ? "최대 " + maximum + "자" : minimum != null ? "최소 " + minimum + "자" : "";
    const difference = maximum != null && length > maximum ? (length - maximum) + "자 초과"
      : minimum != null && length < minimum ? (minimum - length) + "자 부족" : "";
    label.textContent = [length + "자", target, difference].filter(Boolean).join(" · ");
    label.classList.toggle("error", Boolean(difference));
  });
  const n = chars(
    blocks
      .map((b) => b.body)
      .join("\n\n"),
    q.spaces,
  );
  $("#count").textContent =
    n + " / " + q.max_chars + "자 · 공백 " + (q.spaces ? "포함" : "제외");
  $("#count").className = n > q.max_chars ? "error" : "";
}
function mark(q, blocks) {
  dirty.set(q.id, blocks);
  if ($("#saved")) $("#saved").textContent = "저장 중…";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => flush().catch((e) => toast(e.message)), 700);
}
function flush() {
  clearTimeout(saveTimer);
  const save = async () => {
    for (const [id, blocks] of [...dirty]) {
      const q = by(S.questions, id);
      if (!q) continue;
      const result = await api("questions/" + id, "PUT", {
        version: q.rev,
        blocks,
      });
      q.rev = result.rev;
      q.blocks = blocks;
      if (dirty.get(id) === blocks) dirty.delete(id);
      if (current === id && $("#saved")) $("#saved").textContent = "저장됨";
    }
  };
  saveChain = saveChain.catch(() => {}).then(save);
  return saveChain;
}
function library(inWriter) {
  return (
    '<label>저장 문단 검색<input id="filter" placeholder="경험, 태그, 기업, 본문"></label><div id="library">' +
    libraryRows("", inWriter) +
    "</div>"
  );
}
function libraryRows(query, inWriter) {
  const list = S.paragraphs
    .filter((p) => p.saved)
    .map((p) => {
      let q = by(S.questions, p.question),
        a = appOf(q);
      return { p, q, a };
    })
    .filter(({ p, a }) =>
      [
        p.body,
        a.company,
        by(S.experiences, p.experience)?.name,
        ...p.details.tags,
        ...tagNames("paragraph", p.id),
      ]
        .join(" ")
        .includes(query),
    );
  return (
    list
      .map(
        ({ p, q, a }) =>
          '<div class="item"><small>' +
          esc(a.company + " · " + q.title) +
          "</small><p>" +
          esc(p.body) +
          '</p><div class="bar">' +
          (inWriter
            ? button("insert", targetNumber(qNow()) + "번 문단에 적용", p.id) +
              (p.details.mode === "free" ? "" : button("rewrite", "현재 기업에 맞게 다시 쓰기", p.id))
            : "") +
          button("inspect", "문단 열기", p.id) +
          "</div></div>",
      )
      .join("") || '<p class="muted">조건에 맞는 저장 문단이 없습니다.</p>'
  );
}
function drawTool(q) {
  if (tool === "기업 자료") {
    const research = S.jobs.find(j => j.kind === "research_company" && j.args.application === q.app);
    const busy = research && ["대기", "실행", "보류"].includes(research.status);
    const outcome = research?.result;
    $("#tools").innerHTML =
      "<h2>기업 자료</h2>" +
      button("company-doc", "자료 등록") + button("company-research", "자료 조사", q.app, false, busy) +
      (research ? '<p class="note" role="status">' + esc(research.status === "완료"
        ? "조사 완료 · 새 자료 " + (outcome?.documents?.length || 0) + "건 · 이미 등록 " + (outcome?.duplicates || 0) + "건"
        : "기업 자료 조사 " + research.status + (research.error ? " · " + research.error : "")) + "</p>" : "") +
      S.documents
        .filter((d) => d.app === q.app)
        .map(
          (d) =>
            '<div class="item"><small>' +
            esc(d.kind) +
            "</small><p>" +
            esc(d.name) +
            "</p>" +
            button("doc", "원문", d.id) +
            "</div>",
        )
        .join("");
    return;
  }
  if (tool === "보관함") {
    $("#tools").innerHTML = library(true);
    return;
  }
  if (!generationContext) {
    const active = S.jobs.find(j => j.kind === "generate" && j.args.question === q.id &&
      ["대기", "실행"].includes(j.status));
    if (active) {
      generationContext = {job: active.id, q: q.id, source: active.args.revise_from,
        body: by(S.paragraphs, active.args.previous)?.body};
    }
  }
  $("#tools").innerHTML = '<div id="writing-options"></div>' +
    '<p class="note" role="status" id="generation-progress"></p><div id="result"></div>';
  drawWritingOptions(q);
  drawSelectedResult(q);
}
function drawSelectedResult(q) {
  const blocks = readBlocks(), block = targetBlock(q, blocks);
  const matches = p => p && p.question === q.id && (p.details.target_block
    ? p.details.target_block === block?.block_id
    : p.id === block?.paragraph || (blocks.length === 1 && !blocks[0].body && !blocks[0].paragraph));
  const viewed = by(S.paragraphs, viewResult);
  const p = (matches(viewed) ? viewed : null) || S.paragraphs.find(matches);
  $("#result").innerHTML = p ? resultHtml(p) : "";
  const edit = p && resultEdits.get(p.id);
  if (edit !== undefined && p) {
    const editor = $("#result [name=result]");
    editor.value = edit;
    editor.dispatchEvent(new Event("input", {bubbles:true}));
  }
}
function drawWritingOptions(q) {
  const prev = by(S.paragraphs, rewrite);
  const free = (writingMode || (S.experiences.length || S.documents.some(d => !d.app) ? "evidence" : "free")) === "free";
  const heading = '<h2 id="writing-target">' + targetNumber(q) + "번 문단 작성</h2>" +
    '<div class="tabs">' + button("writing-mode", "자료 기반 작성", "evidence", !free) +
    button("writing-mode", "자유 작성", "free", free) + "</div>";
  const options = $("#writing-options");
  const target = nextWritingLimits(q);
  const limits = '<div class="pair">' +
    field("minimum", "최소 글자 수", target.minimum, "number") +
    field("maximum", "최대 글자 수", target.maximum, "number") + "</div>";
  if (free) {
    options.innerHTML = heading + '<p class="note">지원 기업·직무·문항을 바탕으로 초안을 작성합니다.</p>' +
      form("generate-free", limits + area("instruction", "추가 작성 지시", "", false), "자유 문단 생성");
    return;
  }
  const prior = prev?.details;
  options.innerHTML = heading +
    form("generate",
      area("instruction", "지시사항 입력", prior?.instruction || prior?.tags?.join(", ") || "") +
      '<p class="note">예: 시스템 통합 경험을 찾아, 문제를 어떻게 해결했는지 중심으로 써줘.</p>' +
      limits + check("narrative", "이번 문단의 서사 생성 허용"), "문단 생성") +
    '<div class="bar">' + button("direct", "경험 직접 입력") +
    (S.experiences.length ? button("recommend", "경험 추천") : "") + "</div>";
}
function nextWritingLimits(q) {
  const next = targetBlock(q);
  const minimum = next?.minimum ?? (next?.maximum != null ? 1 : Math.min(240, q.max_chars));
  const maximum = next?.maximum ?? Math.max(minimum, Math.min(300, q.max_chars));
  return {minimum: Math.min(6000, Math.max(1, minimum)), maximum: Math.min(6000, maximum)};
}
function syncWritingLimits(q) {
  const options = $("#writing-options form");
  if (!options) return;
  const target = nextWritingLimits(q);
  options.elements.minimum.value = target.minimum;
  options.elements.maximum.value = target.maximum;
}
function resultLength(count, details) {
  const within = details.minimum <= count && count <= details.maximum;
  return count + "자 · 목표 " + details.minimum + "~" + details.maximum + "자 · " +
    (within ? "목표 범위 충족" : "길이 조정 필요");
}

function resultHtml(p) {
  const free = p.details.mode === "free";
  const checks=p.details.tag_checks||[], rounds=p.details.attempts||[];
  const parent = p.previous && by(S.paragraphs, p.previous);
  const history=rounds.length?'<details><summary>검증 기록 · '+rounds.length+'회</summary>'+
    rounds.map(r=>'<div class="item"><b>'+esc(r.round)+'차 · '+esc(r.count)+'자</b><p>'+esc(r.feedback?.join(" / ")||(r.accepted?"검증 조건 충족":"검토 필요"))+'</p></div>').join("")+'</details>':"";
  const tagReport = checks.map(t => {
    const label = '<span class="badge">' + esc(t.tag + " · " + t.status) + "</span>";
    if (t.status !== "충족") return "<p>" + label + " " + esc(t.feedback) + "</p>";
    return "<details><summary>" + label + "</summary><p>문단 · " + esc(t.draft_quote) +
      "</p><p>경험 근거 · " + esc(t.source_quote) + "</p></details>";
  }).join("");
  return (
    '<article class="item"><h3>' + (free ? "자유 작성 문단" : "검토할 문단") + '</h3><small>' +
    esc(p.details.model) +
    " · " +
    '<span id="result-length">' + esc(resultLength(p.details.count, p.details)) + "</span></small>" +
    area("result", "문단 편집", p.body) +
    '<p id="result-edited" class="note"' +
    (p.details.review === "사용자 편집본 · 근거 재검토" ? "" : " hidden") +
    '>편집한 문단은 태그와 근거를 다시 확인하세요.</p>' +
    '<div id="result-assessment">' +
    (p.details.stop_reason?'<p class="note">'+esc(p.details.stop_reason)+'</p>':"")+
    tagReport+(p.details.factual_issues||[]).map(t=>'<p class="error">'+esc(t)+'</p>').join("")+history+
    (p.details.expanded.length
      ? "<p>서사 확장 · " + esc(p.details.expanded.join(" / ")) + "</p>"
      : "") +
    (p.details.terms_to_review?.length
      ? '<p class="note">원문 대조가 필요한 표현 · ' +
        esc(p.details.terms_to_review.join(", ")) +
        "</p>"
      : "") +
    "</div>" + (parent ? "<details><summary>이전 문단 보기</summary><p>" + esc(parent.body) + "</p></details>" : "") +
    (free ? "" : "<details><summary>사용한 근거</summary>" +
    p.details.references
      .map((r) => button("ref", r.name + " · " + r.location, r.id))
      .join("") +
    "</details>") + '<div class="bar">' +
    button("edited-insert", targetNumber(qNow()) + "번 문단에 적용", p.id, true) +
    button("revise", "재작성", p.id, false, generationContext?.q === p.question) +
    button("keep", "저장 문단에 보관", p.id) +
    "</div></article>"
  );
}
let importOptions, activeImport = null;

async function openImport(application) {
  if (activeImport?.done) activeImport = null;
  importOptions ||= await api("imports/options");
  modal("자료 등록",
    '<section id="quick-import" data-app="' + esc(application) + '">' +
    '<p>파일이나 폴더를 선택하면 바로 등록합니다. 자료 유형과 경험은 등록 후 자동으로 분석합니다.</p>' +
    '<label>파일 선택<input id="import-files" type="file" multiple accept="' + esc(importOptions.extensions.join(",")) + '"></label>' +
    '<label>폴더 선택<input id="import-folder" type="file" webkitdirectory multiple></label>' +
    form("import-text", area("text", "텍스트 붙여넣기"), "텍스트 등록", application) +
    '<p id="import-progress" role="status"></p><div id="import-errors"></div></section>' +
    '<details><summary>세부 정보 직접 입력</summary>' + uploadForm(Boolean(application)) + '</details>');
  showImportProgress();
}

function showImportProgress() {
  const panel = $("#quick-import");
  if (!panel || !activeImport) return;
  const s = activeImport;
  for (const input of panel.querySelectorAll("input,textarea,button")) input.disabled = !s.done;
  $("#import-progress").textContent = (s.done ? "등록 결과" : "등록 중 " + s.processed + "/" + s.total) +
    " · 등록 " + s.added + " · 중복 " + s.duplicates + " · 제외 " + s.skipped + " · 실패 " + s.errors.length;
  $("#import-errors").innerHTML = s.errors.length ? "<details><summary>실패한 파일</summary>" +
    s.errors.map(message => "<p>" + esc(message) + "</p>").join("") + "</details>" : "";
}

async function importFiles(files, application) {
  if (activeImport && !activeImport.done) return;
  const s = activeImport = {total: files.length, processed: 0, added: 0, duplicates: 0, skipped: 0, errors: [], done: false};
  const extensions = new Set(importOptions.extensions);
  const ignored = new Set(importOptions.ignored_dirs);
  showImportProgress();
  for (const file of files) {
    const path = file.webkitRelativePath || file.name;
    const parts = path.toLowerCase().split("/");
    const dot = file.name.lastIndexOf(".");
    if (parts.slice(0,-1).some(p => p.startsWith(".") || ignored.has(p)) ||
        file.name.startsWith(".") || file.name.startsWith("~$") || dot < 0 ||
        !extensions.has(file.name.slice(dot).toLowerCase())) {
      s.skipped++;
    } else {
      const data = new FormData();
      data.set("file", file);
      data.set("relative_path", path);
      if (application) data.set("application", application);
      try {
        const result = await api("imports", "POST", data);
        if (result.status === "중복") s.duplicates++;
        else if (result.status === "제외") s.skipped++;
        else s.added++;
      } catch (error) { s.errors.push(path + " · " + error.message); }
    }
    s.processed++;
    if (s.processed % 25 === 0 || s.processed === files.length || !s.skipped) showImportProgress();
  }
  s.done = true;
  showImportProgress();
  await refresh();
  if (route()[0] === "data") { tab = "원본 문서"; render(); }
  if (tool === "기업 자료" && qNow()) drawTool(qNow());
  toast("등록 " + s.added + "건 · 중복 " + s.duplicates + "건 · 실패 " + s.errors.length + "건");
}

document.addEventListener("change", e => {
  if (!e.target.matches("#import-files,#import-folder") || !e.target.files.length) return;
  const files = Array.from(e.target.files);
  e.target.value = "";
  importFiles(files, $("#quick-import").dataset.app).catch(err => toast(err.message));
});

function uploadForm(company) {
  return form(
    "upload",
    field("name", "자료 이름", "", "text", false) +
      (company
        ? select(
            "kind",
            "유형",
            ["공고·직무", "사업", "인재상", "문화", "사용자 메모"]
              .map((v) => option(v, v))
              .join(""),
          ) +
          field("source", "출처 URL 또는 문서명", "", "text", false) +
          field(
            "checked",
            "확인일",
            new Date().toLocaleDateString("sv-SE"),
            "date",
            false,
          )
        : select(
            "kind",
            "유형",
            ["이력서", "과거 자소서", "연구·활동 기록"]
              .map((v) => option(v, v))
              .join(""),
          ) +
          field("source", "과거 지원 기업 또는 출처", "", "text", false) +
          field("past_role", "과거 직무", "", "text", false) +
          field("past_question", "과거 문항", "", "text", false)) +
      '<label>파일<input type="file" name="file" accept=".txt,.docx,.pdf"></label>' +
      area("text", "또는 텍스트 붙여넣기", "", false),
    "등록",
    company ? qNow().app : "",
  );
}
function factForm(f) {
  return form(
    "fact",
    field("title", "활동명", f?.title) +
      select(
        "experience",
        "연결할 경험",
        option("", "새 경험 만들기") +
          S.experiences
            .map((e) => option(e.id, e.name, f?.experience))
            .join(""),
      ) +
      area("body", "확인한 행동과 결과", f?.body) +
      field(
        "tags",
        "확정할 태그 (쉼표로 구분)",
        f ? tagNames("fact", f.id).join(", ") : "",
        "text",
        false,
      ) +
      (f ? f.refs.map((r) => button("ref", "원문 보기", r)).join("") : ""),
    "확정 저장",
    f?.id || "",
  );
}
const splitTags = (t) =>
  t
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
document.addEventListener("focusin", e => {
  if (e.target.matches(".draft") && sessionStorage.getItem(selectedKey(qNow())) !== e.target.dataset.block) {
    selectBlock(qNow(), e.target.dataset.block);
  }
});
document.addEventListener("input", (e) => {
  if (e.target.id === "doc-search") $("#document-list").innerHTML = documentRows(e.target.value);
  if (e.target.matches("#result [name=result]")) {
    const paragraph = by(S.paragraphs, e.target.closest(".item").querySelector('[data-action="keep"]').dataset.id);
    const changed = e.target.value !== paragraph.body;
    if (changed) resultEdits.set(paragraph.id, e.target.value);
    else resultEdits.delete(paragraph.id);
    $("#result-length").textContent = resultLength(chars(e.target.value, qNow().spaces), paragraph.details);
    $("#result-edited").hidden = paragraph.details.mode === "free" ||
      (!changed && paragraph.details.review !== "사용자 편집본 · 근거 재검토");
    $("#result-assessment").hidden = changed;
  }
  if (e.target.matches(".draft")) {
    mark(qNow(), readBlocks());
    countView(qNow());
  }
  if (e.target.id === "filter")
    $("#library").innerHTML = libraryRows(
      e.target.value,
      route()[0] === "write",
    );
});
document.addEventListener("submit", async (e) => {
  if (!e.target.matches("form[data-form]")) return;
  e.preventDefault();
  const f = e.target,
    send = f.querySelector("[type=submit]"),
    fd = new FormData(f),
    d = Object.fromEntries(fd),
    act = f.dataset.form,
    id = f.dataset.id;
  send.disabled = true;
  try {
    if (["model-select", "model-download", "model-import"].includes(act)) {
      let path, payload;
      if (act === "model-select") {
        if ([...f.querySelectorAll("select")].some(s => s.selectedOptions[0]?.disabled)) throw Error("각 역할에 설치된 모델을 선택해 주세요.");
        path = "apply";
        payload = {generation: d.generation, analysis: d.analysis || d.generation, embedding: d.embedding,
          version: Number(f.dataset.id)};
      } else if (act === "model-download") {
        path = "download";
        payload = {name: d.name.trim()};
      } else {
        path = "import";
        payload = fd;
        send.textContent = "파일 전송 중…";
      }
      await api("models/" + path, "POST", payload);
      if (act !== "model-select") f.reset();
      if (route()[0] === "models") await syncModels(modelScreenId);
      toast(act === "model-select" ? "검증과 적용을 요청했습니다." : "모델 설치를 요청했습니다.");
      return;
    }
    const generating = act === "generate" || act === "generate-free";
    const requestedQuestion = generating ? qNow().id : null;
    if (generating && generationContext?.q === requestedQuestion) throw Error("현재 문항의 문단을 작성 중입니다.");
    const destination = generating ? prepareTarget(qNow()) : null;
    await flush();
    if (act === "block-limits") {
      const q = qNow(), blocks = readBlocks(), block = blocks[Number(id)];
      const minimum = d.minimum === "" ? null : Number(d.minimum);
      const maximum = d.maximum === "" ? null : Number(d.maximum);
      if ((minimum !== null && (!Number.isInteger(minimum) || minimum < 0 || minimum > 100000)) ||
          (maximum !== null && (!Number.isInteger(maximum) || maximum < 1 || maximum > 100000)) ||
          (maximum !== null && minimum > maximum)) {
        throw Error("최소·최대 글자 수 범위를 확인해 주세요. 최대는 1~100,000자입니다.");
      }
      Object.assign(block, {minimum, maximum});
      mark(q, blocks);
      drawBlocks(q);
      if (targetBlock(q, blocks) === block) syncWritingLimits(q);
      await flush();
      close();
      return;
    }
    if (act === "app" || act === "question") {
      d.max_chars = Number(d.max_chars);
      d.spaces = fd.has("spaces");
      const r = await api(
        act === "app"
          ? "applications"
          : "applications/" + qNow().app + "/questions",
        "POST",
        d,
      );
      close();
      await refresh();
      location.hash = "write/" + (r.question || r.id);
      return;
    }
    if (act === "import-text") {
      if (id) fd.set("application", id);
      const result = await api("imports", "POST", fd);
      close();
      await refresh();
      if (!id) tab = "원본 문서";
      render();
      toast(result.status === "중복" ? "이미 등록된 자료입니다." : "텍스트를 등록했습니다.");
      return;
    }
    if (act === "upload") {
      if (id) fd.set("application", id);
      if (!fd.get("file")?.name) fd.delete("file");
      if (!fd.has("file") && !d.text?.trim())
        throw Error("등록할 파일이나 본문을 입력해 주세요.");
      await api("documents", "POST", fd);
    }
    if (act === "fact") {
      const f0 = by(S.facts, id);
      await api(id ? "facts/" + id : "facts", id ? "PATCH" : "POST", {
        ...d,
        experience: d.experience || null,
        tags: splitTags(d.tags),
        version: f0?.version || 1,
      });
    }
    if (act === "tag")
      await api(id ? "tags/" + id : "tags", id ? "PATCH" : "POST", {
        ...d,
        parent: d.parent || null,
        aliases: splitTags(d.aliases),
      });
    if (act === "merge-tag")
      await api(
        "tags/" + id + "?merge_into=" + encodeURIComponent(d.target),
        "DELETE",
      );
    if (act === "merge-fact")
      await api("facts/" + id + "/merge/" + d.target, "POST");
    if (act === "replace-doc")
      await api("documents/" + id + "/version", "POST", fd);
    if (act === "para-tags") {
      for (const t of S.tags) {
        const state = fd.getAll("labels").includes(t.id) ? "확정" : "제외";
        if (
          state === "확정" ||
          S.links.some((l) => l.target === id && l.tag === t.id)
        )
          await api("links", "POST", {
            tag: t.id,
            kind: "paragraph",
            target: id,
            state,
          });
      }
    }
    if (generating) {
      const free = act === "generate-free";
      const payload = {
        question: requestedQuestion,
        target_block: destination,
        minimum: Number(d.minimum),
        maximum: Number(d.maximum),
        instruction: d.instruction,
      };
      if (!free) Object.assign(payload, {
        narrative: fd.has("narrative"),
        previous: rewrite,
      });
      const r = await api(free ? "generate/free" : "generate/instructions", "POST", payload);
      generationContext = { job: r.job, q: requestedQuestion };
      rewrite = null;
      toast("문단 생성을 시작했습니다.");
      if (f.narrative) f.narrative.checked = false;
      return;
    }
    close();
    await refresh();
    render();
    toast("저장했습니다.");
  } catch (err) {
    toast(err.message);
  } finally {
    if (act === "model-import") send.textContent = "파일 등록";
    send.disabled = false;
  }
});
function questionFields() {
  return (
    area("title", "자기소개서 문항") +
    field("max_chars", "문항 글자 수 제한", 700, "number") +
    check("spaces", "공백 포함", true)
  );
}
function tagsHtml() {
  return (
    form(
      "tag",
      field("name", "태그 이름") +
        field("description", "설명", "", "text", false) +
        field("aliases", "별칭 (쉼표로 구분)", "", "text", false) +
        select(
          "parent",
          "상위 태그",
          option("", "없음") + S.tags.map((t) => option(t.id, t.name)).join(""),
        ),
      "태그 저장 및 재분석",
    ) +
    S.tags
      .map(
        (t) =>
          '<div class="item"><b>' +
          esc(
            (by(S.tags, t.parent)?.name
              ? by(S.tags, t.parent).name + " › "
              : "") + t.name,
          ) +
          "</b> " +
          (t.proposed ? "<small>자동 제안</small> " : "") +
          button("edit-tag", "편집", t.id) +
          "</div>",
      )
      .join("") +
    S.links
      .filter((l) => l.state !== "제외")
      .map((l) => {
        let key = [l.tag, l.target_type, l.target].join("/");
        return (
          '<div class="item"><small>' +
          esc(by(S.tags, l.tag)?.name + " · " + l.state) +
          "</small><p>" +
          esc(by(S.facts, l.target)?.body || "원문 문단") +
          "</p>" +
          (l.target_type === "passage" ? button("ref", "원문", l.target) : "") +
          button("accept", "확정", key) +
          button("exclude", "제외", key) +
          "</div>"
        );
      })
      .join("")
  );
}
async function insert(pid, qid, targetId, fromResult = false) {
  if (qNow()?.id !== qid) throw Error("원래 문항에서 문단을 적용해 주세요.");
  const p = by(S.paragraphs, pid),
    q = qNow();
  let blocks = readBlocks();
  let block = targetId ? blocks.find(b => b.block_id === targetId) : newBlock();
  if (!block) throw Error("선택한 문단이 삭제되었습니다.");
  if (fromResult && p.details.target_digest && block.body && block.body !== p.body) {
    const before = block.body;
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(before));
    const hex = Array.from(new Uint8Array(digest), n => n.toString(16).padStart(2, "0")).join("");
    if (hex !== p.details.target_digest && !window.confirm("초안 요청 후 이 문단 내용이 바뀌었습니다. 현재 내용을 교체할까요?")) return;
    if (qNow()?.id !== qid) return;
    blocks = readBlocks();
    block = blocks.find(b => b.block_id === targetId);
    if (!block || block.body !== before) throw Error("문단이 변경되었습니다. 다시 적용해 주세요.");
  }
  if (block.minimum == null && block.maximum == null) {
    block.minimum = p.details.minimum;
    block.maximum = p.details.maximum;
  }
  const advance = !block.body.trim() && targetBlock(q, blocks)?.block_id === block.block_id;
  block.body = p.body;
  block.paragraph = p.id;
  if (!blocks.includes(block)) blocks.push(block);
  if (advance) sessionStorage.removeItem(selectedKey(q));
  mark(q, blocks);
  drawBlocks(q);
  syncWritingLimits(q);
  if ($("#library") && tool === "보관함") $("#library").innerHTML = libraryRows($("#filter").value, true);
  await flush();
  toast("문단을 적용했습니다.");
}
async function actions(act, id) {
  const q = qNow();
  if (act === "close") return close();
  if (act === "writing-mode") {
    writingMode = id;
    rewrite = null;
    drawWritingOptions(q);
    return;
  }
  if (act === "block-limits") {
    const block = readBlocks()[Number(id)];
    return modal("문단 " + (Number(id) + 1) + " 글자 수 설정",
      form("block-limits", '<div class="pair">' +
        field("minimum", "최소 글자 수", block.minimum ?? "", "number", false) +
        field("maximum", "최대 글자 수", block.maximum ?? "", "number", false) +
        '</div><p class="note">빈칸으로 저장하면 해당 제한을 해제합니다.</p>', "적용", id));
  }
  if (act === "goto") {
    await flush();
    location.hash = "write/" + id;
    return;
  }
  if (act === "tab") {
    tab = id;
    render();
    return;
  }
  if (act === "tool") {
    tool = id;
    rewrite = null;
    drawTool(q);
    return;
  }
  if (act === "new-app")
    return modal(
      "새 자소서",
      form(
        "app",
        field("company", "지원 기업") +
          field("role", "지원 직무") +
          questionFields(),
        "작성 시작",
      ),
    );
  if (act === "add-q")
    return modal("문항 추가", form("question", questionFields(), "문항 추가"));
  if (act === "direct") return modal("경험 직접 입력", factForm());
  if (act === "fact") {
    const f=by(S.facts,id), candidates=await api("facts/"+id+"/candidates");
    const comparison=candidates.length?'<details><summary>관련 사실과 원문 비교</summary>'+candidates.map(c=>
      '<div class="item"><b>'+esc(c.title)+'</b><p>'+esc(c.body)+'</p>'+
      c.refs.map(ref=>button("ref","원문",ref)).join("")+'</div>').join("")+
      form("merge-fact",select("target","동일한 사실로 확인한 항목",candidates.map(c=>option(c.id,c.body)).join("")),"이 사실에 출처 합치기",id)+'</details>':"";
    return modal("경험 검토",factForm(f)+comparison);
  }
  if (act === "upload" || act === "company-doc")
    return openImport(act === "company-doc" ? q.app : "");
  if (act === "tags") return modal("태그 관리", tagsHtml());
  if (act === "edit-tag") {
    const t = by(S.tags, id),
      others = S.tags.filter((x) => x.id !== id);
    return modal(
      "태그 편집",
      form(
        "tag",
        field("name", "이름", t.name) +
          field("description", "설명", t.description, "text", false) +
          field("aliases", "별칭", t.aliases.join(", "), "text", false) +
          select(
            "parent",
            "상위 태그",
            option("", "없음") +
              others.map((x) => option(x.id, x.name, t.parent)).join(""),
          ),
        "변경 저장",
        id,
      ) +
        (others.length
          ? form(
              "merge-tag",
              select(
                "target",
                "연결을 옮길 태그",
                others.map((x) => option(x.id, x.name)).join(""),
              ),
              "선택 태그로 병합",
              id,
            )
          : "") +
        button("delete-tag", "태그 삭제", id),
    );
  }
  if (act === "delete-tag" || act === "delete-doc") {
    if (!window.confirm("선택한 자료를 삭제할까요?")) return;
    await api((act === "delete-tag" ? "tags/" : "documents/") + id, "DELETE");
    close();
    await refresh();
    render();
    return;
  }
  if (act === "accept" || act === "exclude") {
    let [tag, kind, target] = id.split("/");
    await api("links", "POST", {
      tag,
      kind,
      target,
      state: act === "accept" ? "확정" : "제외",
    });
    await refresh();
    return modal("태그 관리", tagsHtml());
  }
  if (act === "doc") {
    const d = by(S.documents, id),
      rows = await api("documents/" + id + "/passages");
    return modal(
      d.name,
      "<small>" +
        esc(
          [d.kind, d.metadata.source || d.metadata.suggested_company, d.metadata.role, d.metadata.checked, d.status]
            .filter(Boolean)
            .join(" · "),
        ) +
        "</small>" +
        '<p><a href="/api/documents/' + esc(id) + '/original" download>원본 파일 내려받기</a></p>' +
        (/^https?:\/\//i.test(d.metadata.web_url || "") ? '<p><a target="_blank" rel="noopener noreferrer" href="' + esc(d.metadata.web_url) + '">조사 출처 열기</a></p>' : "") +
        (d.status === "이미지 보관" ? '<img style="max-width:100%" src="/api/documents/' + esc(d.id) + '/image" alt="' + esc(d.name) + '">' : "") +
        (d.metadata.folder_paths?.length ? "<details><summary>등록 경로</summary>" + d.metadata.folder_paths.map(p => "<p>" + esc(p) + "</p>").join("") + "</details>" : "") +
        "<details><summary>원본 관리</summary>" +
        form(
          "replace-doc",
          '<label>새 버전 파일<input required type="file" name="file" accept=".txt,.docx,.pdf"></label>',
          "새 버전 등록",
          id,
        ) +
        button("delete-doc", "원본 삭제", id) +
        "</details>" +
        rows
          .map(
            (r) =>
              '<div class="item"><b>' +
              esc(r.location) +
              "</b><p>" +
              esc(r.body) + "</p>" +
              ["확정", "제안"].map(state => {
                const names = tagNames("passage", r.id, state);
                return names.length ? "<small>" + esc(state + " 태그 · " + names.join(", ")) + "</small> " : "";
              }).join("") + "</div>",
          )
          .join(""),
    );
  }
  if (act === "ref") {
    try {
      const r = await api("passages/" + id);
      return modal(
        r.name + " · " + r.location,
        "<small>" +
          esc("버전 " + r.version + (r.active ? "" : " · 이전 원본")) +
          '</small><p class="preview">' +
          esc(r.body) +
          "</p>",
      );
    } catch (e) {
      return modal("근거 상태", "원본이 삭제되었거나 접근할 수 없습니다.");
    }
  }
  if (act === "settings") {
    if (route()[0] === "models") return openModels();
    location.hash = "models";
    return;
  }
  if (act === "company-research") {
    await api("applications/" + id + "/research", "POST");
    await refresh();
    if (qNow()?.app === id && tool === "기업 자료") drawTool(qNow());
    toast("회사·직무 자료 조사를 요청했습니다.");
    return;
  }
  if (act === "clear-completed") {
    const result = await api("jobs/clear-completed", "POST");
    await actions("refresh-jobs");
    toast("완료 작업 " + result.cleared + "건을 정리했습니다.");
    return;
  }
  if (act === "jobs" || act === "refresh-jobs") {
    await refresh();
    if (act === "refresh-jobs" && (!$("#modal").open || $("#modal-title").textContent !== "작업 상태")) return;
    return modal(
      "작업 상태",
      button("clear-completed", "완료 작업 정리") + ([...S.jobs].sort((a, b) => jobPriority(a.status) - jobPriority(b.status))
        .map(
          (j) =>
            '<div class="item"><b>' +
            esc(
              {
                parse: "원문 추출",
                classify: "자료 유형 자동 분류",
                extract: "경험 분석",
                company_tags: "기업 자료 태그 제안",
                report_tags: "보고서 자료 태그",
                analyze_template: "양식 분석",
                report_cell: "보고서 칸 작성",
                research_company: "기업 자료 조사",
                configure_models: "모델 검증 및 적용",
                install_model: "모델 설치",
                generate: j.args.mode === "free" ? "자유 문단 생성" : "문단 생성",
                retag: "재태깅",
                index_doc: "문서 색인",
                index_fact: "경험 색인",
              }[j.kind],
            ) +
            '</b> <span class="badge">' +
            esc(j.status) +
            "</span><p>" +
            esc([j.subject || j.args.subject || by(S.applications, j.args.application)?.company, j.location, j.error].filter(Boolean).join(" · ")) +
            "</p>" +
            (["대기","실행"].includes(j.status) ? button("pause", "보류", j.id) : "") +
            (j.status === "보류" ? button("resume", "재개", j.id) : "") +
            (["대기","실행","보류"].includes(j.status) ? button("cancel", "취소", j.id) : "") +
            (j.status === "실패" ? button("retry", "재시도", j.id) : "") +
            (j.kind==="generate" && j.args.mode !== "free" ? button("rounds","검증 기록",j.id) : "")+
            (j.args.question
              ? button("job-q", "요청한 문항", j.args.question)
              : "") +
            "</div>",
        )
        .join("") || "<p>등록된 작업이 없습니다.</p>"),
    );
  }
  if (["pause","resume","cancel","retry"].includes(act)) {
    await api("jobs/" + id + "/" + act, "POST");
    const job = by(S.jobs, id);
    if (act === "resume" && job?.kind === "generate" && q?.id === job.args.question &&
        (!generationContext || generationContext.job === id)) {
      generationContext = {job: id, q: q.id, source: job.args.revise_from, body: by(S.paragraphs, job.args.previous)?.body};
    }
    return actions("refresh-jobs");
  }
  if(act==="rounds"){
    const j=await api("jobs/"+id);
    return modal("문단 검증 기록",'<p>'+esc(j.status)+(j.error?" · "+esc(j.error):"")+'</p>'+
      (j.rounds.map(r=>'<div class="item"><b>'+r.round+'차 초안</b><p>'+
        esc(r.assessment?(r.assessment.count+'자 · '+(r.assessment.accepted?'조건 충족':r.assessment.feedback.join(' / '))):'초안 저장됨 · 평가 대기')+
        '</p></div>').join("")||'<p>첫 초안을 준비하고 있습니다.</p>'));
  }
  if (act === "job-q") {
    close();
    await flush();
    location.hash = "write/" + id;
    return;
  }
  if (
    act === "block-add" ||
    act === "remove-block" ||
    act === "up" ||
    act === "down"
  ) {
    let blocks = readBlocks(),
      i = Number(id);
    if (act === "block-add") blocks.push(newBlock());
    else if (act === "remove-block") blocks.splice(i, 1);
    else {
      let to = i + (act === "up" ? -1 : 1);
      [blocks[i], blocks[to]] = [blocks[to], blocks[i]];
    }
    mark(q, blocks);
    drawBlocks(q);
    if (act === "block-add") {
      document.querySelectorAll("#blocks .draft")[blocks.length - 1].focus();
    } else {
      syncWritingLimits(q);
      if ($("#result")) drawSelectedResult(q);
    }
    return;
  }
  if (act === "save") {
    await flush();
    toast("원고를 저장했습니다.");
    return;
  }
  if (act === "preview") {
    await flush();
    return modal(
      "원고 미리보기",
      button("copy-all", "전체 답변 복사") +
        qs(q.app)
          .map(
            (it, i) =>
              '<div class="item"><h3>' +
              esc(it.title) +
              "</h3><p>" +
              esc(joined(it)) +
              "</p>" +
              button("copy", "문항 복사", it.id) +
              "</div>",
          )
          .join(""),
    );
  }
  if (act === "copy" || act === "copy-all") {
    await navigator.clipboard.writeText(
      act === "copy"
        ? joined(by(S.questions, id))
        : qs(q.app).map(joined).join("\n\n"),
    );
    toast("복사했습니다.");
    return;
  }
  if (act === "recommend") {
    toast("관련 경험을 찾고 있습니다.");
    const r = await api("questions/" + q.id + "/recommendations");
    return modal(
      "추천 경험",
      "<p>" +
        esc(r.role + " · " + r.question) +
        "</p>" +
        r.facts
          .map(
            (f) =>
              '<div class="item"><b>' +
              esc(f.experience_name) +
              "</b><p>" +
              esc(f.reason) +
              "</p><p>" +
              esc(f.body) +
              "</p>" +
              button("pick", "지시사항에 추가", f.id) +
              "</div>",
          )
          .join("") +
        (!r.facts.length ? "확정한 경험이 필요합니다." : ""),
    );
  }
  if (act === "pick") {
    const f = by(S.facts, id);
    close();
    const input = $("#writing-options [name=instruction]");
    input.value = [input.value.trim(), [f.title, ...tagNames("fact", id, "확정")].join(", ")].filter(Boolean).join("\n");
    input.focus();
    return;
  }
  if (act === "inspect") {
    const p = by(S.paragraphs, id);
    let versions = [],
      cur = p;
    while (cur && !versions.some((v) => v.id === cur.id)) {
      versions.push(cur);
      cur = by(S.paragraphs, cur.previous);
    }
    return modal(
      "보관한 문단",
      '<p class="preview">' +
        esc(p.body) +
        '</p><p class="note">' +
        esc(p.details.tags.join(" · ")) +
        "</p>" +
        p.details.references
          .map((r) => button("ref", r.name + " · " + r.location, r.id))
          .join("") +
        (p.details.mode === "free" ? "" : "<details><summary>태그 편집</summary>" +
        form(
          "para-tags",
          S.tags
            .map(
              (t) =>
                '<label class="inline"><input type="checkbox" name="labels" value="' +
                esc(t.id) +
                '" ' +
                (tagNames("paragraph", id, "확정").includes(t.name)
                  ? "checked"
                  : "") +
                "> " +
                esc(t.name) +
                "</label>",
            )
            .join(""),
          "태그 저장",
          id,
        ) +
        "</details>") +
        "<details><summary>문단 버전 " +
        versions.length +
        "개</summary>" +
        versions
          .map(
            (v) =>
              '<div class="item"><small>' +
              esc(v.created.slice(0, 16)) +
              "</small><p>" +
              esc(v.body) +
              "</p></div>",
          )
          .join("") +
        "</details>" +
        button("unkeep", "보관 해제", id),
    );
  }
  if (act === "unkeep") {
    let p = by(S.paragraphs, id);
    await api("paragraphs/" + id, "POST", { body: p.body, saved: false });
    close();
    await refresh();
    render();
    return;
  }
  if (act === "rewrite") {
    tool = "생성";
    writingMode = "evidence";
    rewrite = id;
    drawTool(q);
    return;
  }
  if (act === "revise") {
    const panel = $("#result");
    const trigger = panel.querySelector('[data-action="revise"]');
    if (trigger.disabled || generationContext?.q === q.id) return;
    const qid = q.id;
    const body = panel.querySelector("[name=result]").value;
    if (!body.trim()) throw Error("재작성할 문단을 입력하세요.");
    trigger.disabled = true;
    try {
      const narrative = $("#tools [name=narrative]");
      const destination = prepareTarget(q);
      await flush();
      const r = await api("paragraphs/" + id + "/revise", "POST", {
        body, target_block: destination, request_id: crypto.randomUUID(), narrative: Boolean(narrative?.checked),
      });
      generationContext = {job: r.job, q: qid, source: id, body};
      if (narrative) narrative.checked = false;
      toast("문단 재작성을 시작했습니다.");
    } catch (error) {
      trigger.disabled = false;
      throw error;
    }
    return;
  }
  if (act === "insert") return insert(id, q.id, targetBlock(q)?.block_id);
  if (act === "keep" || act === "edited-insert") {
    const destination = by(S.paragraphs, id).details.target_block || targetBlock(q)?.block_id;
    const body = document.querySelector("[name=result]").value,
      r = await api("paragraphs/" + id, "POST", {
        body,
        saved: act === "keep" || by(S.paragraphs, id).saved,
      });
    await flush();
    await refresh();
    viewResult = r.id;
    if (act === "edited-insert") await insert(r.id, q.id, destination, true);
    else toast("문단을 보관했습니다.");
    if (qNow()?.id === q.id) drawTool(qNow());
    return;
  }
}
document.addEventListener("click", (e) => {
  let b = e.target.closest("[data-action]");
  if (b)
    actions(b.dataset.action, b.dataset.id).catch((err) => toast(err.message));
});
window.addEventListener("hashchange", async () => {
  try {
    await flush();
    await refresh();
    tool = "생성";
    rewrite = null;
    viewResult = null;
    render();
  } catch (e) {
    toast(e.message);
  }
});
window.addEventListener("beforeunload", (e) => {
  if (dirty.size) {
    e.preventDefault();
    e.returnValue = "";
  }
});
setInterval(async () => {
  try {
    if (generationContext) {
      const tracked=generationContext;
      const j = await api("jobs/" + tracked.job);
      if(generationContext!==tracked)return;
      if(current===tracked.q && $("#generation-progress")){
        const last=j.rounds?.at(-1);
        $("#generation-progress").textContent=j.status==="완료"?"문단 작성 완료":j.status==="실패"?(j.error||"작업 실패"):
          j.status==="보류"?"문단 작성 보류":j.status==="취소"?"문단 작성 취소":
          last?(last.assessment?last.round+"차 평가 · "+(last.assessment.feedback.join(" / ")||"조건 충족"):last.round+"차 초안의 태그와 근거를 검토하고 있습니다."):
          j.status==="대기"?"작업 대기 중":j.args.mode==="free"?"자유 문단 작성 중":"근거 검색 및 초안 작성 중";
      }
      if (["완료", "실패", "보류", "취소"].includes(j.status)) {
        let dest = tracked.q;
        generationContext = null;
        await flush();
        await refresh();
        const newerEdit = tracked.source && resultEdits.has(tracked.source) &&
          resultEdits.get(tracked.source) !== tracked.body;
        if (
          j.status === "완료" &&
          current === dest &&
          tool === "생성" &&
          $("#result") && !newerEdit
        ) {
          viewResult = j.result.paragraph;
          drawSelectedResult(qNow());
        }
        const reviseButton = $('#result [data-action="revise"]');
        if (reviseButton) reviseButton.disabled = false;
        toast(
          j.status === "완료"
            ? "요청한 문항의 문단이 준비되었습니다."
            : j.error || ("문단 작성 " + j.status),
        );
      }
    }
    if (qNow() && tool === "기업 자료") {
      const aid = qNow().app;
      const docs = new Set(S.documents.filter(d => d.app === aid).map(d => d.id));
      if (S.jobs.some(j => (j.args.application === aid || docs.has(j.args.doc)) && ["대기", "실행", "보류"].includes(j.status))) {
        await refresh();
        if (qNow()?.app === aid && tool === "기업 자료") drawTool(qNow());
      }
    }
    if ($("#modal").open && $("#modal-title").textContent === "작업 상태")
      await actions("refresh-jobs");
  } catch (e) {
    toast(e.message);
  }
}, 3500);
refresh()
  .then(render)
  .catch((e) => {
    $("#main").textContent = e.message;
  });
