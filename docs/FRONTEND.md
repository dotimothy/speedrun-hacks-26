# Call-taking workspace

Run `npm ci` and `npm run dev` in `frontend`. The default workspace uses live microphone input; `?input=transcript` opens typed utterance input. Both use `/api/v2`. Scripted rehearsal remains an explicitly selected development fixture. `?view=pipeline` opens the older v1 inspector.

The API defaults to `http://127.0.0.1:8000`. To connect a teammate's API, copy `frontend/.env.example` to `frontend/.env.local`, set `DISPATCHIQ_API_TARGET`, and restart Vite. Deployment needs equivalent `/api` routing. On a backend with models installed, start it using the repository setup instructions. The default decision engine is Laya; the frontend follows deployment configuration rather than silently choosing an alternative.

The design uses one connected workspace: a white accepted incident record, a dark slate bilingual conversation panel, and a pale blue review column. Blue identifies review actions; green is reserved for accepted status. The header shows accepted location and publication state. Source inspection expands original Spanish and illustrative English inline; corrections separate the current record from the proposed replacement. Amber marks caller corrections. Desktop keeps all three visible; narrow layouts stack them. Source selection persists and is keyboard accessible. Status is communicated with words, not only color. Typography uses DM Sans and IBM Plex Mono when the Google Fonts request succeeds, with system fallbacks.

## Demo walkthrough

1. Accept the initial location, 124 Oak Street, and incident description.
2. Select Next segment for callback and caller details. Accept, edit, or dismiss each.
3. Advance again: the caller corrects the address to 142 Oak Street. The accepted address stays at 124 until reviewed.
4. Inspect the source, then accept or edit the correction.
5. Publish a partial or complete record. The Receiving desk displays a snapshot, including unresolved items at publication.
6. Return to the record and advance to the access detail. The receiving snapshot remains unchanged until the next explicit publication.
7. Reset clears all state. Demo state is memory-only and also resets on refresh.

Play scenario advances the scripted text every 6.5 seconds. Displayed timestamps are source-script timestamps, not measured latency or wall-clock call duration. The scripted rehearsal has no audio; switch input mode for microphone capture or WAV playback. Translation is illustrative, not interpreter-verified. Accepted means operator-reviewed; it does not assert translation verification or dispatch clearance.

## V2 integration

`workspace/v2.ts` handles server snapshots, typed field mapping, serialized writes, stable operation IDs, and 409 conflict recovery. The browser renders server state; accept/edit/reject, manual entry, conflict resolution, narratives, and publication go through the v2 API. Incoming snapshots use `event_seq` to prevent older responses rolling back the screen. Scripted rehearsal still uses the local reducer.

### Live workflow

1. Start microphone capture, select the speaker before recording, and press **Send utterance** at a natural pause. Capture continues while the submitted utterance is processed. **Stop recording** sends only the remaining audio. The full recording remains available for local playback.
2. Each audio sample is submitted once, rather than repeatedly sending the accumulated call. Failed utterances stay queued and retry with their original client IDs. A WAV upload is treated as one utterance; use single-speaker synthetic input.
3. Transcript mode sends only the next statement. The backend maintains conversation context. Attribution is manual; caller, call taker, interpreter, and mixed/unknown input are distinguished. Only caller input creates proposals.
4. Inspect original or translated evidence; accept/edit/dismiss proposals. Conflicts require an explicit choice between statements. Reviews don't rerun model inference. An edit interrupted by a superseding proposal stays visible as a manual-entry draft.
5. Inspect the generated narrative or enter a full replacement. Publish sends the reviewed record to the server receiving inbox; the frontend reads that inbox after publication. Copy/download uses the published record.
6. Server records survive switching to another call in the browser, but not a backend restart. Call-history/resume UI and automatic multi-client refresh are not wired yet. Browser refresh currently opens a new call. Browser audio is memory-only.

Mapped fields include location, callback, incident type, people, access notes, injuries, hazards, weapons, and caller safety. Input attribution is not diarization. The frontend labels segments by utterance order: the backend's audio offsets are not yet sufficiently aligned with full-recording playback for source seeking. Continuous automatic utterance segmentation remains future work.

### Backend fixes found during integration

- Reject stale publication revisions instead of silently sharing a newer record.
- Use a fresh explicit address/phone in a caller utterance when constructing conflict choices; the cumulative rule extractor otherwise returns the first value in the call.
- Exclude operator/interpreter/unknown segments from suggestion diffing, including correction-cue detection.

