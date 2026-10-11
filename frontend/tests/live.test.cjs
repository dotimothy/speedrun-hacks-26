const { test } = require("node:test");
const assert = require("node:assert/strict");
const ts = require("typescript");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
function load(name) {
  const filename = path.resolve(__dirname, "../src", name);
  const mod = new Module(filename, module);
  mod._compile(
    ts.transpileModule(fs.readFileSync(filename, "utf8"), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2020,
      },
    }).outputText,
    filename,
  );
  return mod.exports;
}
const { initialSession, transition } = load("workspace/session.ts");
const { requestSnapshot, parseResult } = load("workspace/backend.ts");
const { Recorder, wavDuration } = load("audio/recorder.ts");
const { adaptSnapshot, appendUtterance, generateAssistance } = load("workspace/v2.ts");
const { readModelPreferences, saveModelPreferences } = load("workspace/modelPreferences.ts");

test("assistant endpoint retains the summary and question without changing the reviewed record", async t => {
  const snapshot = { session_id: "assistant", event_seq: 4, revision: 0, segments: [], accepted_record: {},
    suggestions: [], narrative: {}, published: [], unresolved: [], review_flags: [],
    assistant: { status: "ready", summary: "Caller reports chest pain.", follow_up_question: "Where are you?",
      source_segment_ids: ["seg-1"], source_revision: 0, source_segment_count: 1, model: "configured-model" } };
  const controller = new AbortController();
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/api/v2/sessions/assistant/assist");
    assert.equal(options.method, "POST");
    assert.equal(options.signal, controller.signal);
    return new Response(JSON.stringify({ snapshot, cached: false, stale: false }), { status: 200 });
  });
  const raw = await generateAssistance("assistant", controller.signal);
  assert.deepEqual(raw.assistant, snapshot.assistant);
  assert.deepEqual(raw.accepted_record, {});
  assert.deepEqual(adaptSnapshot(raw, "live").server.assistant, snapshot.assistant);
});

test("model settings persist and exclude engines no longer available", t => {
  let stored = null;
  t.mock.method(globalThis, "fetch", async () => { throw new Error("Unexpected request"); });
  const oldStorage = globalThis.localStorage;
  globalThis.localStorage = { getItem: () => stored, setItem: (_key, value) => { stored = value; } };
  t.after(() => { globalThis.localStorage = oldStorage; });
  const models = { decision_engines: [{ name: "rules", ready: true }, { name: "jev", ready: false }],
    whisper_sizes: ["small", "base"], whisper_default: "small" };
  saveModelPreferences({ engine: "rules", whisperSize: "base" });
  assert.deepEqual(readModelPreferences(models), { engine: "rules", whisperSize: "base" });
  saveModelPreferences({ engine: "jev", whisperSize: "unavailable" });
  assert.deepEqual(readModelPreferences(models), { engine: "", whisperSize: "small" });
  stored = "bad json";
  assert.deepEqual(readModelPreferences(models), { engine: "", whisperSize: "small" });
});

test("selected decision and speech models reach both text and audio requests", async t => {
  const snapshot = { session_id: "models", event_seq: 0, revision: 0, segments: [], accepted_record: {},
    suggestions: [], narrative: {}, published: [], unresolved: [], review_flags: [] };
  const payloads = [];
  t.mock.method(globalThis, "fetch", async (_url, options) => {
    if (options?.method === "POST") {
      const payload = options.body instanceof FormData ? JSON.parse(options.body.get("payload")) : JSON.parse(options.body);
      payloads.push(payload);
      return new Response(JSON.stringify({ snapshot }), { status: 200 });
    }
    return new Response(JSON.stringify(snapshot), { status: 200 });
  });
  for (const input of ["A synthetic caller statement", new Blob(["audio"], { type: "audio/wav" })]) {
    await appendUtterance("models", { input, clientId: "test-" + payloads.length, speaker: "caller",
      decisionEngine: "rules", whisperSize: "base" }, new AbortController().signal);
  }
  assert.equal(payloads.length, 2);
  for (const payload of payloads) {
    assert.equal(payload.decision_engine, "rules");
    assert.equal(payload.whisper_size, "base");
  }
});

