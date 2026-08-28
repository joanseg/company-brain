"use strict";

// One line per tab, shown under the tab bar for whichever tab is active.
const TAB_HINTS = {
  communities: "What the corpus is about. Clusters of related entities, each " +
    "summarised from the documents that mention them.",
  graph: "How entities connect. The most-mentioned entities, sized by how " +
    "often they appear across your documents.",
  corpus: "Every document, grouped by source and folder, sized by length. " +
    "Faded documents have no entities yet.",
};

const Brain = {
  state: {},
  async fetchJSON(path) {
    const response = await fetch(path);
    if (!response.ok) throw new Error(path + " → " + response.status);
    return response.json();
  },
  el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const key in attrs || {}) {
      if (key === "class") node.className = attrs[key];
      else if (key === "text") node.textContent = attrs[key];
      else node.setAttribute(key, attrs[key]);
    }
    (children || []).forEach((child) => node.appendChild(child));
    return node;
  },
  empty(target, message, command) {
    target.innerHTML = "";
    const box = Brain.el("div", { class: "empty" });
    box.appendChild(document.createTextNode(message + " "));
    if (command) box.appendChild(Brain.el("code", { text: command }));
    target.appendChild(box);
  },
  showTab(name) {
    document.querySelectorAll(".tabs button").forEach((button) =>
      button.classList.toggle("active", button.dataset.tab === name));
    ["communities", "graph", "corpus"].forEach((tab) => {
      document.getElementById("tab-" + tab).hidden = tab !== name;
    });
    const hint = document.getElementById("tab-hint");
    if (hint) hint.textContent = TAB_HINTS[name] || "";
    if (Brain.render[name]) Brain.render[name]();
  },
  render: {},
};
if (typeof window !== "undefined") window.Brain = Brain;

// `description` is rendered alongside the command (in its own element, not
// via textContent) but never copied — only `text` goes to the clipboard.
function copyButton(text, className, description) {
  const button = Brain.el("button", { class: "copy " + (className || "") });
  button.appendChild(document.createTextNode(text));
  if (description) button.appendChild(Brain.el("span", { class: "does", text: description }));
  button.addEventListener("click", () => {
    navigator.clipboard.writeText(text).then(() => {
      button.classList.add("copied");
      setTimeout(() => button.classList.remove("copied"), 900);
    });
  });
  return button;
}

// The filesystem root of a source is long and not the interesting part —
// abbreviate the home directory to `~` so it fits a 320px panel. This is a
// client-side heuristic (the browser has no notion of "home directory"): it
// matches the common `/Users/<name>` and `/home/<name>` prefixes. The full
// root is always still available via the row's `title` tooltip.
function abbreviateRoot(root) {
  return root.replace(/^\/(Users|home)\/[^/]+/, "~");
}

function renderSources(panel, sources) {
  if (!sources || !sources.length) return;
  panel.appendChild(Brain.el("h2", { text: "Sources" }));
  panel.appendChild(Brain.el("p", {
    class: "hint",
    text: "/brain-source adds another repo; /brain-index picks up changes across all of them.",
  }));
  const list = Brain.el("div", { class: "sources" });
  sources.forEach((source) => {
    const row = Brain.el("div", { class: "source", title: source.root });
    row.appendChild(Brain.el("div", { class: "source-name", text: source.name }));
    const stats = Brain.el("div", { class: "source-stats" });
    stats.appendChild(document.createTextNode(
      source.documents + " docs · " + source.chunks + " chunks"));
    stats.appendChild(Brain.el("span", { class: "trust", text: " trust " + source.weight }));
    row.appendChild(stats);
    row.appendChild(Brain.el("div", { class: "source-root", text: abbreviateRoot(source.root) }));
    list.appendChild(row);
  });
  panel.appendChild(list);
}

