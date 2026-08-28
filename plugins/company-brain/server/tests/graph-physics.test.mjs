// Assertion harness for the graph force-layout in
// plugins/company-brain/server/company_brain/assets/app.js.
//
// Regression proof: 400 nodes on a ring start ~3px apart. With the
// original unbounded repulsion (900 / distance^2, distance floored only at
// 0.01) and no per-frame velocity clamp, every node's position is NaN by
// frame 10 (see graph-physics-repro.mjs). This harness turns that repro
// into assertions: run with `node --test graph-physics.test.mjs`.

import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const appJsPath = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "..", "company_brain", "assets", "app.js",
);
const { graphTick } = require(appJsPath);

const WIDTH = 1370, HEIGHT = 560;

// Synthetic 400-node ring — same construction as app.js uses for the
// initial layout, and the same one graph-physics-repro.mjs proved fails.
function ringNodes(count, width, height) {
  const nodes = [];
  for (let i = 0; i < count; i++) {
    const angle = (i / count) * Math.PI * 2;
    nodes.push({
      x: width / 2 + Math.cos(angle) * Math.min(width, height) * 0.35,
      y: height / 2 + Math.sin(angle) * Math.min(width, height) * 0.35,
      vx: 0, vy: 0,
    });
  }
  return nodes;
}

// A ring of edges (each node linked to the next) — enough to exercise the
// spring force without depending on the live API's edge list.
function ringEdges(count) {
  const edges = [];
  for (let i = 0; i < count; i++) edges.push({ a: i, b: (i + 1) % count });
  return edges;
}

test("400-node ring stays finite, spreads out, and mostly stays onscreen after 320 frames", () => {
  const nodes = ringNodes(400, WIDTH, HEIGHT);
  const edges = ringEdges(400);

  for (let frame = 0; frame < 320; frame++) {
    graphTick(nodes, edges, WIDTH, HEIGHT);
  }

  const nonFinite = nodes.filter((n) => !Number.isFinite(n.x) || !Number.isFinite(n.y));
  assert.equal(nonFinite.length, 0,
    `expected every node finite after 320 frames, got ${nonFinite.length}/${nodes.length} non-finite`);

  const onscreen = nodes.filter((n) => n.x >= 0 && n.x <= WIDTH && n.y >= 0 && n.y <= HEIGHT).length;
  const onscreenRatio = onscreen / nodes.length;
  assert.ok(onscreenRatio >= 0.8,
    `expected >=80% of nodes onscreen, got ${onscreen}/${nodes.length} (${(onscreenRatio * 100).toFixed(1)}%)`);

  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  const spreadX = Math.max(...xs) - Math.min(...xs);
  const spreadY = Math.max(...ys) - Math.min(...ys);
  assert.ok(spreadX > 50 && spreadY > 50,
    `nodes must not collapse to a single point: spreadX=${spreadX.toFixed(1)} spreadY=${spreadY.toFixed(1)}`);
});

test("50-node ring also settles (small-N case must not regress)", () => {
  const nodes = ringNodes(50, WIDTH, HEIGHT);
  const edges = ringEdges(50);

  for (let frame = 0; frame < 320; frame++) {
    graphTick(nodes, edges, WIDTH, HEIGHT);
  }

  const nonFinite = nodes.filter((n) => !Number.isFinite(n.x) || !Number.isFinite(n.y));
  assert.equal(nonFinite.length, 0, "50-node ring must stay finite");

  const onscreen = nodes.filter((n) => n.x >= 0 && n.x <= WIDTH && n.y >= 0 && n.y <= HEIGHT).length;
  assert.ok(onscreen / nodes.length >= 0.8, "50-node ring must mostly stay onscreen");
});