test("scenario decisions and cumulative observations remain reviewable through the UI adapter", () => {
  const focus = { scenario: "medical", title: "Medical", purpose: "Document changes", decisions: [
    { field: "medical_course", value: "worsening", status: "pending_review", suggestion_id: "course" },
  ] };
  const raw = {
    session_id: "detail", revision: 1, event_seq: 2, detected_language: "en", segments: [],
    accepted_record: { symptom_details: { value: ["Her face is drooping.", "She cannot move her left arm."], presence: "stated", history: [] } },
    suggestions: [
      { id: "course", field: "medical_course", kind: "addition", proposed_value: "worsening", status: "pending", evidence: [], conflict_options: [] },
      { id: "changes", field: "symptom_changes", kind: "addition", proposed_value: "Symptoms changed a minute ago.", status: "pending", evidence: [], conflict_options: [] },
    ],
    narrative: { text: "Draft", edited: false }, published: [], unresolved: [], review_flags: [], diagnostics: {},
    decision_focus: focus, relevant_fields: ["medical_course", "symptom_details", "symptom_changes"],
    follow_up_questions: [],
  };
  const s = adaptSnapshot(raw, "transcript");
  assert.equal(s.record.symptom_details, "Her face is drooping.\nShe cannot move her left arm.");
  assert.equal(s.record.medical_course, undefined);
  assert.equal(s.suggestions[0].value, "worsening");
  assert.equal(s.suggestions[1].field, "symptom_changes");
  assert.deepEqual(s.server.decision_focus, focus);
});

test("all four supplied demo call scripts are available without changing caller wording", () => {
  const { detailedScenarios } = load("workspace/scenarios.ts");
  const cases = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../backend/evals/demo_call_cases.json"), "utf8"));
  for (const call of cases) {
    const sample = detailedScenarios.find(s => s.name === "Provided call: " + call.name);
    assert.deepEqual(sample.turns, call.turns.filter(t => t.speaker === "caller").map(t => t.text));
  }
});

