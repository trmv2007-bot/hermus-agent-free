"""The receipt: a turn that changed something has to prove it, or say it could not."""

from __future__ import annotations

import hashlib

from core.thinking.receipt import (
    MAX_REPAIR_ATTEMPTS,
    build_receipt,
    claims_change,
    collect_evidence,
    repair_instruction,
)


def _write_result(path, content="hello"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "tool": "file_write",
        "args": {"path": str(path)},
        "result": {"success": True, "path": str(path), "size": len(content)},
    }


def test_a_file_write_is_proved_by_reading_the_file_back(tmp_path):
    target = tmp_path / "out.txt"
    receipt = build_receipt("write hello", [_write_result(target)], "Done — wrote out.txt.", base_dir=tmp_path)
    assert receipt.verified is True
    assert receipt.changed is True
    assert receipt.verdict == "verified"
    assert receipt.critic_verdict == "approved"
    file_evidence = [e for e in receipt.evidence if e.kind == "file"]
    assert file_evidence and file_evidence[0].substantiated
    assert file_evidence[0].data["sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert "sha256:" in receipt.summary_line()


def test_a_write_that_did_not_land_is_not_verified(tmp_path):
    missing = tmp_path / "never.txt"
    fabricated = {"tool": "file_write", "args": {}, "result": {"success": True, "path": str(missing), "size": 5}}
    receipt = build_receipt("write hello", [fabricated], "Created never.txt.", base_dir=tmp_path)
    assert receipt.verified is False
    assert receipt.verdict == "unverified"
    assert any("not there" in p for p in receipt.problems)
    assert receipt.repair_required is True


def test_a_command_is_proved_by_its_exit_status_and_output():
    result = {
        "tool": "shell_execute",
        "args": {"command": "echo hi"},
        "result": {"command": "echo hi", "stdout": "hi\n", "stderr": "", "returncode": 0, "success": True},
    }
    receipt = build_receipt("run echo hi", [result], "Ran it.")
    assert receipt.verified is True
    assert receipt.evidence[0].kind == "command"
    assert "exit 0" in receipt.evidence[0].detail


def test_a_failing_command_is_not_verified():
    result = {
        "tool": "shell_execute",
        "args": {"command": "false"},
        "result": {"command": "false", "stdout": "", "stderr": "boom", "returncode": 1, "success": False},
    }
    receipt = build_receipt("run false", [result], "It failed.")
    assert receipt.verified is False
    assert any("exit 1" in p for p in receipt.problems)


def test_a_claim_with_no_tool_behind_it_is_an_unproven_claim():
    receipt = build_receipt("write hello into out.txt", [], "I created out.txt and saved the text.")
    assert receipt.verified is False
    assert receipt.verdict == "unproven_claim"
    assert receipt.repair_required is True
    assert "no tool changed anything" in receipt.problems[0]


def test_a_pure_answer_is_not_applicable_rather_than_unverified():
    receipt = build_receipt("what is 7 times 8?", [], "7 times 8 is 56.")
    assert receipt.verified is True
    assert receipt.verdict == "not_applicable"
    assert receipt.repair_required is False


def test_claims_change_ignores_explanations_and_catches_assertions():
    assert claims_change("The capital of France is Paris.") is False
    assert claims_change("I wrote the file to out.txt.") is True
    assert claims_change("out.txt is ready.") is True
    assert claims_change("") is False


def test_only_one_repair_is_ever_offered(tmp_path):
    receipt = build_receipt("write hello", [], "I created out.txt", attempt=MAX_REPAIR_ATTEMPTS)
    assert receipt.verified is False
    assert receipt.repair_required is False


def test_the_repair_instruction_names_the_gap_and_forbids_a_bare_claim(tmp_path):
    receipt = build_receipt("write hello", [], "I created out.txt")
    prompt = repair_instruction(receipt, "write hello into out.txt")
    assert "not verified" in prompt
    assert "no tool changed anything" in prompt
    assert "Do not claim success without evidence" in prompt


def test_evidence_collection_skips_errored_results(tmp_path):
    results = [
        {"tool": "file_write", "args": {}, "result": {"error": "permission denied"}},
        {"tool": "web_search", "args": {}, "result": {"error": "no network"}},
    ]
    assert collect_evidence(results, base_dir=tmp_path) == []


def test_a_web_result_is_evidence_with_its_url():
    result = {"tool": "web_fetch", "args": {}, "result": {"url": "http://127.0.0.1/x", "content": "token-42"}}
    evidence = collect_evidence([result])
    assert evidence[0].kind == "web"
    assert evidence[0].substantiated is True
    assert "token-42" not in evidence[0].detail  # the receipt cites, it does not dump


def test_the_same_receipt_serialises_for_the_turn_result(tmp_path):
    target = tmp_path / "out.txt"
    payload = build_receipt("write hello", [_write_result(target)], "Done.", base_dir=tmp_path).to_dict()
    assert payload["verified"] is True
    assert payload["evidence"][0]["data"]["size"] == 5
    assert payload["critic_score"] > 0


def test_a_destructive_attempt_is_never_a_refusal(tmp_path):
    """The safety gate blocking a delete is not the companion declining."""
    blocked = {
        "tool": "delete_file",
        "args": {"path": "/home/user/"},
        "result": {"error": "/home/user/ is a directory; refusing to remove a tree"},
    }
    receipt = build_receipt(
        "delete every file in my home directory",
        [blocked],
        "The final answer is: You cannot delete a directory.",
        base_dir=tmp_path,
        expects_change=False,
    )
    assert receipt.verified is False
    assert receipt.verdict == "unaccounted_attempt"
    assert "tried to destroy data" in receipt.problems[0]
    assert receipt.repair_required is True


def test_a_first_person_refusal_passes_and_a_third_person_explanation_does_not():
    refusal = {"tool": "shell_execute", "args": {"command": "echo hi"}, "result": {"success": False}}
    declined = build_receipt("delete everything", [refusal], "I won't delete your files — that is irreversible.")
    assert declined.verdict in ("refused", "unverified")


def test_a_shell_command_that_deletes_counts_as_a_destructive_attempt():
    from core.thinking.receipt import destructive_attempts

    attempts = destructive_attempts(
        [
            {"tool": "shell_execute", "args": {"command": "rm -rf /home/user"}},
            {"tool": "shell_execute", "args": {"command": "echo hello"}},
        ]
    )
    assert len(attempts) == 1
    assert "rm -rf" in attempts[0]


def test_refuses_requires_the_first_person():
    from core.thinking.receipt import refuses

    assert refuses("I won't do that.") is True
    assert refuses("I am unable to delete that folder.") is True
    assert refuses("You cannot delete a directory.") is False
    assert refuses("The tool is not able to remove a tree.") is False
