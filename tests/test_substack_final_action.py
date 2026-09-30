from copy import deepcopy
from unittest.mock import MagicMock

from publish_to_all.browser import publish_inspect
from publish_to_all.presentation import format_final_action_diagnostic


def attribute(value=None, *, present=False):
    return {'present': present, 'value': '' if value is None else value}


def raw_candidate(**changes):
    candidate = {
        'tagName': 'button', 'role': 'button',
        'accessibleName': 'Send to everyone now',
        'visibleText': 'Send to everyone now', 'visible': True, 'enabled': True,
        'effectiveType': 'button', 'type': attribute('button', present=True),
        'id': attribute(), 'className': attribute('button primary', present=True),
        'dataTestid': attribute('publish-button', present=True),
        'name': attribute(), 'value': attribute(), 'href': attribute(),
        'target': attribute(), 'rel': attribute(), 'formAttribute': attribute(),
        'formOwner': None, 'formOwnerAction': attribute(), 'formOwnerMethod': attribute(),
        'formOwnerEffectiveMethod': None,
        'nearestForm': None, 'nearestFormAction': attribute(),
        'nearestFormMethod': attribute(), 'nearestFormEffectiveMethod': None,
        'formaction': attribute(), 'formmethod': attribute(),
        'ariaLabelledby': attribute(), 'ariaDescribedby': attribute(),
        'tabindex': attribute(),
        'nearestDialog': 'div data-testid="publish-modal" role="dialog"',
        'nearestStableTestidAncestor': 'div data-testid="publish-modal" role="dialog"',
        'publishModalAncestry': 'indirect', 'publishModalAncestorCount': 1,
        'belongsToExactlyOnePublishModal': True,
    }
    candidate.update(changes)
    return candidate


def collect(candidates, modal_count=1):
    page = MagicMock()
    page.evaluate.return_value = {
        'candidates': candidates, 'publishModalMatches': modal_count,
    }
    evidence = publish_inspect.inspect_final_action_evidence(page)
    return evidence, page


def test_one_valid_visible_button_preserves_complete_semantics_without_interaction():
    candidate = raw_candidate(
        id=attribute('', present=True), target=attribute('', present=True),
        href=attribute('/publish', present=True),
        formAttribute=attribute('publish-form', present=True),
        formOwner='form id="publish-form"', nearestForm='form id="publish-form"',
        formOwnerAction=attribute('/owner-send', present=True),
        formOwnerMethod=attribute('post', present=True), formOwnerEffectiveMethod='post',
        nearestFormAction=attribute('/send', present=True),
        nearestFormMethod=attribute('post', present=True),
        nearestFormEffectiveMethod='post',
        formaction=attribute('/send-now', present=True),
        formmethod=attribute('post', present=True),
        ariaLabelledby=attribute('send-label', present=True),
        ariaDescribedby=attribute('send-help', present=True),
        tabindex=attribute('0', present=True),
    )
    evidence, page = collect([candidate])
    item = evidence.candidates[0]
    assert (evidence.total_matches, evidence.visible_matches, evidence.hidden_matches) == (1, 1, 0)
    assert (evidence.enabled_visible_matches, evidence.disabled_visible_matches) == (1, 0)
    assert item.tag_name == 'button' and item.role == 'button'
    assert item.element_type == publish_inspect.AttributeEvidence(True, 'button')
    assert item.effective_type == 'button'
    assert item.id == publish_inspect.AttributeEvidence(True, '')
    assert item.href == publish_inspect.AttributeEvidence(True, '/publish')
    assert item.target == publish_inspect.AttributeEvidence(True, '')
    assert item.form_attribute == publish_inspect.AttributeEvidence(True, 'publish-form')
    assert item.form_owner == 'form id="publish-form"'
    assert item.form_owner_action.value == '/owner-send'
    assert item.form_owner_method.value == 'post'
    assert item.nearest_form == 'form id="publish-form"'
    assert item.nearest_form_action.value == '/send'
    assert item.nearest_form_method.value == 'post'
    assert item.formaction.value == '/send-now' and item.formmethod.value == 'post'
    assert item.belongs_to_exactly_one_publish_modal
    assert item.publish_modal_ancestry == 'indirect'
    page.click.assert_not_called()
    page.keyboard.press.assert_not_called()