test("scenario details survive the adapter as reviewable fields and published facts", () => {
  const raw = {
    session_id: "medical", revision: 2, event_seq: 4, detected_language: "en",
    segments: [{ id: "s1", speaker: "caller", original_text: "He is 67 years old.", translation: null }],
    accepted_record: { patient_age: { value: "He is 67 years old.", presence: "stated", history: [] } },
    suggestions: [{ id: "history", field: "medical_history", kind: "addition", proposed_value: "History of asthma.",
      status: "pending", evidence: [], conflict_options: [] }],
    narrative: { text: "Patient age: 67 years old.", edited: false, editor_text: null },
    published: [{ revision: 2, record: { patient_age: "He is 67 years old." }, narrative: "Patient age: 67 years old.",
      unresolved: [], open_flags: [] }],
    unresolved: [], review_flags: [], diagnostics: {},
    relevant_fields: ["patient_age", "medical_history"],
    follow_up_questions: [{ field: "breathing", text: "What can you tell me about breathing?" }],
  };
  const session = adaptSnapshot(raw, "transcript");
  assert.equal(session.record.patient_age, "He is 67 years old.");
  assert.equal(session.record.medical_history, undefined);
  assert.equal(session.suggestions[0].field, "medical_history");
  assert.equal(session.published.record.patient_age, "He is 67 years old.");
  assert.deepEqual(session.server.follow_up_questions, raw.follow_up_questions);
});
function result(
  values = { location_text: "124 Oak Street" },
  text = "At 124 Oak Street",
) {
  return {
    session_id: "call-a",
    transcript_original: text,
    transcript_english: text,
    detected_language: "en",
    latency_ms: { server_total: 20 },
    predictions: Object.entries(values).map(([label, value]) => ({
      label,
      value,
      verification_state: "unverified",
      decided_on: "rules",
      evidence: { text, source: "original" },
      conflict: false,
    })),
  };
}
function ingest(s, response = result(), sequence = s.lastSequence + 1) {
  return transition(s, {
    type: "result",
    id: s.id,
    sequence,
    at: "00:05",
    response,
  });
}
function review(s, decision = "accept", value) {
  return transition(s, {
    type: "review",
    id: s.suggestions.at(-1).id,
    decision,
    value,
  });
}
test("live corrections preserve the accepted record and original evidence snapshot", () => {
  let s = review(ingest(initialSession("live", "call-a")));
  const original = s.segments[0];
  s = ingest(
    s,
    result({ location_text: "142 Oak Street" }, "Sorry, 142 Oak Street"),
  );
  assert.equal(s.record.location, "124 Oak Street");
  assert.equal(s.suggestions.at(-1).kind, "change");
  assert.equal(s.suggestions.at(-1).source, 1);
  assert.equal(s.segments[0], original);
  assert.equal(s.suggestions[0].evidence.text, "At 124 Oak Street");
  assert.equal(review(s).record.location, "142 Oak Street");
});
test("identical passes do not resurrect accepted, edited or rejected proposals", () => {
  for (const [decision, value] of [
    ["accept"],
    ["accept", "124 Oak, apartment 2"],
    ["reject"],
  ]) {
    let s = review(
      ingest(initialSession("transcript", "call-a")),
      decision,
      value,
    );
    const prior = s.suggestions;
    s = ingest(s);
    assert.equal(s.suggestions.length, 1);
    assert.equal(s.suggestions[0], prior[0]);
    assert.equal(s.segments.length, 1);
    assert.equal(s.suggestions.filter((x) => x.status === "pending").length, 0);
  }
});
test("old responses and responses from another session cannot enter the record", () => {
  const s = ingest(initialSession("live", "call-a"));
  assert.equal(ingest(s, result(), 1), s);
  assert.equal(ingest(s, { ...result(), session_id: "call-b" }), s);
  const reset = transition(s, { type: "reset", mode: "live", id: "call-b" });
  assert.equal(ingest(reset, result(), 4), reset);
});
test("withdrawal removes pending proposals but never clears accepted fields", () => {
  let s = ingest(initialSession("live", "call-a"));
  s = ingest(s, result({ location_text: "unknown" }));
  assert.equal(s.suggestions[0].status, "superseded");
  s = review(ingest(s));
  s = ingest(s, result({}));
  assert.equal(s.record.location, "124 Oak Street");
  assert.equal(s.suggestions.filter((x) => x.status === "pending").length, 0);
});
test("false and zero are meaningful values, while null and unknown remain absent", () => {
  const s = ingest(
    initialSession("live", "call-a"),
    result({
      caller_safe: false,
      persons_involved: 0,
      weapons_present: "unknown",
      location_text: null,
    }),
  );
  assert.deepEqual(
    s.suggestions.map((x) => [x.field, x.value]),
    [
      ["safety", "false"],
      ["people", "0"],
    ],
  );
});
test("late backend updates cannot change an already published brief", () => {
  let s = review(ingest(initialSession("live", "call-a")));
  s = transition(s, { type: "publish", note: "First brief" });
  const published = s.published;
  s = review(ingest(s, result({ location_text: "142 Oak Street" })));
  assert.equal(s.published, published);
  assert.equal(s.published.record.location, "124 Oak Street");
  assert.equal(s.record.location, "142 Oak Street");
});
test("malformed transport responses are rejected before they reach state", () => {
  for (const bad of [
    null,
    {},
    { ...result(), predictions: [null] },
    { ...result(), latency_ms: "20" },
    { ...result(), detected_language: {} },
    {
      ...result(),
      predictions: [
        { ...result().predictions[0], evidence: { text: "x", source: "bad" } },
      ],
    },
  ]) {
    assert.throws(() => parseResult(bad));
  }
});
test("transcript and WAV transport retain the same session ID without sending review decisions", async (t) => {
  const requests = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    requests.push({ url, ...init });
    return { ok: true, json: async () => result() };
  });
  const signal = new AbortController().signal;
  await requestSnapshot("call-a", "Conversation", signal);
  await requestSnapshot(
    "call-a",
    new Blob(["audio"], { type: "audio/wav" }),
    signal,
  );
  assert.equal(requests[0].url, "/api/process");
  assert.deepEqual(JSON.parse(requests[0].body), {
    session_id: "call-a",
    transcript: "Conversation",
  });
  assert.deepEqual(JSON.parse(requests[1].body.get("payload")), {
    session_id: "call-a",
    whisper_size: "small",
  });
  assert.equal(requests[1].body.get("audio").name, "call.wav");
  assert.equal(requests[1].signal, signal);
});
test("failed requests and mismatched session IDs surface errors", async (t) => {
  const mock = t.mock.method(globalThis, "fetch", async () => ({
    ok: false,
    status: 503,
    text: async () => "Model unavailable",
  }));
  await assert.rejects(
    requestSnapshot("call-a", "text", new AbortController().signal),
    /503.*Model unavailable/,
  );
  mock.mock.mockImplementation(async () => ({
    ok: true,
    json: async () => ({ ...result(), session_id: "wrong" }),
  }));
  await assert.rejects(
    requestSnapshot("call-a", "text", new AbortController().signal),
    /different session/,
  );
});
function audioEnvironment(t, getUserMedia) {
  const contexts = [];
  class Context {
    constructor() {
      this.state = "running";
      this.sampleRate = 48000;
      this.closed = false;
      contexts.push(this);
    }
    async close() {
      this.closed = true;
    }
    createMediaStreamSource() {
      return { connect() {} };
    }
    createAnalyser() {
      return {
        connect() {},
        disconnect() {},
        getFloatTimeDomainData(a) {
          a.fill(0.1);
        },
      };
    }
    createScriptProcessor() {
      return (this.processor = { connect() {}, disconnect() {} });
    }
    createGain() {
      return { gain: {}, connect() {}, disconnect() {} };
    }
  }
  const oldWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const oldNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "window", {
    value: { AudioContext: Context },
    configurable: true,
  });
  Object.defineProperty(globalThis, "navigator", {
    value: { mediaDevices: { getUserMedia } },
    configurable: true,
  });
  t.after(() => {
    if (oldWindow) Object.defineProperty(globalThis, "window", oldWindow);
    else delete globalThis.window;
    if (oldNavigator)
      Object.defineProperty(globalThis, "navigator", oldNavigator);
    else delete globalThis.navigator;
  });
  return contexts;
}
test("microphone permission denial closes the audio context", async (t) => {
  const contexts = audioEnvironment(t, async () => {
    throw new Error("Permission denied");
  });
  const recorder = new Recorder();
  await assert.rejects(recorder.start(), /Permission denied/);
  assert.equal(contexts[0].closed, true);
  assert.equal(recorder.recording, false);
});
test("permission granted after disposal immediately stops the late microphone stream", async (t) => {
  let resolve;
  let stopped = 0;
  const contexts = audioEnvironment(t, () => new Promise((r) => (resolve = r)));
  const recorder = new Recorder();
  const pending = recorder.start();
  recorder.dispose();
  resolve({
    getTracks: () => [
      {
        stop() {
          stopped++;
        },
      },
    ],
  });
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(stopped, 1);
  assert.equal(contexts[0].closed, true);
});
test("interim WAV capture leaves the microphone open; final capture releases it", async (t) => {
  let stopped = 0;
  const contexts = audioEnvironment(t, async () => ({
    getTracks: () => [
      {
        stop() {
          stopped++;
        },
      },
    ],
  }));
  const recorder = new Recorder();
  await recorder.start();
  contexts[0].processor.onaudioprocess({
    inputBuffer: { getChannelData: () => new Float32Array(48000).fill(0.2) },
  });
  const interim = recorder.snapshot();
  assert.equal(await wavDuration(interim), 1);
  assert.equal(stopped, 0);
  assert.equal(recorder.recording, true);
  const final = await recorder.stop();
  assert.equal(await wavDuration(final), 1);
  assert.equal(stopped, 1);
  assert.equal(contexts[0].closed, true);
  assert.equal(contexts[0].processor.onaudioprocess, null);
  assert.equal(recorder.snapshot(), null);
});

