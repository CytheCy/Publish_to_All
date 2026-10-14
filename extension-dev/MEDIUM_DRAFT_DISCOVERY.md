# Isolated Medium draft read-contract discovery

This tool is for a disposable Medium account and two disposable drafts only. The
production draft and its current Chrome tab must remain untouched. It does not
know a draft-read endpoint, perform replay, or implement a persistence verifier.
The earlier audit remains: safe read-only endpoint **No**; persistence verifier
**No**; live production use **No**.

## Isolation and collection boundary

`medium_draft_discovery.py record` launches a separate persistent Chrome profile
under `extension-dev/.medium-draft-discovery/profiles/<name>/chrome-data`. It
refuses an unmarked existing profile or symlink, starts on a blank page, and
disables extensions. It never connects to the production Chrome session. It
does not read cookie, localStorage, sessionStorage, IndexedDB, or extension
storage. Chrome itself retains only the throwaway account's state in this
separate profile. A narrow request guard aborts any URL containing the forbidden
production draft ID before transmission. The recorder aborts and discards its
current output if that ID appears in an observed URL, request body, or inspected
response. Do not copy production profile data into the discovery directory.

The user drives Chrome. The recorder never navigates, types, clicks, edits,
creates, saves, publishes, replays, or resends a request. It stores one JSONL
record per observed response. Query **names** are stored, query values are not.
Request bodies are reduced to a length, truncated SHA-256 fingerprint, GraphQL
operation type/name, and a Boolean for test story markers. Response bodies are
used in memory for marker matching and structural paths, and are never written.
Inspection is limited to Medium JSON/HTML responses at most 2 MB when a content
length is supplied; larger inspected bodies are discarded. Only response status,
type, length, redirect host/path, resource type, and bounded schema evidence are
written. No cookies, tokens, full headers, or raw story content are written.
Keep markers distinctive and avoid secrets in their text.

## Manual throwaway procedure (not performed during development)

Use a new profile name and a completely separate throwaway Medium account. Run
all commands from the repository root. Every `record` command opens the same
isolated Chrome profile and waits until you press Enter in its terminal. Use
Chrome's address bar and Medium's normal UI yourself; no URL is sent by the
tool. Never use the production account, profile, draft, or current tab.

1. Run `.venv/bin/python -B extension-dev/medium_draft_discovery.py record --profile test-2026 --phase write --title-marker 'Disposable alpha title 87361' --body-marker 'Disposable alpha body 28471'`. In its blank Chrome window, sign into the throwaway account. Create draft A with those exact markers, let Medium save, then close or navigate away from the draft. Press Enter. This phase supplies autosave/write evidence. Record the throwaway draft ID from Medium's normal UI.
2. Run `record` again with `--profile test-2026 --phase a1 --draft-id <throwaway-A-ID> --title-marker 'Disposable alpha title 87361' --body-marker 'Disposable alpha body 28471'` and the same Python command prefix. In the isolated Chrome window, reopen that already saved draft normally. Wait for it to load, leave it unchanged, then press Enter. This is the first read observation.
3. Close or navigate away from draft A normally. Repeat step 2 with `--phase a2`. This is the second read observation.
4. Run `--phase write` again and manually create draft B with different unmistakable title and body markers. Let it save and navigate away. Record draft B's throwaway ID.
5. Run `--phase b --draft-id <throwaway-B-ID>` with B's title and body markers. Reopen draft B normally and press Enter.
6. Run `.venv/bin/python -B extension-dev/medium_draft_discovery.py report extension-dev/.medium-draft-discovery/profiles/test-2026/*.jsonl`. Review the report and the bounded JSONL rows. The tool does not contact Medium during reporting.

Each phase opens a blank page first. Do not refresh or navigate the production
tab. If Chrome closes unexpectedly, rerun that phase with the same test profile
and keep only a complete observation file. Do not infer a read from a path name;
look for the test ID, title, and body marker in the **response**, and confirm a
query or payload-free GET rather than a mutation. A POST GraphQL query may be a
semantic read; a POST mutation or request with modified story data is a write.

## Review gate after collection

For each candidate, review HTTP method, host, path or GraphQL operation, response
format, top-level keys, ID/title/body field paths, body and formatting
representation, and marker matches. Compare A's two reopen events and B's event.
The report normalizes a draft ID in URL paths to `{draft_id}` for comparison.
Check whether any other differences remain, including query parameter **values**
that the recorder deliberately does not store; inspect these only in the test
browser if needed, without copying secrets into an artifact. An HTML document
match without field paths is insufficient to establish a structured read
contract. Count only genuine reopen observations for endpoint provenance.

Record the write path, method, trigger, response status, and whether its request
contained modified story data from the `write` phase. These requests must never
be replayed. Then answer conceptually, without a replay: whether the read would
instantiate an editor, could modify a draft, contains mutation data, returns the
stored body alone, induces autosave, or requires a write-capable GraphQL
operation. Use **Unknown** where observation cannot decide. The offline report
can reach `READ_CONTRACT_FOUND_REPLAY_SAFETY_UNKNOWN` after matching three
structured responses; it deliberately cannot declare replay safe. A human
review may later choose among `READ_CONTRACT_NOT_FOUND`,
`READ_CONTRACT_FOUND_REPLAY_UNSAFE`,
`READ_CONTRACT_FOUND_REPLAY_SAFETY_UNKNOWN`, and
`READ_CONTRACT_FOUND_CANDIDATE_SAFE`. None authorizes production use by itself.