async function boot() {
  const health = document.getElementById("health");
  const panel = document.getElementById("panel");
  const overview = await Brain.fetchJSON("/api/overview");
  Brain.state.overview = overview;

  if (overview.empty) {
    health.textContent = "no index yet";
    ["communities", "graph", "corpus"].forEach((tab) =>
      Brain.empty(document.getElementById("tab-" + tab), "Nothing indexed yet. Run", "/brain-index"));
  } else {
    const counts = overview.counts;
    const parts = [
      ["documents", counts.documents], ["chunks", counts.chunks],
      ["entities", counts.entities], ["communities", counts.communities],
    ];
    parts.forEach(([label, value]) => {
      const span = Brain.el("span", {});
      span.appendChild(Brain.el("b", { text: String(value) }));
      span.appendChild(document.createTextNode(" " + label));
      health.appendChild(span);
    });
    const flags = [
      ["enrich_pending", "chunks pending enrichment", "/brain-enrich"],
      ["unembedded", "chunks not yet embedded", "/brain-index"],
      ["documents_without_entities", "documents with no entities", "/brain-enrich"],
      ["summaries_missing", "communities missing a summary", "/brain-enrich --communities"],
      ["stale_files", "files changed since the last index", "/brain-index"],
    ];
    flags.forEach(([key, label, command]) => {
      const value = overview.health[key];
      if (value > 0) {
        const flag = Brain.el("span", { class: "flag" });
        flag.appendChild(document.createTextNode(value + " " + label + " "));
        flag.appendChild(copyButton(command, "cmd flag-cmd"));
        health.appendChild(flag);
      }
    });
    renderSources(panel, overview.sources);
  }

  document.querySelectorAll(".tabs button").forEach((button) =>
    button.addEventListener("click", () => Brain.showTab(button.dataset.tab)));
  Brain.showTab("communities");

  // The "what to ask" panel is secondary — its failure must not take the
  // tabs above down with it, since /api/overview already succeeded.
  try {
    const suggestions = await Brain.fetchJSON("/api/suggestions");
    if (!suggestions.empty) {
      panel.appendChild(Brain.el("h2", { text: "Ask" }));
      suggestions.questions.forEach((q) => panel.appendChild(copyButton(q.text)));
      panel.appendChild(Brain.el("h2", { text: "Which tool" }));
      suggestions.how_to.forEach((row) => {
        const line = Brain.el("div", { class: "how" });
        line.appendChild(Brain.el("b", { text: row.tool }));
        line.appendChild(document.createTextNode(" — " + row.when));
        panel.appendChild(line);
      });
      panel.appendChild(Brain.el("h2", { text: "Commands" }));
      suggestions.commands.forEach((c) => panel.appendChild(copyButton(c.command, "cmd", c.does)));
    }
  } catch (error) {
    // Leave the panel empty rather than dragging down a page whose primary
    // tabs already rendered fine.
  }
}

if (typeof document !== "undefined") {
  boot().catch((error) => {
    document.getElementById("health").textContent = "failed to load: " + error.message;
  });
}

