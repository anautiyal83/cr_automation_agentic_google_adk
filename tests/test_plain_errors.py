"""Review screen: technical step errors summarised in plain language (user, 2026-11-11)."""
from __future__ import annotations

from shared.plain_errors import split_reasons, summarize

EIR_STEP6 = (
    "Unresolvable placeholder ${black_counts[$pod]}: variable is never registered by a step (add a register with "
    "a (?<name>…) group on the step that produces it); Unresolvable placeholder ${black_counts[$first_pod]}: "
    "variable is never registered by a step; Unresolvable placeholder ${grey_counts[$pod]}: variable is never "
    "registered by a step; Unresolvable placeholder ${white_counts[$pod]}: variable is never registered by a step; "
    "Step 'ss' (preNodeHealthCheck): validation regex /State\\s+Recv-Q/ does not match the real output recorded "
    "for this command. Real output (relevant lines):\n     LOCAL NODE STATUS:\nWrite the regex against this real "
    "output.\n• Step 'dscli update file /tmp/bulkProvisioning.json -n EIR -v ${HOSTNAME} -r 1' "
    "(activityConfiguration): validation regex /FILEPATH: \\S+/ does not match the real output recorded for this "
    "command. Real output (relevant lines):\n    Removing file: /tmp/bulkProvisioning.json\nWrite the regex.\n"
    "• Step 'dscli update file /tmp/bulkProvisioning.json -n EIR -v ${HOSTNAME} -r 1' (activityConfiguration): "
    "register regex /FILEPATH: (?<FILEPATH>\\S+)/ captures nothing from the real output recorded for this command. "
    "Real output:\n    Removing file; Step 'dscli update file /tmp/bulkProvisioning.json -n EIR -v ${HOSTNAME} "
    "-r 1' (rollbackConfiguration): validation regex /FILEPATH: \\S+/ does not match the real output recorded for "
    "this command.\n• Step 'unzip -l ${configSequences.0.records.0.data.BULK_PROVISIONING_FILE} | grep .json' "
    "(preNodeHealthCheck) uses CIQ column 'BULK_PROVISIONING_FILE', which is empty in 1 of 2 sample rows")


def test_eir_step6_says_what_is_missing_in_the_documents():
    headline, sections, action = summarize(6, split_reasons(EIR_STEP6))
    by = dict(sections)
    assert headline == "Step 6 could not build a working workflow — 4 things to check in your documents"
    assert [s for s, _ in sections] == ["CIQ", "MOP", "Session logs (command outputs)",
                                        "Nothing to change in your documents (Re-run fixes it)"]
    assert by["CIQ"] == ["Column BULK_PROVISIONING_FILE is empty in 1 of 2 rows. Fill it in, or say when it may be "
                         "left empty."]
    assert by["MOP"][0].startswith("`ss`: what does a successful result look like? Your session log shows "
                                   "“LOCAL NODE STATUS:”")
    assert len(by["MOP"]) == 2                                   # ss + dscli (its rollback copy merged)
    assert "Your session log shows “Removing file: /tmp/bulkProvisioning.json”" in by["MOP"][1]
    assert "needs FILEPATH from this command" in by["Session logs (command outputs)"][0]
    assert "shell arrays" in by["Nothing to change in your documents (Re-run fixes it)"][0]
    assert "Edit requirements" in action
    text = " ".join(line for _, lines in sections for line in lines)
    assert "regex" not in text and "placeholder" not in text and "register" not in text


def test_value_without_a_source_and_missing_command_output():
    headline, sections, action = summarize(
        9, ["Unresolvable placeholder ${eirNamespace}: variable is never registered by a step",
            "1/2 tests failed", "TC-P01 Sunny day — CR1 / EIR1: Every executed step passed: oc get pods failed"],
        unmatched_commands=["oc get ns | grep ei1"])
    by = dict(sections)
    assert headline.endswith("2 things to check in your documents")
    assert by["Where a value comes from"] == ["eirNamespace: say where each value comes from — a CIQ column, a "
                                              "command's output, or a fixed value."]
    assert by["Session logs (command outputs)"][0].startswith("`oc get ns | grep ei1`: there is no recorded output")
    assert by["Nothing to change in your documents (Re-run fixes it)"][0].startswith("Test “Sunny day — CR1 / EIR1”")


def test_only_ai_problems_say_nothing_is_missing():
    headline, sections, action = summarize(6, ["Step 'x' (backup): expr 'a.every(v => v)' is not something "
                                                "criteria.expr can evaluate: …"])
    assert headline == "Step 6 could not build a working workflow — nothing missing in your documents"
    assert action.startswith("Re-run step")