test("saving a retained edit manually leaves the newer proposal available for review", () => {
  let s = ingest(initialSession("live", "call-a"));
  const editedProposal = s.suggestions[0];
  s = ingest(
    s,
    result({ location_text: "142 Oak Street" }, "Correction: 142 Oak Street"),
  );
  assert.equal(s.suggestions[0].status, "superseded");
  s = transition(s, {
    type: "manual",
    field: editedProposal.field,
    value: "124 Oak Street, apartment 2",
  });
  assert.equal(s.record.location, "124 Oak Street, apartment 2");
  assert.equal(s.suggestions.at(-1).status, "pending");
  assert.equal(s.suggestions.at(-1).value, "142 Oak Street");
  assert.equal(review(s).record.location, "142 Oak Street");
});

test("utterance capture drains new audio once while preserving the full recording", async (t) => {
  const contexts = audioEnvironment(t, async () => ({
    getTracks: () => [{ stop() {} }],
  }));
  const recorder = new Recorder();
  await recorder.start();
  const feed = () =>
    contexts[0].processor.onaudioprocess({
      inputBuffer: { getChannelData: () => new Float32Array(48000).fill(0.2) },
    });
  feed();
  assert.equal(await wavDuration(recorder.takeUtterance()), 1);
  assert.equal(recorder.takeUtterance(), null);
  feed();
  assert.equal(await wavDuration(recorder.takeUtterance()), 1);
  assert.equal(await wavDuration(await recorder.stop()), 2);
});