Brain.render.communities = async function () {
  const target = document.getElementById("tab-communities");
  if (target.dataset.loaded) return;
  target.dataset.loaded = "1";

  const data = await Brain.fetchJSON("/api/communities");
  if (data.empty) return;
  if (!data.communities.length) {
    Brain.empty(target, "No community summaries yet. Run", data.next);
    return;
  }

  const detail = Brain.el("div", { class: "detail" });
  detail.hidden = true;
  const grid = Brain.el("div", { class: "cards" });
  target.appendChild(detail);
  target.appendChild(grid);

  data.communities.forEach((community) => {
    const card = Brain.el("div", { class: "card" });
    card.appendChild(Brain.el("h3", { text: community.title }));
    const meta = Brain.el("div", { class: "meta" });
    const rating = Brain.el("span", {});
    rating.appendChild(Brain.el("b", { text: String(community.rating) }));
    rating.appendChild(document.createTextNode("/10"));
    meta.appendChild(rating);
    meta.appendChild(Brain.el("span", { text: community.member_count + " entities" }));
    if (community.dominant_source) {
      meta.appendChild(Brain.el("span", { text: community.dominant_source }));
    }
    card.appendChild(meta);
    card.addEventListener("click", () => {
      detail.innerHTML = "";
      detail.hidden = false;
      detail.appendChild(Brain.el("h3", { text: community.title }));
      detail.appendChild(Brain.el("p", { text: community.summary }));
      const remaining = community.member_count - community.members.length;
      const membersText = community.members.join(" · ") +
        (remaining > 0 ? " · +" + remaining + " more" : "");
      detail.appendChild(Brain.el("div", { class: "members", text: membersText }));
      if (community.citations && community.citations.length) {
        const citations = Brain.el("div", { class: "citations" });
        community.citations.forEach((c) => {
          citations.appendChild(Brain.el("div", {
            class: "citation", text: c.path + ":" + c.line,
          }));
        });
        detail.appendChild(citations);
      }
      detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    grid.appendChild(card);
  });
};

const TYPE_COLOURS = {
  person: "#0e6e68", organisation: "#8a5310", product: "#3f6ea8",
  place: "#6a7b45", concept: "#7a5a86", event: "#a05252", decision: "#4c5a63",
};

// Force-directed layout for the graph canvas. Pulled out to a pure function
// (nodes/edges/canvas size in, mutates node.x/y/vx/vy) so it has no DOM
// dependency and the physics test can exercise it directly under Node.
//
// Three bounds keep this stable from 50 to ~1700+ nodes, where the old
// unbounded version went to NaN by frame 10 on a 400-node ring (see
// plugins/company-brain/server/tests/graph-physics.test.mjs):
//   - `minDistance` floors the repulsion denominator at a physical distance
//     (not the old `|| 0.01`, which let force run away as nodes converged).
//   - `maxPush` caps any single pair's repulsion force outright.
//   - `maxSpeed` clamps each node's per-frame velocity after damping, so
//     even a still-large summed force can't move a node further than the
//     canvas in one frame.
// `repelBase` is divided by node count in graphTick — repulsion is summed
// over every pair, so a fixed constant made 400+ nodes push each other
// far harder, per node, than 50 did.
const GRAPH_PHYSICS = {
  minDistance: 12,
  repelBase: 14000,
  maxPush: 40,
  maxSpeed: 12,
  damping: 0.86,
  edgeTarget: 70,
  edgeStrength: 0.02,
  centerPull: 0.0006,
};

function graphTick(nodes, edges, width, height) {
  const repel = GRAPH_PHYSICS.repelBase / Math.max(nodes.length, 1);
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j];
      let dx = b.x - a.x, dy = b.y - a.y;
      let distance = Math.sqrt(dx * dx + dy * dy);
      if (distance < GRAPH_PHYSICS.minDistance) distance = GRAPH_PHYSICS.minDistance;
      const push = Math.min(repel / (distance * distance), GRAPH_PHYSICS.maxPush);
      dx /= distance; dy /= distance;
      a.vx -= dx * push; a.vy -= dy * push;
      b.vx += dx * push; b.vy += dy * push;
    }
  }
  edges.forEach((edge) => {
    const a = nodes[edge.a], b = nodes[edge.b];
    let dx = b.x - a.x, dy = b.y - a.y;
    let distance = Math.sqrt(dx * dx + dy * dy);
    if (distance < GRAPH_PHYSICS.minDistance) distance = GRAPH_PHYSICS.minDistance;
    const pull = (distance - GRAPH_PHYSICS.edgeTarget) * GRAPH_PHYSICS.edgeStrength;
    dx /= distance; dy /= distance;
    a.vx += dx * pull; a.vy += dy * pull;
    b.vx -= dx * pull; b.vy -= dy * pull;
  });
  nodes.forEach((node) => {
    node.vx += (width / 2 - node.x) * GRAPH_PHYSICS.centerPull;
    node.vy += (height / 2 - node.y) * GRAPH_PHYSICS.centerPull;
    node.vx *= GRAPH_PHYSICS.damping; node.vy *= GRAPH_PHYSICS.damping;
    const speed = Math.hypot(node.vx, node.vy);
    if (speed > GRAPH_PHYSICS.maxSpeed) {
      const scale = GRAPH_PHYSICS.maxSpeed / speed;
      node.vx *= scale; node.vy *= scale;
    }
    node.x += node.vx; node.y += node.vy;
  });
}

