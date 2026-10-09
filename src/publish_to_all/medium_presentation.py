"""Safe Medium Stage 1 CLI reports; no raw browser objects or exception text."""

from .browser.medium import MediumResult


def format_medium(result: MediumResult, *, inspect_import: bool = False) -> str:
    lines = ["Medium Stage 1", f"Medium browser profile: {result.profile}",
             f"medium-session result: {result.authentication.value}",
             "Authentication evidence: " + (", ".join(result.evidence) or "No decisive rendered controls"),
             f"Current URL (redacted): {result.current_url}"]
    probe = result.account_probe
    if probe is not None:
        chain = " → ".join(f"{hop['method']} {hop['url']} (HTTP {hop['status']})"
                           for hop in probe.redirect_chain)
        lines += ["Medium authentication strategy: Homepage-only → /me read-only account probe",
                  f"Requested URL: {probe.requested_url}",
                  f"Final URL: {probe.final_url}",
                  "Redirect chain (safe origins/paths): " + (chain or "Not observed"),
                  "Committed navigation (safe origins/paths): " + (" → ".join(probe.navigation) or "Not observed"),
                  f"Page title: {probe.page_title or '[unavailable]'}",
                  "Rendered owner/account controls: " + (", ".join(probe.owner_controls) or "None observed"),
                  "Rendered signed-out controls: " + (", ".join(probe.signed_out_controls) or "None observed"),
                  "Import navigation: No"]
    if result.stopped_reason:
        lines.append(f"Stopped: {result.stopped_reason}")
    if inspect_import:
        lines += [f"Medium Import workflow found: {'Yes' if result.interface else 'No'}",
                  "Observed navigation path: " + (" → ".join(result.navigation) or "Not verified")]
        fields = result.interface or {}
        for label, key in (
            ("Heading", "heading"), ("URL input", "url_input"),
            ("Input accessible name", "input_accessible_name"),
            ("Input placeholder", "input_placeholder"), ("Input type", "input_type"),
            ("Input data-testid", "input_testid"), ("Import action label", "import_action_label"),
            ("Import action data-testid", "import_action_testid"),
            ("Import action enabled while empty", "import_action_enabled_while_empty"),
            ("Safe cancel/back action", "safe_cancel_back"),
            ("Explanatory text", "explanatory_text"), ("Canonical-link text", "canonical_text"),
        ):
            value = fields.get(key, "Not verified")
            if value is None:
                value = "Not present"
            if isinstance(value, list):
                value = " | ".join(value) or "Not observed"
            if value == "":
                value = "Empty"
            lines.append(f"{label}: {value}")
        lines += [f"LAST VERIFIED READ-ONLY STEP: {result.last_read_only}",
                  f"FIRST WRITE/IMPORT ACTION: {result.first_write}"]
    unexpected = [r for r in result.requests if r["classification"] == "UNEXPECTED_REQUEST_BLOCKED"]
    lines.append(f"Unexpected requests blocked: {len(unexpected)}")
    for item in result.requests:
        classification = ("EXPECTED NON-MUTATING TELEMETRY" if item['classification'] == "TELEMETRY_BLOCKED"
                          else item['classification'])
        lines.append(f"  {item['method']} {item['origin']}{item['path']} {classification} — {item['disposition']}"
                     f" resource={item['resource_type']} stage={item['stage']}")
    for label, count in (("observed", result.precursor.observed), ("classified", result.precursor.observed),
                         ("allowed", result.precursor.allowed), ("completed", result.precursor.completed),
                         ("failed", result.precursor.failed)):
        lines.append(f"Precursor requests {label}: {count}")
    lines.append("Precursor completion means an HTTP exchange finished; authentication requires rendered UI.")
    lines += [f"Safe diagnostics: {result.diagnostics or 'Unavailable'}",
              "Medium source URL entered: No", "Medium import requests performed: 0",
              "Medium drafts created: 0", "Medium posts published: 0",
              "Substack state changed: No", "SQLite migration performed: No"]
    return "\n".join(lines)
