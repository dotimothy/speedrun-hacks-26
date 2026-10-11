const { test } = require("node:test");
const assert = require("node:assert/strict");
const ts = require("typescript");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
const filename = path.resolve(__dirname, "../src/workspace/session.ts");
const compiled = new Module(filename, module);
compiled._compile(
  ts.transpileModule(fs.readFileSync(filename, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
  }).outputText,
  filename,
);
const { initialSession, transition } = compiled.exports;
const accept = (s, id, value) =>
  transition(s, { type: "review", id, decision: "accept", value });
const next = (s) => transition(s, { type: "next" });
test("caller correction preserves accepted location until explicitly reviewed", () => {
  let s = accept(initialSession(), "location-1");
  s = next(next(s));
  assert.equal(s.record.location, "124 Oak Street");
  assert.equal(
    s.suggestions.find((x) => x.id === "location-2").status,
    "pending",
  );
  s = accept(s, "location-2");
  assert.equal(s.record.location, "142 Oak Street");
});
test("stale and duplicate suggestions cannot overwrite newer decisions", () => {
  let s = next(next(initialSession()));
  assert.equal(
    s.suggestions.find((x) => x.id === "location-1").status,
    "superseded",
  );
  assert.equal(accept(s, "location-1"), s);
  s = accept(s, "location-2", "142 Oak Street, unit 2");
  assert.equal(accept(s, "location-2"), s);
  assert.equal(s.record.location, "142 Oak Street, unit 2");
});
test("reject and blank edits leave accepted record unchanged", () => {
  let s = accept(initialSession(), "location-1");
  s = next(next(s));
  assert.equal(accept(s, "location-2", "  "), s);
  s = transition(s, { type: "review", id: "location-2", decision: "reject" });
  assert.equal(s.record.location, "124 Oak Street");
});
test("published snapshot stays unchanged until next explicit publication", () => {
  let s = accept(initialSession(), "location-1");
  s = transition(s, { type: "publish", note: "Operator context" });
  const snapshot = s.published;
  assert.equal(snapshot.version, 1);
  assert.ok(snapshot.unresolved.includes("Callback number not recorded"));
  s = accept(next(next(s)), "location-2");
  assert.equal(s.published, snapshot);
  assert.equal(s.published.record.location, "124 Oak Street");
  s = transition(s, { type: "publish", note: "Correction reviewed" });
  assert.equal(s.published.record.location, "142 Oak Street");
  assert.equal(s.published.version, 2);
});
test("manual edits preserve review queue and reset clears all session state", () => {
  let s = transition(initialSession(), {
    type: "manual",
    field: "location",
    value: "Operator-entered location",
  });
  assert.equal(s.suggestions[0].status, "pending");
  s = transition(s, { type: "publish", note: "Test" });
  assert.deepEqual(transition(s, { type: "reset" }), initialSession());
  s = transition(s, { type: "manual", field: "location", value: "" });
  assert.equal(s.record.location, undefined);
});
test("empty records cannot publish; stepping past final segment is a no-op", () => {
  const s = initialSession();
  assert.equal(transition(s, { type: "publish", note: "Only a note" }), s);
  let end = next(next(next(s)));
  assert.equal(next(end), end);
});