let graphRun = null;
let graphGeneration = 0;

Brain.render.graph = async function () {
  const target = document.getElementById("tab-graph");
  if (target.dataset.loaded) return;
  target.dataset.loaded = "1";

  // A generation token, captured before the await below, so a second call
  // that starts (and finishes fetching) while this one is still in flight
  // can tell it has been superseded and bail out before touching the DOM,
  // rAF, or window listeners. Without this, two `change` events fired close
  // together race: both find `graphRun === null` (the first hasn't resolved
  // far enough to assign it yet), both proceed, and whichever resolves last
  // silently orphans the other's canvas, rAF chain and mouseup listener.
  const myGeneration = ++graphGeneration;

  // A previous *settled* run (from a slider move) may still hold a scheduled
  // frame and a window-level listener — stop both before starting a new one.
  if (graphRun) {
    graphRun.stopped = true;
    if (graphRun.frameId !== null) cancelAnimationFrame(graphRun.frameId);
    if (graphRun.onMouseUp) window.removeEventListener("mouseup", graphRun.onMouseUp);
    graphRun = null;
  }

  const limit = Number(target.dataset.limit || 400);
  const data = await Brain.fetchJSON("/api/graph?limit=" + limit);
  if (myGeneration !== graphGeneration) return; // superseded while awaiting — leave no trace
  if (data.empty || !data.nodes.length) {
    Brain.empty(target, "No entities yet. Run", "/brain-enrich");
    return;
  }

  const canvas = Brain.el("canvas", {});
  target.appendChild(canvas);
  const hint = data.truncated
    ? "Showing the " + data.nodes.length + " most-mentioned of " + data.total_entities + " entities."
    : "All " + data.nodes.length + " entities.";
  target.appendChild(Brain.el("p", { class: "hint", text: hint +
    " Drag to pan, scroll to zoom, click a node to isolate it (double-click to reset)." }));

  // Raise the cap and watch it turn into a hairball — the spec's slider.
  const sliderMax = Math.max(50, data.total_entities);
  const initial = Math.min(limit, sliderMax);
  const control = Brain.el("p", { class: "hint" });
  const slider = Brain.el("input", {
    type: "range", min: "50", max: String(sliderMax), step: "50",
    value: String(initial), "aria-label": "Number of entities to draw",
  });
  const readout = Brain.el("span", { text: " " + initial + " nodes" });
  slider.addEventListener("change", () => {
    target.dataset.limit = slider.value;
    target.dataset.loaded = "";
    target.innerHTML = "";
    Brain.render.graph();
  });
  slider.addEventListener("input", () => { readout.textContent = " " + slider.value + " nodes"; });
  control.appendChild(slider);
  control.appendChild(readout);
  target.appendChild(control);

  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth, height = canvas.clientHeight;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);

  const index = new Map();
  const nodes = data.nodes.map((node, i) => {
    index.set(node.id, i);
    const angle = (i / data.nodes.length) * Math.PI * 2;
    return {
      ...node,
      x: width / 2 + Math.cos(angle) * Math.min(width, height) * 0.35,
      y: height / 2 + Math.sin(angle) * Math.min(width, height) * 0.35,
      vx: 0, vy: 0,
      r: 3 + Math.sqrt(node.mentions) * 1.2,
    };
  });
  const edges = data.edges
    .map((edge) => ({ a: index.get(edge.src), b: index.get(edge.dst) }))
    .filter((edge) => edge.a !== undefined && edge.b !== undefined);

  let focus = null, panX = 0, panY = 0, scale = 1, dragging = false, lastX = 0, lastY = 0;

  function tick() {
    graphTick(nodes, edges, width, height);
  }

  // Screen position of a world point, computed by hand rather than via
  // ctx.translate/scale — see the note on draw() below for why.
  function toScreen(node) {
    return [panX + node.x * scale, panY + node.y * scale];
  }
  // Cheap visibility test used to cull off-screen nodes/edges before they
  // reach the canvas API at all. `margin` gives labels (drawn to the right
  // of a node) and edge endpoints just outside the frame room to still
  // paint their visible portion.
  function onScreen(sx, sy, margin) {
    return sx > -margin && sx < width + margin && sy > -margin && sy < height + margin;
  }

  function draw() {
    ctx.clearRect(0, 0, width, height);
    const near = focus === null ? null : new Set([focus]);
    if (near) edges.forEach((e) => {
      if (e.a === focus) near.add(e.b);
      if (e.b === focus) near.add(e.a);
    });

    // Positions are transformed by hand (pan + multiply by scale) instead of
    // via ctx.translate()/ctx.scale(), so every size below — line width, dot
    // radius, font — is a literal screen-pixel constant that zoom never
    // touches. Only the *spread* of node positions changes with scale.
    ctx.lineWidth = 1;
    edges.forEach((edge) => {
      const a = nodes[edge.a], b = nodes[edge.b];
      const [ax, ay] = toScreen(a), [bx, by] = toScreen(b);
      if (!onScreen(ax, ay, 40) && !onScreen(bx, by, 40)) return; // both ends off-screen
      const lit = near && (near.has(edge.a) && near.has(edge.b));
      ctx.strokeStyle = near && !lit ? "rgba(128,128,128,.06)" : "rgba(128,128,128,.22)";
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.stroke();
    });

    ctx.font = "11px system-ui, sans-serif";
    const labelColour = getComputedStyle(document.body).color;
    nodes.forEach((node, i) => {
      const [sx, sy] = toScreen(node);
      if (!onScreen(sx, sy, node.r + 120)) return; // 120: room for a label's text width
      const dim = near && !near.has(i);
      ctx.globalAlpha = dim ? 0.15 : 1;
      ctx.fillStyle = TYPE_COLOURS[node.type] || "#7a828a";
      ctx.beginPath();
      ctx.arc(sx, sy, node.r, 0, Math.PI * 2);
      ctx.fill();
      // Label gate: node.r is a fixed world-space "importance" (from mention
      // count), never redrawn at a magnified size any more. `node.r * scale`
      // is what that dot's radius *would* have been under the old scaled-
      // drawing approach — reusing it here as the gate, rather than for
      // drawing, keeps the default zoom (scale===1) identical to the old
      // >7 rule (only the genuinely prominent entities), while letting the
      // threshold effectively loosen as you zoom in: nodes spread apart on
      // screen, so more of them cross the bar and labels are progressively
      // revealed instead of all piling up at once.
      if (!dim && node.r * scale > 7) {
        ctx.fillStyle = labelColour;
        ctx.fillText(node.name, sx + node.r + 3, sy + 3);
      }
    });
    ctx.globalAlpha = 1;
  }

  // One run owns this canvas's animation. It stops itself once the layout
  // has settled (frame 320) and nothing is interacting — a `dirty` flag set
  // by ticks-still-settling and by pan/click keeps it going only as long as
  // something is actually changing, instead of drawing forever.
  const run = { stopped: false, frameId: null, dirty: true, onMouseUp: null };
  graphRun = run;
  let frames = 0;

  function schedule() {
    if (run.stopped) return;
    run.frameId = requestAnimationFrame(loop);
  }
  function wake() {
    run.dirty = true;
    if (run.frameId === null) schedule();
  }
  function loop() {
    run.frameId = null;
    if (run.stopped) return;
    if (frames < 320) { tick(); frames++; run.dirty = true; }
    if (run.dirty) {
      draw();
      run.dirty = false;
      schedule();
    }
  }
  schedule();

  canvas.addEventListener("mousedown", (event) => {
    dragging = true; lastX = event.offsetX; lastY = event.offsetY;
  });
  run.onMouseUp = () => { dragging = false; };
  window.addEventListener("mouseup", run.onMouseUp);
  canvas.addEventListener("mousemove", (event) => {
    if (!dragging) return;
    panX += event.offsetX - lastX; panY += event.offsetY - lastY;
    lastX = event.offsetX; lastY = event.offsetY;
    wake();
  });
  canvas.addEventListener("click", (event) => {
    // Screen → world: draw() now computes each node's screen position by
    // hand as sx = panX + node.x*scale (toScreen()), the same linear map
    // ctx.translate(panX,panY)+ctx.scale(scale) used to apply — a world
    // point p still renders at p*scale + pan. Inverting that — undo the pan
    // translation, then divide by scale — recovers the world-space point
    // under the cursor at any zoom level, which is what node.x/node.y (also
    // world-space) must be compared against. The formula is unchanged from
    // before; only how draw() gets to sx/sy changed.
    const x = (event.offsetX - panX) / scale, y = (event.offsetY - panY) / scale;
    let hit = null;
    nodes.forEach((node, i) => {
      if (Math.hypot(node.x - x, node.y - y) <= node.r + 4) hit = i;
    });
    focus = hit === focus ? null : hit;
    wake();
  });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    // Keep the world point under the cursor fixed: solve pan so that
    // cursor = worldUnderCursor*newScale + pan, using the worldUnderCursor
    // computed from the *old* pan/scale before either changes.
    const cx = event.offsetX, cy = event.offsetY;
    const worldX = (cx - panX) / scale, worldY = (cy - panY) / scale;
    const factor = Math.exp(-event.deltaY * 0.001);
    scale = Math.min(5, Math.max(0.2, scale * factor));
    panX = cx - worldX * scale;
    panY = cy - worldY * scale;
    wake();
  }, { passive: false });
  canvas.addEventListener("dblclick", () => {
    panX = 0; panY = 0; scale = 1;
    wake();
  });
};

