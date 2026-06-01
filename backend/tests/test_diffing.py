from __future__ import annotations

import unittest

from app.services.diffing import (
    analyze_unified_diff,
    apply_unified_diff,
    choose_patch_strategy,
    validate_diff,
    DiffValidation,
)


class DiffingTests(unittest.TestCase):
    def test_apply_unified_diff_accepts_fenced_diff(self) -> None:
        original = "alpha\nbeta\ngamma\n"
        diff = """```diff
--- a/sample.txt
+++ b/sample.txt
@@ -1,3 +1,4 @@
 alpha
 beta
+beta-2
 gamma
```"""

        updated = apply_unified_diff(original, diff, "sample.txt")

        self.assertEqual(updated, "alpha\nbeta\nbeta-2\ngamma\n")

    def test_apply_unified_diff_preserves_original_context_whitespace(self) -> None:
        original = "function demo() {\n    return true;\n}\n"
        diff = """@@ -1,3 +1,4 @@
 function demo() {
     return true;
+    console.log('patched');
 }
"""

        updated = apply_unified_diff(original, diff, "sample.js")

        self.assertEqual(
            updated,
            "function demo() {\n    return true;\n    console.log('patched');\n}\n",
        )

    def test_choose_patch_strategy_prefers_rewrite_for_large_patch(self) -> None:
        original = "\n".join(f"line {index}" for index in range(1, 301)) + "\n"
        replacement_lines = "\n".join(f"+updated {index}" for index in range(1, 181))
        removed_lines = "\n".join(f"-line {index}" for index in range(1, 181))
        diff = f"""@@ -1,180 +1,180 @@
{removed_lines}
{replacement_lines}
"""

        stats = analyze_unified_diff(original, diff, "large.txt")
        strategy, reason = choose_patch_strategy(stats)

        self.assertEqual(strategy, "rewrite")
        self.assertIn("changes", reason)


    # ------------------------------------------------------------------
    # CRLF normalisation
    # ------------------------------------------------------------------

    def test_apply_unified_diff_handles_crlf_line_endings_in_original(self) -> None:
        """Files with CRLF line endings must patch cleanly (\\r stripped before comparison)."""
        original = "alpha\r\nbeta\r\ngamma\r\n"
        diff = """\
@@ -1,3 +1,4 @@
 alpha
 beta
+beta-2
 gamma
"""
        updated = apply_unified_diff(original, diff, "sample.txt")
        self.assertIn("beta-2", updated)
        self.assertIn("alpha", updated)

    def test_apply_unified_diff_handles_mixed_crlf_and_lf(self) -> None:
        """Mixed CRLF/LF in context lines must not cause a mismatch error."""
        original = "line1\r\nline2\nline3\r\n"
        diff = """\
@@ -1,3 +1,4 @@
 line1
 line2
+inserted
 line3
"""
        updated = apply_unified_diff(original, diff, "sample.txt")
        self.assertIn("inserted", updated)

    # ------------------------------------------------------------------
    # validate_diff — pre-validation before applying
    # ------------------------------------------------------------------

    def test_validate_diff_returns_applicable_for_valid_diff(self) -> None:
        original = "alpha\nbeta\ngamma\n"
        diff = """\
@@ -1,3 +1,4 @@
 alpha
 beta
+new-line
 gamma
"""
        result = validate_diff(original, diff)
        self.assertIsInstance(result, DiffValidation)
        self.assertTrue(result.applicable)
        self.assertEqual(result.hunk_issues, [])

    def test_validate_diff_returns_not_applicable_when_context_missing(self) -> None:
        original = "alpha\nbeta\ngamma\n"
        diff = """\
@@ -1,3 +1,4 @@
 does-not-exist
 beta
+new-line
 gamma
"""
        result = validate_diff(original, diff)
        self.assertFalse(result.applicable)
        self.assertTrue(len(result.hunk_issues) > 0)
        self.assertIn("Hunk 1", result.hunk_issues[0])

    def test_validate_diff_reports_all_failing_hunks(self) -> None:
        """Every unlocatable hunk must appear in hunk_issues, not just the first one."""
        original = "line1\nline2\nline3\nline4\n"
        diff = """\
@@ -1,2 +1,3 @@
 missing-context-a
 line2
+new1
@@ -3,2 +4,3 @@
 missing-context-b
 line4
+new2
"""
        result = validate_diff(original, diff)
        self.assertFalse(result.applicable)
        self.assertEqual(len(result.hunk_issues), 2)

    def test_validate_diff_not_applicable_for_malformed_diff_header(self) -> None:
        """A diff with no valid @@ header must return applicable=False immediately."""
        result = validate_diff("some content\n", "not a diff at all")
        self.assertFalse(result.applicable)
        self.assertIn("Invalid", result.reason)

    def test_validate_diff_multi_hunk_all_pass(self) -> None:
        original = "a\nb\nc\nd\ne\n"
        diff = """\
@@ -1,2 +1,3 @@
 a
+a2
 b
@@ -4,2 +5,3 @@
 d
+d2
 e
"""
        result = validate_diff(original, diff)
        self.assertTrue(result.applicable, result.hunk_issues)

    # ------------------------------------------------------------------
    # apply_unified_diff — error messages include expected vs found
    # ------------------------------------------------------------------

    def test_apply_unified_diff_error_message_includes_hunk_number_and_context(self) -> None:
        """Error for an unlocatable hunk includes the hunk number and the expected context line."""
        original = "alpha\nbeta\ngamma\n"
        diff = """\
@@ -1,3 +1,3 @@
 does-not-exist
-beta
+replacement
 gamma
"""
        try:
            apply_unified_diff(original, diff, "sample.txt")
            self.fail("Expected ValueError")
        except ValueError as exc:
            msg = str(exc)
            self.assertIn("Hunk 1", msg)
            self.assertIn("does-not-exist", msg)  # the expected context line is shown

    def test_apply_unified_diff_error_message_includes_near_line_number(self) -> None:
        """Error for an unlocatable hunk includes 'near line N' to help the model locate the problem."""
        original = "x\ny\nz\n"
        diff = """\
@@ -2,2 +2,2 @@
 missing-context
-z
+new
"""
        try:
            apply_unified_diff(original, diff, "sample.txt")
            self.fail("Expected ValueError")
        except ValueError as exc:
            msg = str(exc)
            self.assertIn("near line", msg)  # "near line 2" appears in error

    def test_apply_unified_diff_second_hunk_error_references_second_hunk(self) -> None:
        """An error on the second hunk must say 'Hunk 2', not 'Hunk 1'."""
        original = "a\nb\nc\nd\ne\n"
        # First hunk matches, second hunk does not
        diff = """\
@@ -1,2 +1,3 @@
 a
+a2
 b
@@ -3,3 +4,3 @@
 no-such-line
-d
+d2
"""
        try:
            apply_unified_diff(original, diff, "sample.txt")
            self.fail("Expected ValueError")
        except ValueError as exc:
            msg = str(exc)
            self.assertIn("Hunk 2", msg)

    # ------------------------------------------------------------------
    # choose_patch_strategy thresholds (raised from previous session)
    # ------------------------------------------------------------------

    def test_choose_patch_strategy_rewrite_at_300_changed_lines(self) -> None:
        """≥300 changed lines triggers rewrite (threshold raised from 240 → 300)."""
        original = "\n".join(f"line {i}" for i in range(1, 401)) + "\n"
        removed = "\n".join(f"-line {i}" for i in range(1, 302))
        added = "\n".join(f"+new {i}" for i in range(1, 302))
        diff = f"@@ -1,301 +1,301 @@\n{removed}\n{added}\n"
        stats = analyze_unified_diff(original, diff, "big.js")
        strategy, _ = choose_patch_strategy(stats)
        self.assertEqual(strategy, "rewrite")

    def test_choose_patch_strategy_diff_below_300_changed_lines(self) -> None:
        """Small targeted edits on a large file do NOT force rewrite.

        500 lines, 4 hunks × (3 removed + 3 added) = 24 changed lines total.
        change_ratio ≈ 5%, largest_hunk_change = 6, hunk_count = 4 — all below every threshold.
        """
        lines = [f"line{i}" for i in range(1, 501)]
        original = "\n".join(lines) + "\n"
        hunk_blocks = []
        for i in range(4):
            base = i * 100 + 10
            removed = "\n".join(f"-line{base + j}" for j in range(3))
            added = "\n".join(f"+new{base + j}" for j in range(3))
            hunk_blocks.append(f"@@ -{base},3 +{base},3 @@\n{removed}\n{added}")
        diff = "\n".join(hunk_blocks) + "\n"
        stats = analyze_unified_diff(original, diff, "medium.js")
        strategy, _ = choose_patch_strategy(stats)
        self.assertEqual(strategy, "diff")

    def test_choose_patch_strategy_rewrite_at_15_hunks(self) -> None:
        """≥15 hunks triggers rewrite (threshold raised from 8 → 14)."""
        lines = [f"line{i}" for i in range(1, 200)]
        original = "\n".join(lines) + "\n"
        hunk_blocks = []
        for i in range(1, 16):
            hunk_blocks.append(f"@@ -{i * 10},{2} +{i * 10},{2} @@\n-line{i * 10}\n+replaced{i * 10}")
        diff = "\n".join(hunk_blocks) + "\n"
        stats = analyze_unified_diff(original, diff, "many_hunks.js")
        strategy, reason = choose_patch_strategy(stats)
        self.assertEqual(strategy, "rewrite")
        self.assertIn("hunks", reason)

    def test_choose_patch_strategy_diff_at_14_hunks(self) -> None:
        """14 hunks alone does NOT trigger rewrite (threshold is >14)."""
        lines = [f"line{i}" for i in range(1, 200)]
        original = "\n".join(lines) + "\n"
        hunk_blocks = []
        for i in range(1, 15):
            hunk_blocks.append(f"@@ -{i * 10},{2} +{i * 10},{2} @@\n-line{i * 10}\n+replaced{i * 10}")
        diff = "\n".join(hunk_blocks) + "\n"
        stats = analyze_unified_diff(original, diff, "fourteen_hunks.js")
        strategy, _ = choose_patch_strategy(stats)
        self.assertEqual(strategy, "diff")

    def test_choose_patch_strategy_rewrite_for_single_large_hunk(self) -> None:
        """A single hunk changing >150 lines triggers rewrite (threshold raised from 120 → 150)."""
        original = "\n".join(f"old{i}" for i in range(1, 200)) + "\n"
        removed = "\n".join(f"-old{i}" for i in range(1, 153))
        added = "\n".join(f"+new{i}" for i in range(1, 153))
        diff = f"@@ -1,152 +1,152 @@\n{removed}\n{added}\n"
        stats = analyze_unified_diff(original, diff, "large_hunk.js")
        strategy, reason = choose_patch_strategy(stats)
        self.assertEqual(strategy, "rewrite")
        self.assertIn("one hunk", reason)


if __name__ == "__main__":
    unittest.main()
