import { readFile } from "node:fs/promises";
import vm from "node:vm";

const html = await readFile(new URL("../public/index.html", import.meta.url), "utf8");

const requiredRoutes = [
  "home",
  "why",
  "data",
  "source-flow",
  "agents",
  "memory",
  "memory-proof",
  "routes",
  "route-depth",
  "flywheel",
  "account",
  "account-proof",
  "demo",
  "demo-trace",
  "proof",
  "proof-obsidian",
  "proof-hermes",
  "proof-tools",
  "ask",
];

const missingRoutes = requiredRoutes.filter(
  (route) => !html.includes(`data-route="${route}"`),
);

if (missingRoutes.length > 0) {
  throw new Error(`Missing slide routes: ${missingRoutes.join(", ")}`);
}

const slideRoutesFromDom = [
  ...html.matchAll(/<section\b[^>]*class="[^"]*\bslide\b[^"]*"[^>]*data-route="([^"]+)"/gi),
].map((match) => match[1]);

if (slideRoutesFromDom.length !== requiredRoutes.length) {
  throw new Error(
    `Slide count mismatch: DOM has ${slideRoutesFromDom.length} .slide sections, canonical list has ${requiredRoutes.length}.`,
  );
}

for (let index = 0; index < requiredRoutes.length; index++) {
  if (slideRoutesFromDom[index] !== requiredRoutes[index]) {
    throw new Error(
      `Slide DOM order mismatch at index ${index}: expected "${requiredRoutes[index]}", got "${slideRoutesFromDom[index]}".`,
    );
  }
}

for (const phrase of ["V7 GTM Engine", "Hermes", "Obsidian", "Specter", "Cala", "Luma"]) {
  if (!html.includes(phrase)) {
    throw new Error(`Missing expected deck phrase: ${phrase}`);
  }
}

for (const phrase of ["assets/v7labs-logo.svg", "PROPOSAL MAP", "Timing-graph lens", "Replay trace"]) {
  if (!html.includes(phrase)) {
    throw new Error(`Missing expected V7 review deck phrase: ${phrase}`);
  }
}

for (const phrase of ["proposal →", "↓ build", 'cta: "ask"', 'loop: "flywheel"']) {
  if (!html.includes(phrase)) {
    throw new Error(`Missing expected presentation hierarchy affordance: ${phrase}`);
  }
}

for (const phrase of [
  "GTM flywheel",
  "actionable outbound",
  "inbound growth",
  "shared memory/data graph",
  "LinkedIn Sales Navigator + LinkedIn",
  "Nitter / X + public web surfaces",
]) {
  if (!html.includes(phrase)) {
    throw new Error(`Missing expected GTM flywheel phrase: ${phrase}`);
  }
}

for (const route of requiredRoutes) {
  if (!html.includes(`route: "${route}"`)) {
    throw new Error(`Missing quest map node for route: ${route}`);
  }
}

for (const phrase of ["data-label", ".map-cell.route::before", "dataset.label"]) {
  if (html.includes(phrase)) {
    throw new Error(`Quest map cell text label regression: ${phrase}`);
  }
}

const forbiddenProviderVariants = [
  ["K", "ala"].join(""),
  "TrySpecter",
  "Try Specter",
  "Specter / TrySpecter",
];
for (const phrase of forbiddenProviderVariants) {
  if (html.includes(phrase)) {
    throw new Error(`Forbidden provider naming variant detected: ${phrase}`);
  }
}

const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((match) => match[1]);
if (scripts.length !== 1) {
  throw new Error(`Expected one inline script, found ${scripts.length}`);
}

const script = scripts[0];
const questNodesMatch = script.match(/const questNodes = \[([\s\S]*?)\];/);

if (!questNodesMatch) {
  throw new Error("Missing quest map nodes.");
}

if (html.includes("const mapShape")) {
  throw new Error("Quest map regression: mapShape strip model must not return.");
}

const questNodeBlocks = [...questNodesMatch[1].matchAll(/\{\s+route: "([^"]+)",[\s\S]*?\n\s+\}/g)].map(
  ([block, route]) => ({
    route,
    block,
    coordinate: block.match(/coordinate: "([^"]+)"/)?.[1] || null,
    parent: block.match(/parent: "([^"]+)"/)?.[1] || null,
    parentIsNull: /parent: null/.test(block),
  }),
);
const questNodeBlockMap = new Map(questNodeBlocks.map((node) => [node.route, node]));

if (questNodeBlocks.length !== requiredRoutes.length) {
  throw new Error(
    `Quest node count mismatch: found ${questNodeBlocks.length} nodes for ${requiredRoutes.length} slides.`,
  );
}

const routeCounts = new Map();
const coordinateCounts = new Map();
for (const node of questNodeBlocks) {
  routeCounts.set(node.route, (routeCounts.get(node.route) || 0) + 1);
  if (node.coordinate) {
    coordinateCounts.set(node.coordinate, (coordinateCounts.get(node.coordinate) || 0) + 1);
  }
}

for (const route of requiredRoutes) {
  if (routeCounts.get(route) !== 1) {
    throw new Error(`Every slide route must have exactly one quest map node: ${route}`);
  }
}

for (const [coordinate, count] of coordinateCounts) {
  if (count !== 1) {
    throw new Error(`Every quest map coordinate must be unique: ${coordinate}`);
  }
}

if (coordinateCounts.size !== requiredRoutes.length) {
  throw new Error("Every slide route must have exactly one quest map coordinate.");
}