Brain.render.corpus = async function () {
  const target = document.getElementById("tab-corpus");
  if (target.dataset.loaded) return;
  target.dataset.loaded = "1";

  const data = await Brain.fetchJSON("/api/corpus");
  if (data.empty) return;

  const groups = new Map();
  data.documents.forEach((doc) => {
    const key = doc.source + " › " + doc.folder;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(doc);
  });

  const bare = data.documents.filter((d) => !d.has_entities).length;
  target.appendChild(Brain.el("p", {
    class: "hint",
    text: data.documents.length + " documents. " + bare + " have no entities yet (shown faded).",
  }));

  [...groups.entries()].sort().forEach(([key, docs]) => {
    const group = Brain.el("div", { class: "group" });
    group.appendChild(Brain.el("h3", { text: key + " · " + docs.length }));
    const tree = Brain.el("div", { class: "tree" });
    docs.sort((a, b) => b.chunks - a.chunks).forEach((doc) => {
      const leaf = Brain.el("div", { class: "leaf" + (doc.has_entities ? "" : " bare") });
      leaf.style.flexGrow = String(Math.max(1, doc.chunks));
      leaf.style.flexBasis = Math.min(220, 34 + doc.chunks * 3) + "px";
      leaf.title = doc.path + "\n" + (doc.date || "") + " · " + doc.chunks + " chunks";
      leaf.textContent = doc.title;
      tree.appendChild(leaf);
    });
    group.appendChild(tree);
    target.appendChild(group);
  });
};

// Node-only export for the physics test (plain CommonJS check — no bundler,
// no effect in the browser where `module` is undefined).
if (typeof module !== "undefined" && module.exports) {
  module.exports = { graphTick, GRAPH_PHYSICS };
}
