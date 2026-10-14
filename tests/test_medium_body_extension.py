"""Canonical body packaging and local Chrome body-fill safeguards."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from publish_to_all.story import load_story


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome-extension"
EXTENSION_DEV = ROOT / "extension-dev"


def exporter():
    spec = importlib.util.spec_from_file_location(
        "export_story_body", EXTENSION_DEV / "export-story-body.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_packaged_body_matches_canonical_story():
    module = exporter()
    story = load_story(ROOT / "In")
    packaged = (EXTENSION / "story-body.js").read_text(encoding="utf-8")
    data = module.analyze(story)
    assert packaged == module.render(ROOT)
    assert data["source"] == "In/01_01_2040.md"
    assert data["sourceHash"] == story.source_hash
    assert data["characterCount"] == len(story.markdown)
    assert data["normalizedCharacterCount"] == 58757
    assert data["fingerprint"] == "fnv1a32:342d11eb"
    assert data["paragraphCount"] == 18
    assert data["headingCount"] == 18
    assert data["unsupported"] == []
    assert story.metadata.title not in "\n".join(block["text"] for block in data["blocks"])
    assert "title:" not in data["blocks"][0]["text"]
    assert data["blocks"][0] == {"kind": "heading", "level": 1, "text": "01"}


def test_unsupported_markdown_detected_before_packaging(tmp_path):
    source = tmp_path / "In"
    source.mkdir()
    (source / "story.md").write_text(
        "---\ntitle: Story\n---\n# Heading\n\nA [link](https://example.com) and **bold**.\n",
        encoding="utf-8")
    data = exporter().analyze(load_story(source))
    assert "link_open" in data["unsupported"]
    assert "strong_open" in data["unsupported"]
    assert data["features"]["link"] == 1
    assert data["features"]["emphasis"] == 1


def test_local_chrome_body_fill_fixture():
    completed = subprocess.run(
        [sys.executable, "-B", str(EXTENSION_DEV / "fixtures/chrome_medium_body_fixture.py")],
        cwd=ROOT, capture_output=True, text=True, check=True, timeout=60)
    report = json.loads(completed.stdout)
    normal = report["normal"]
    assert normal["result"]["workflow"] == "MEDIUM_BODY_VERIFIED"
    assert normal["result"]["bodyDefaultPromptRemoved"]
    assert normal["result"]["bodyFingerprintMatch"]
    assert normal["partial"]["currentBodyExactCanonicalMatch"]
    assert normal["result"]["titleUnchangedAfterBodyFill"]
    assert normal["again"]["reason"] == "BODY_ALREADY_FILLED_BY_WORKFLOW"
    assert normal["publish"] == normal["settings"] == normal["canonical"] == 0
    assert report["rebuild"]["result"]["workflow"] == "MEDIUM_BODY_VERIFIED"
    assert report["promptRemoved"]["partial"]["currentBodyNormalizedLength"] == 0
    assert report["promptRemoved"]["again"]["reason"] == \
        "BODY_ALREADY_FILLED_BY_WORKFLOW"
    first = report["firstInsertion"]
    assert first["partial"]["currentBodyCanonicalPrefixMatch"]
    assert first["partial"]["canonicalPrefixMatchedThroughBlock"] == 1
    assert first["partial"]["currentBodyFingerprint"] == "fnv1a32:20ed70ba"
    assert report["fontCanonical"]["result"]["workflow"] == "MEDIUM_BODY_VERIFIED"
    reflow = report["mediumReflow"]
    assert reflow["result"]["bodyFillFailure"] == "BLOCK_POSTCHECK_FAILED"
    assert reflow["partial"]["currentBodyBlockCount"] == 2
    assert reflow["inspectedAfter"]["bodySubregionCandidateCount"] == 2
    assert not reflow["partial"]["partialBodyDetected"]
    assert reflow["partial"]["partialBodyRecoveryClassification"] == "UNKNOWN"
    assert not reflow["partial"]["currentBodyCanonicalPrefixMatch"]
    transient = report["transientReflow"]
    assert transient["result"]["bodyFillFailure"] == "BLOCK_POSTCHECK_FAILED"
    assert transient["partial"]["currentBodyBlockCount"] == 1
    assert transient["partial"]["currentBodyCanonicalPrefixMatch"]
    assert transient["partial"]["currentBodyDOMStructure"][0]["children"] == ["font"]
    artifact = report["twoCharacterArtifact"]
    assert artifact["partial"]["currentBodyNormalizedLength"] == 2
    assert artifact["partial"]["currentBodySafeText"] == "##"
    assert artifact["partial"]["currentBodyMatchesMarkdownHeadingMarkup"]
    assert artifact["partial"]["partialBodyRecoveryClassification"] == \
        "NONCANONICAL_PARTIAL_CONTENT"
    for case in (first, transient, artifact):
        assert case["partial"]["partialBodyDetected"]
        assert case["again"]["reason"] == "BODY_ALREADY_FILLED_BY_WORKFLOW"
        assert case["inspectedAfter"]["titleVerifiedInPersistedDraft"]
        assert case["publish"] == case["settings"] == 0
    assert reflow["again"]["reason"] == "BODY_ALREADY_FILLED_BY_WORKFLOW"
    assert reflow["publish"] == reflow["settings"] == 0


def test_local_chrome_persisted_partial_body_fixture():
    completed = subprocess.run(
        [sys.executable, "-B", str(EXTENSION_DEV / "fixtures/chrome_medium_partial_fixture.py")],
        cwd=ROOT, capture_output=True, text=True, check=True, timeout=60)
    report = json.loads(completed.stdout)
    for shape in ("direct", "canonical", "span", "whitespace",
                  "heading", "separateSections", "wrappedTitle"):
        body = report[shape]["reconciliation"]
        assert body["partialBodyDetected"]
        assert body["currentBodySafeText"] == "01"
        assert body["currentBodyNormalizedLength"] == 2
        assert body["currentBodyFingerprint"] == "fnv1a32:20ed70ba"
        assert body["currentBodyCanonicalTextPrefix"]
        assert body["partialBodyRecoveryClassification"] == "EXACT_CANONICAL_PREFIX"
        assert not report[shape]["eligibility"]["ready"]
        assert report[shape]["publishClicks"] == 0
        assert report[shape]["settingsClicks"] == 0
    assert report["direct"]["reconciliation"]["formattingDiscrepancy"] == \
        "Expected heading block; observed paragraph"
    assert not report["direct"]["reconciliation"][
        "currentBodyCanonicalFormattingComplete"]
    assert report["heading"]["reconciliation"][
        "currentBodyCanonicalFormattingComplete"]
    assert report["noncanonicalDirect"]["reconciliation"][
        "partialBodyRecoveryClassification"] == "NONCANONICAL_PARTIAL_CONTENT"
    assert report["reload"]["reconciliation"] == report["canonical"]["reconciliation"]
    assert report["full"]["reconciliation"]["currentBodyExactCanonicalMatch"]


def test_local_chrome_exact_checkpoint_recovery_fixture():
    completed = subprocess.run(
        [sys.executable, "-B", str(EXTENSION_DEV / "fixtures/chrome_medium_recovery_fixture.py")],
        cwd=ROOT, capture_output=True, text=True, check=True, timeout=90)
    report = json.loads(completed.stdout)
    direct = report["direct"]
    assert direct["before"]["workflow"] == "MEDIUM_BODY_RECOVERY_READY"
    assert direct["before"]["ready"]
    assert direct["before"]["reason"] == "None"
    assert direct["before"]["failedGuard"] is None
    assert all(direct["before"]["guards"].values())
    evidence = direct["before"]["diagnostics"]
    assert evidence["expectedTitleAvailable"]
    assert evidence["expectedTitle"] == evidence["liveTitle"] == \
        "Saturation of Artificial Intelligence"
    assert evidence["expectedTitleFingerprint"] == evidence["liveTitleFingerprint"] == \
        "fnv1a32:5e81665d"
    assert evidence["canonicalBodySourceAvailable"]
    assert evidence["canonicalBodyBlocksAvailable"]
    assert evidence["expectedFullBodyFingerprint"] == "fnv1a32:342d11eb"
    assert evidence["expectedDraftId"] == evidence["currentDraftId"] == \
        "fd6c1824c4de"
    assert evidence["partialReconciliationResult"] == "EXACT_CANONICAL_PREFIX"
    assert evidence["unexpectedContentCandidateCount"] == 0
    assert evidence["unexpectedContentCandidates"] == []
    assert not evidence["unexpectedContentClassificationBug"]
    assert all(evidence["editorIdentityChecks"].values())
    assert direct["before"]["recoveryStartBlock"] == 1
    assert direct["before"]["recoveryNextCanonicalBlock"] == 2
    assert direct["before"]["blockOneRequiresFormattingRepair"]
    assert not direct["normal"]["ready"]
    assert direct["noMutationOnInspection"]
    assert direct["control"] == {"present": True, "interactability": "ENABLED",
                                 "disabledProperty": False, "listenerAttached": True}
    finished = direct["result"]
    assert finished["workflow"] == "MEDIUM_BODY_RECOVERY_VERIFIED"
    assert finished["bodyWorkflow"] == "MEDIUM_BODY_VERIFIED"
    assert finished["bodyRecoveryFailure"] == "None"
    assert finished["lastVerifiedCanonicalBlock"] == 36
    assert finished["nextExpectedCanonicalBlock"] is None
    assert finished["blockOneText"] == "01"
    assert finished["blockOneFingerprint"] == "fnv1a32:20ed70ba"
    assert finished["blockOneCanonicalText"] and finished["blockOneCanonicalFormatting"]
    assert finished["bodyExactNormalizedMatch"] and finished["bodyFingerprintMatch"]
    assert finished["bodyFingerprint"] == "fnv1a32:342d11eb"
    assert finished["bodyParagraphCount"] == finished["bodyHeadingCount"] == 18
    assert finished["bodyParagraphCountMatch"] and finished["bodyHeadingCountMatch"]
    assert finished["titleUnchanged"] and finished["titleExactMatch"]
    assert finished["titleFingerprintMatch"] and finished["draftIdentityUnchanged"]
    assert not finished["publishActionOccurred"]
    assert len(finished["completedBlocks"]) == 36
    assert direct["saveState"]["domBodyVerified"]
    assert not direct["saveState"]["persistenceVerified"]
    assert direct["saveState"]["mediumSaveState"] == "MEDIUM_SAVE_INDICATED"
    assert direct["afterWarning"]["mediumSaveState"] == "MEDIUM_SAVE_ERROR_VISIBLE"
    assert direct["afterWarning"]["domBodyVerified"]
    assert not direct["afterWarning"]["persistenceVerified"]
    assert direct["afterWarning"]["safeToLeaveReloadPage"] == "No"
    assert direct["again"]["normalFill"] == {"ready": False,
        "reason": "BODY_ALREADY_VERIFIED"}
    assert finished["completedBlocks"][0] == {"number": 1, "kind": "heading",
        "normalizedLength": 2, "fingerprint": "fnv1a32:20ed70ba"}
    assert finished["completedBlocks"][1] == {"number": 2, "kind": "paragraph",
        "normalizedLength": 905, "fingerprint": "fnv1a32:ad559899"}
    assert direct["textCount"] == 35  # Existing 01 was never retyped.


    assert direct["formatCount"] == 36
    assert direct["finalBlocks"] == 36
    assert direct["again"]["reason"] == "BODY_ALREADY_VERIFIED"
    assert not direct["again"]["ready"]
    assert direct["again"]["secondInvocation"] == {
        "attempted": False, "reason": "RECOVERY_ALREADY_ATTEMPTED"}

    for mode in ("font", "rebuild", "transient", "transientInsert"):
        case = report[mode]
        result = case["result"]
        assert case["before"]["ready"] and case["noMutationOnInspection"], mode
        assert result["lastVerifiedCanonicalBlock"] == 2, mode
        assert result["nextExpectedCanonicalBlock"] == 3, mode
        assert result["completedBlocks"][1]["fingerprint"] == \
            "fnv1a32:ad559899", mode
        assert result["stableDOMObserved"] and not result["mutationRepeated"], mode
        assert case["textCount"] == 1 and case["paragraphCount"] == 1, mode
        assert case["firstBlock"].startswith("<h1"), mode
    assert report["rebuild"]["reflowCount"] == 1
    assert report["transientInsert"]["reflowCount"] == 1
    assert report["transientInsert"]["result"]["postEditStabilizationAttempts"] > \
        report["font"]["result"]["postEditStabilizationAttempts"]
    timeout = report["timeout"]
    assert timeout["result"]["workflow"] == "MEDIUM_BODY_RECOVERY_AMBIGUOUS"
    assert timeout["result"]["bodyRecoveryFailure"] == \
        "POST_EDIT_STABILIZATION_TIMEOUT"
    assert timeout["result"]["lastVerifiedCanonicalBlock"] == 0
    assert timeout["formatCount"] == 1 and timeout["textCount"] == 0
    failed = report["failN"]
    assert failed["result"]["lastVerifiedCanonicalBlock"] == 2
    assert failed["result"]["nextExpectedCanonicalBlock"] == 3
    assert failed["textCount"] == 2  # Block 4 was never attempted.
    assert failed["result"]["workflow"] == "MEDIUM_BODY_RECOVERY_AMBIGUOUS"
    rejected = {
        "wrongText": "partialState",
        "extraBlock": "bodySubregion",
        "missingBody": "bodySubregion",
        "duplicateBody": "bodySubregion",
        "extraAuthored": "bodySubregion",
        "wrongBlockCount": "blockCount",
        "wrongDraft": "route",
        "wrongTitle": "titleText",
        "wrongExpectedTitle": "titleText",
        "wrongFingerprint": "titleFingerprint",
        "missingSource": "sourceLoaded",
        "wrongFullBodyFingerprint": "expectedBodyFingerprint",
        "wrongPartialFingerprint": "partialFingerprint",
        "heading": "formattingRepair",
        "publishObserved": "publishState",
    }
    for mode, failed_guard in rejected.items():
        case = report[mode]
        assert not case["before"]["ready"], mode
        assert case["before"]["failedGuard"] == failed_guard, mode
        assert not case["before"]["guards"][failed_guard], mode
        assert case["before"]["workflow"] == "MEDIUM_BODY_RECOVERY_NOT_READY", mode
        assert case["noMutationOnInspection"], mode
        assert not case["normal"]["ready"], mode
        if mode == "wrongDraft":
            assert not case["control"]["present"]
        else:
            assert case["control"]["disabledProperty"], mode
            assert case["control"]["interactability"] == "BLOCKED", mode
        assert case["formatCount"] == case["textCount"] == 0, mode
    assert report["wrongFingerprint"]["before"]["guards"]["titleText"]
    assert report["wrongFullBodyFingerprint"]["before"]["guards"]["sourceLoaded"]
    assert report["wrongPartialFingerprint"]["before"]["guards"]["partialState"]
    assert report["heading"]["before"]["guards"]["partialState"]
    assert report["heading"]["before"]["guards"]["partialFingerprint"]
    assert report["heading"]["before"]["guards"]["blockCount"]
    assert report["heading"]["before"]["reason"] == "BLOCK_ONE_ALREADY_FORMATTED"
    wrapped = report["wrapped"]
    assert wrapped["before"]["ready"]
    assert wrapped["before"]["workflow"] == "MEDIUM_BODY_RECOVERY_READY"
    assert wrapped["before"]["recoveryStartBlock"] == 1
    assert wrapped["before"]["recoveryNextCanonicalBlock"] == 2
    assert wrapped["before"]["blockOneRequiresFormattingRepair"]
    assert all(value is True for value in wrapped["before"]["guards"].values())
    assert wrapped["postInspection"]["bodyCount"] == 1
    assert wrapped["before"]["diagnostics"]["recoveryBodyCandidateCount"] == 1
    assert wrapped["before"]["diagnostics"]["recoveryBodySharesInspectorNode"]
    assert wrapped["before"]["diagnostics"]["unexpectedContentCandidateCount"] == 0
    assert wrapped["before"]["guards"]["unexpectedContent"]
    assert wrapped["before"]["diagnostics"]["editorIdentityChecks"][
        "noUnexpectedEditorContent"]
    assert wrapped["control"]["interactability"] == "ENABLED"
    assert wrapped["noMutationOnInspection"] and not wrapped["result"]
    assert wrapped["formatCount"] == wrapped["paragraphCount"] == \
        wrapped["textCount"] == 0
    assert not wrapped["normal"]["ready"]
    for mode, count in (("missingBody", 0), ("duplicateBody", 2),
                        ("extraAuthored", 2)):
        case = report[mode]
        assert case["postInspection"]["bodyCount"] == count
        assert case["before"]["diagnostics"]["recoveryBodyCandidateCount"] == count
        for guard in ("partialState", "partialFingerprint", "blockCount",
                      "formattingRepair"):
            assert case["before"]["guards"][guard] == "NOT_EVALUATED"
    assert not report["extraAuthored"]["before"]["guards"]["unexpectedContent"]
    assert not report["duplicateBody"]["before"]["guards"]["unexpectedContent"]
    for mode in ("extraAuthored", "duplicateBody", "extraHeading",
                 "hiddenHeading", "strayText", "strayBeforeTitle",
                 "bodyExtraText", "hiddenBodyText"):
        case = report[mode]
        diagnostic = case["before"]["diagnostics"]
        assert not case["before"]["ready"], mode
        assert not case["before"]["guards"]["unexpectedContent"], mode
        assert diagnostic["unexpectedContentCandidateCount"] >= 1, mode
        assert not diagnostic["unexpectedContentClassificationBug"], mode
        assert case["noMutationOnInspection"] and not case["result"], mode
        assert not case["normal"]["ready"], mode
        assert case["control"]["interactability"] == "BLOCKED", mode
        for candidate in diagnostic["unexpectedContentCandidates"]:
            assert candidate["path"] and candidate["reason"]
            assert len(candidate["safeShortText"]) <= 48
            assert candidate["normalizedTextFingerprint"].startswith("fnv1a32:")
    assert report["strayBeforeTitle"]["before"]["failedGuard"] == \
        "unexpectedContent"
    assert report["bodyExtraText"]["before"]["diagnostics"][
        "unexpectedContentCandidates"][0]["reason"] == \
        "SELECTED_BODY_TEXT_NOT_CANONICAL_01"
    bug = report["classificationBug"]["before"]
    assert bug["failedGuard"] == "unexpectedContent"
    assert bug["diagnostics"]["unexpectedContentCandidateCount"] == 0
    assert bug["diagnostics"]["unexpectedContentClassificationBug"]
    changed = report["changedBeforeClick"]
    assert changed["before"]["ready"]
    assert changed["result"]["bodyRecoveryFailure"] == \
        "BODY_NOT_EXACT_CANONICAL_PREFIX"
    assert not changed["result"]["bodyRecoveryAttempted"]
    assert changed["formatCount"] == changed["textCount"] == 0
    for case in report.values():
        assert case["publish"] == case["settings"] == 0

def test_local_chrome_save_warning_and_event_semantics():
    completed = subprocess.run(
        [sys.executable, "-B", str(EXTENSION_DEV / "fixtures/chrome_medium_save_fixture.py")],
        cwd=ROOT, capture_output=True, text=True, check=True, timeout=60)
    report = json.loads(completed.stdout)
    warning = report["withWarning"]
    assert warning["mediumSaveState"] == "MEDIUM_SAVE_ERROR_VISIBLE"
    assert warning["mediumSaveErrorVisible"]
    assert warning["mediumSaveWarningCandidateCount"] == 1
    assert warning["mediumSaveWarningCandidates"][0]["role"] == "alert"
    assert warning["mediumSaveWarningCandidates"][0]["safeText"].startswith(
        "Something is wrong and we cannot save your story.")
    for state in ("withoutWarning", "withWarning", "warningGone", "indicated"):
        assert report[state]["domBodyVerified"]
        assert not report[state]["persistenceVerified"]
        assert report[state]["safeToLeaveReloadPage"] == "No"
    assert report["warningGone"]["mediumSaveState"] == "MEDIUM_SAVE_UNKNOWN"
    assert report["pending"]["mediumSaveState"] == "MEDIUM_SAVE_PENDING"
    assert report["indicated"]["mediumSaveState"] == "MEDIUM_SAVE_INDICATED"
    assert report["bounded"]["mediumSaveWarningCandidateCount"] == 12
    assert len(report["bounded"]["mediumSaveWarningCandidates"]) == 5
    assert len(report["telemetryEvents"]) <= 80
    assert any(item["type"] == "beforeinput" for item in report["earlyTelemetry"])
    assert any(item["type"] == "editor-dom-mutation"
               for item in report["earlyTelemetry"])
    assert any(item["type"] == "save-warning-appeared"
               for item in report["warningTelemetry"])
    assert any(item["type"] == "save-warning-disappeared"
               for item in report["goneTelemetry"])
    assert any(item["type"] == "input" for item in report["telemetryEvents"])
    assert report["eventAllowed"] and not report["defaultPrevented"]
    assert report["noEditorMutationOnInspection"]
    operations = {item["operation"]: item for item in report["operations"]}
    for name in ("title insertText", "recovery formatBlock h1",
                 "recovery insertParagraph", "recovery insertText"):
        assert operations[name]["commandSucceeded"]
        assert operations[name]["inputObserved"]
        assert operations[name]["domChanged"]
        assert all(operations[name]["isTrusted"])
    assert not any(item["beforeinputObserved"] or item["changeObserved"]
                   for item in operations.values())


def test_save_inspection_has_no_retry_or_editor_mutation_path():
    source = (EXTENSION / "medium-save-state.js").read_text(encoding="utf-8")
    for forbidden in ("execCommand", "dispatchEvent", "preventDefault",
                      "stopPropagation", "click(", "fetch(", "location.assign",
                      "location.replace", "innerHTML =", "textContent ="):
        assert forbidden not in source
    assert "MutationObserver" in source
    assert "records.length > limit" in source