The workspace has no CAD vendor connection or unit-dispatch actions. Autonomous replies are outside the operator documentation flow.

## Validation

`npm test` covers state transitions, publication protection, transport errors, and mocked microphone lifecycle, including non-overlapping utterance extraction. `npm run build` checks TypeScript and the Vite bundle.

Run the backend session suites with `PYTHONPATH=backend python -m pytest backend/tests/test_live_api.py backend/tests/test_live_store.py backend/tests/test_live_diffing.py`. They do not require model weights.

`PYTHONPATH=backend V2_PYTHON=/path/to/python node frontend/tests/v2.integration.cjs` starts a temporary API, exercises real HTTP through the frontend adapter, and terminates the API afterward. It checks create, append, deduplication, accept, caller correction, operator exclusion, publication/inbox, stale publication rejection, and conflict resolution. It uses real rules/session state, not Laya or Whisper. The Python environment needs FastAPI, uvicorn, pydantic, python-multipart, httpx, and pytest.

Actual microphone/ASR/Laya execution and browser interaction QA remain unverified. Browser testing is blocked by the user's 20 GiB disk-space safeguard.

## Design exclusions

Keep the interface grounded in call-taking. Exclude promotional headlines and slogans from the working screen, decorative section numbers, fake operator avatars, decorative heading icons, nested cards around every update, pill badges for ordinary labels, invented metrics, gradient/glass effects, and an all-over accent-color wash. Use plain labels, ruled rows, neutral working surfaces, and color tied to a meaningful state. Preserve visible focus, readable supporting text, and textual state labels. A functional three-pane workspace is appropriate here; a blanket ban on three columns is not.

The second design pass replaces the slogan with the call ID, flattens review cards into rows, removes numbered headings/avatar/extra icons, and enlarges supporting text. The dark conversation pane remains distinct from the accepted record. These decisions were informed by the [anti-AI-slop design catalog](https://github.com/prangishviliAbe/agent-skills/blob/main/anti-ai-slop-design/SKILL.md), rather than treating every recommendation in that catalog as a requirement.

The third pass follows the [Lenny’s Newsletter article](https://www.lennysnewsletter.com/p/how-to-turn-your-ai-into-a-world) by developing the selected direction further rather than adding decorative effects. Its visual identity is an incident editor: connected panes, document-like record fields, explicit proposed changes, and inspectable provenance. Published-record status compares accepted values, operator notes, and unresolved items against the shared version; pending suggestions remain visible in their own queue. No claim of a screenshot-based critique is made while browser testing is blocked by disk space.

## Microinteraction pass

| Before | After | Why |
| --- | --- | --- |
| Abrupt pointer press | 120 ms, 0.98-scale feedback | Acknowledge the press without delaying the action |
| Accepted text changes silently | 180 ms field/check acknowledgement | Direct attention to the changed record |
| Source toggle lacks a directional cue | Rotating chevron and 160 ms excerpt reveal | Connect the control to its source |
| View changes abruptly | 140 ms opacity transition and active underline | Maintain orientation without moving the workspace |
| Publish/copy only reports in the status line | Inline Published version / Copied labels | Confirm success beside the action |
| Reviewed row removes the focused control | Keyboard focus advances to the next proposal or empty state | Preserve review continuity |

Motion uses transforms and opacity, never delays state updates, cancels when interrupted, and is suppressed for keyboard input and reduced-motion preferences. No animation library was added. Visual motion QA remains pending under the disk-space safeguard.

## Live-workspace usability pass

| Before | After | Why |
| --- | --- | --- |
| New transcript could pull the reader away | Follow pauses when scrolling back; Jump to latest includes arrival count | Keep earlier information readable during incoming updates |
| Source navigation could scroll the whole page | Source selection scrolls only the conversation pane | Preserve the operator's workspace position |
| Switching views lost transcript position | Reading position is restored on return | Avoid repeatedly finding the same information |
| Superseded proposal could remove an active editor | Preserve draft, explain the change, offer explicit manual save | Protect operator work while leaving newer proposals pending |
| Cancel/save could lose editing focus | Escape cancels; manual save/cancel restores the edit control | Keep keyboard navigation predictable |
| Current record appeared crossed out before review | Show current value without strikethrough | Accepted information remains valid until the operator changes it |

Source inspection can be exited with Jump to latest. Transcript and record scroll areas contain their own scroll gestures. Supporting review labels and edit targets have been enlarged. Browser interaction QA remains outstanding due to the disk-space safeguard.