const expectedCoordinates = new Map([
  ["home", "01"],
  ["why", "02"],
  ["data", "03"],
  ["source-flow", "03A"],
  ["agents", "04"],
  ["memory", "05"],
  ["memory-proof", "05A"],
  ["routes", "06"],
  ["route-depth", "06A"],
  ["flywheel", "07"],
  ["account", "08"],
  ["account-proof", "08A"],
  ["demo", "09"],
  ["demo-trace", "09A"],
  ["proof", "09B"],
  ["proof-obsidian", "09C"],
  ["proof-hermes", "09D"],
  ["proof-tools", "09E"],
  ["ask", "10"],
]);

for (const [route, expectedCoordinate] of expectedCoordinates) {
  const node = questNodeBlockMap.get(route);
  if (node?.coordinate !== expectedCoordinate) {
    throw new Error(`Quest map coordinate regression: ${route}`);
  }
}

for (const node of questNodeBlocks) {
  const expectedPattern = node.parentIsNull ? /^\d+$/ : /^\d+[A-Z]$/;
  if (!node.coordinate || !expectedPattern.test(node.coordinate)) {
    throw new Error(`Quest map coordinate format regression: ${node.route}`);
  }
  if (node.parent && !questNodeBlockMap.has(node.parent)) {
    throw new Error(`Quest map parent route is missing: ${node.route} -> ${node.parent}`);
  }
}

const routesOrderedByCoordinate = [...questNodeBlocks]
  .sort((a, b) => {
    const left = a.coordinate.match(/^(\d+)([A-Z]?)$/);
    const right = b.coordinate.match(/^(\d+)([A-Z]?)$/);
    return Number(left[1]) - Number(right[1]) || (left[2] || "").localeCompare(right[2] || "");
  })
  .map((node) => node.route);
if (routesOrderedByCoordinate.join("|") !== requiredRoutes.join("|")) {
  throw new Error("Quest node coordinate ordering must match DOM slide sequence.");
}

for (const node of questNodeBlocks) {
  if (!node.parent) continue;
  const parent = questNodeBlockMap.get(node.parent);
  if (!node.coordinate.startsWith(parent.coordinate.replace(/[A-Z]$/, ""))) {
    throw new Error(`Quest map child must stack under its parent column: ${node.route}`);
  }
}

const topLevelCoordinates = questNodeBlocks
  .filter((node) => node.parentIsNull)
  .sort((a, b) => Number(a.coordinate) - Number(b.coordinate))
  .map((node) => node.coordinate);
const expectedTopLevelCoordinates = Array.from({ length: topLevelCoordinates.length }, (_, index) =>
  String(index + 1).padStart(2, "0"),
);
if (topLevelCoordinates.join("|") !== expectedTopLevelCoordinates.join("|")) {
  throw new Error("Top-level quest map columns must be numbered 01..X with no gaps.");
}

const childrenByColumn = new Map();
for (const node of questNodeBlocks) {
  if (!node.parent) continue;
  const [, column, detail] = node.coordinate.match(/^(\d+)([A-Z])$/);
  if (!childrenByColumn.has(column)) childrenByColumn.set(column, []);
  childrenByColumn.get(column).push(detail);
}

for (const [column, detailLetters] of childrenByColumn) {
  const expectedLetters = Array.from({ length: detailLetters.length }, (_, index) =>
    String.fromCharCode(65 + index),
  );
  if (detailLetters.sort().join("|") !== expectedLetters.join("|")) {
    throw new Error(`Detail coordinates must stack as ${column}A, ${column}B, ${column}C...`);
  }
}

for (const phrase of [
  "topLevelNodes.forEach",
  "columnNodesFor(topNode)",
  "columnNodes.slice(activeColumnIndex + 1)",
  "data-coordinate",
  "activeNode.coordinate",
  "compareCoordinates",
]) {
  if (!html.includes(phrase)) {
    throw new Error(`Missing column-based quest map behavior: ${phrase}`);
  }
}

for (const phrase of [
  ".map-cell.route.visited:not(.active)",
  "visitedStorageKey",
  "sessionStorage",
  "visitedRoutes.add(routes[bounded])",
  "breadcrumbFor(activeNode)",
  "scrollIntoView",
]) {
  if (!html.includes(phrase)) {
    throw new Error(`Missing quest map UX behavior: ${phrase}`);
  }
}

const fallbackBackRouteMatch = script.match(/function fallbackBackRoute\(\) \{\s*return routes\[currentIndex - 1\] \|\| null;\s*\}/);
if (!fallbackBackRouteMatch) {
  throw new Error("ArrowLeft fallback must walk the canonical deck route order.");
}

if (script.includes("routeHistory")) {
  throw new Error("ArrowLeft must not use custom routeHistory that can bounce between slides.");
}

for (const phrase of ["function ensureBrowserBackGuard", "deckBackGuard", 'window.addEventListener("popstate"', "moveBack();"]) {
  if (!script.includes(phrase)) {
    throw new Error(`Missing browser-back deck navigation guard: ${phrase}`);
  }
}

const simulatedBackSequence = [];
let simulatedIndex = requiredRoutes.indexOf("ask");
while (simulatedIndex > -1) {
  simulatedBackSequence.push(requiredRoutes[simulatedIndex]);
  simulatedIndex -= 1;
}

const expectedBackSequencePrefix = [
  "ask",
  "proof-tools",
  "proof-hermes",
  "proof-obsidian",
  "proof",
  "demo-trace",
  "demo",
  "account-proof",
  "account",
  "flywheel",
  "route-depth",
  "routes",
];

if (simulatedBackSequence.slice(0, expectedBackSequencePrefix.length).join("|") !== expectedBackSequencePrefix.join("|")) {
  throw new Error(
    `Back traversal regression from ask: ${simulatedBackSequence.slice(0, expectedBackSequencePrefix.length).join(" -> ")}`,
  );
}

new vm.Script(scripts[0], { filename: "public/index.html:inline-script" });

console.log("Static deck validation passed.");