def test_two_identical_visible_candidates_are_never_deduplicated():
    original = raw_candidate()
    evidence, _page = collect([original, deepcopy(original)])
    assert evidence.total_matches == 2
    assert evidence.visible_matches == 2
    assert len(evidence.candidates) == 2
    assert evidence.candidates[0] == evidence.candidates[1]
    assert evidence.ambiguity


def test_visible_and_hidden_duplicate_are_counted_separately():
    evidence, _page = collect([raw_candidate(), raw_candidate(visible=False)])
    assert (evidence.total_matches, evidence.visible_matches, evidence.hidden_matches) == (2, 1, 1)
    assert [item.visible for item in evidence.candidates] == [True, False]
    assert evidence.enabled_visible_matches == 1


def test_two_hidden_candidates_remain_distinguishable():
    evidence, _page = collect([
        raw_candidate(visible=False, id=attribute('one', present=True)),
        raw_candidate(visible=False, id=attribute('two', present=True)),
    ])
    assert evidence.total_matches == 2 and evidence.hidden_matches == 2
    assert [item.id.value for item in evidence.candidates] == ['one', 'two']


def test_absence_form_presence_modal_membership_and_disabled_counts_are_preserved():
    evidence, _page = collect([
        raw_candidate(),
        raw_candidate(
            enabled=False, href=attribute('', present=True),
            nearestForm='form id="f"', nearestFormAction=attribute('', present=True),
            nearestFormMethod=attribute('', present=True), nearestFormEffectiveMethod='get',
            formaction=attribute('', present=True), nearestDialog='div role="dialog"',
            nearestStableTestidAncestor=None, publishModalAncestry='absent',
            publishModalAncestorCount=0, belongsToExactlyOnePublishModal=False,
        ),
    ], modal_count=2)
    first, second = evidence.candidates
    assert first.href == publish_inspect.AttributeEvidence(False, '')
    assert first.form_owner is None and first.nearest_form is None
    assert first.formaction == publish_inspect.AttributeEvidence(False, '')
    assert second.href == publish_inspect.AttributeEvidence(True, '')
    assert second.nearest_form_action == publish_inspect.AttributeEvidence(True, '')
    assert second.formaction == publish_inspect.AttributeEvidence(True, '')
    assert not second.belongs_to_exactly_one_publish_modal
    assert evidence.disabled_visible_matches == 1
    assert evidence.enabled_visible_matches == 1
    assert evidence.multiple_publish_modals


def test_diagnostic_dom_script_is_read_only_and_final_action_is_never_clicked():
    evidence, page = collect([raw_candidate()])
    script = page.evaluate.call_args.args[0]
    forbidden = ('.click(', '.focus(', 'dispatchEvent(', 'submit(', 'requestSubmit(',
                 'innerHTML =', 'outerHTML =', 'textContent =', 'setAttribute(',
                 'removeAttribute(')
    assert not any(fragment in script for fragment in forbidden)
    assert evidence.total_matches == 1
    page.click.assert_not_called()
    page.get_by_role.assert_not_called()
    page.locator.assert_not_called()


def test_presentation_lists_each_candidate_and_distinguishes_absent_from_empty():
    evidence, _page = collect([
        raw_candidate(), raw_candidate(href=attribute('', present=True), visible=False),
    ])
    screen = publish_inspect.FinalScreenInspection(
        'https://example.com', 'Publish', (), (), None,
        'Browser closed on final publication screen; no exit control was used.', (),
        final_action_evidence=evidence,
    )
    result = MagicMock(
        final_screen=screen, database_sha256_before='abc', database_sha256_after='abc',
        local_state_changed=False,
    )
    report = format_final_action_diagnostic(result)
    assert 'Candidate 1:' in report and 'Candidate 2:' in report
    assert 'total: 2' in report and 'hidden: 1' in report
    assert 'href: absent' in report
    assert 'href: ""' in report
    assert 'Final action clicked: No' in report
