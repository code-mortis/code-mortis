"""Local source-navigation checks; no providers, repository execution or services."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from code_mortis.core.errors import ReviewStopped
from code_mortis.security_scans import context


class SourceNavigationTests(TestCase):
    def snapshot(self, files):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for path, source in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source)
        reads = []

        def source(path):
            reads.append(path)
            return files[path].replace("synthetic_secret", "[redacted]")

        return SimpleNamespace(sha="a" * 40, files=root, source=source,
                               inventory={path: {"code": True} for path in files}), reads

    def navigate(self, item, sources, paths=None, stopped=lambda: False):
        return context.source_navigation(item, sources, list(item.inventory) if paths is None else paths, stopped)

    def test_finds_cross_file_names_attributes_and_aliased_imports_without_execution(self):
        item, reads = self.snapshot({
            "boundary.py": "def scrub(value):\n    return value\n",
            "route.py": "from boundary import scrub as clean\nresult = clean(request.value)\n",
            "worker.py": "result = boundary.scrub(request.value)\n",
            "trap.py": "raise RuntimeError('repository code must never run')\nscrub(value)\n",
        })
        result = self.navigate(item, {"boundary.py": item.source("boundary.py")})
        self.assertEqual({hint["path"] for hint in result["hints"]}, {"route.py", "worker.py", "trap.py"})
        self.assertEqual({hint["relation"] for hint in result["hints"]}, {"unresolved_name_reference"})
        self.assertEqual(result["revision"], item.sha)
        self.assertIn("not resolved callers", result["notice"])
        self.assertFalse(result["truncated"])
        self.assertEqual(reads.count("route.py"), 1)

    def test_comments_strings_similar_names_and_definitions_are_not_references(self):
        item, _ = self.snapshot({
            "boundary.py": "async def validate(value):\n    return value\n",
            "unrelated.py": "# validate(request)\ntext = 'validate(request)'\nvalidate_extra(value)\n"
                            "def validate(value):\n    return value\n",
        })
        result = self.navigate(item, {"boundary.py": item.source("boundary.py")})
        self.assertEqual(result["hints"], [])
        self.assertIn("empty hint list does not establish", result["notice"])

    def test_excluded_noncode_and_unavailable_paths_are_never_read(self):
        item, reads = self.snapshot({
            "boundary.py": "def check(value):\n    return value\n",
            "excluded.py": "check(value)", "hidden.py": "check(value)",
            "route.py": "check(value)", "client.ts": "check(value)",
        })
        item.inventory["excluded.py"]["code"] = False
        result = self.navigate(item, {"boundary.py": item.source("boundary.py")},
                               ["boundary.py", "excluded.py", "route.py", "client.ts"])
        self.assertEqual({hint["path"] for hint in result["hints"]}, {"route.py"})
        self.assertNotIn("hidden.py", reads)
        self.assertNotIn("excluded.py", reads)
        self.assertNotIn("client.ts", reads)
        self.assertEqual(result["omissions"]["unsupported_language"], 1)

    def test_redaction_precedes_parsing_and_no_source_text_is_returned_or_cached(self):
        item, _ = self.snapshot({
            "boundary.py": "def expose(value):\n    return value\n",
            "route.py": "expose(value)\npassword = 'synthetic_secret'\n",
        })
        result = self.navigate(item, {"boundary.py": item.source("boundary.py")})
        self.assertEqual(result["hints"][0]["path"], "route.py")
        self.assertNotIn("synthetic_secret", json.dumps(result))
        self.assertNotIn("password", repr(item._security_source_navigation.value))
        self.assertNotIn("[redacted]", repr(item._security_source_navigation.value))

    def test_cache_is_shared_once_and_invalidated_for_revision_and_eligibility(self):
        item, reads = self.snapshot({"boundary.py": "def run():\n    pass\n", "route.py": "run()"})
        supplied = {"boundary.py": item.source("boundary.py")}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.navigate(item, supplied), range(4)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(reads.count("route.py"), 1)
        item.sha = "b" * 40
        self.assertEqual(self.navigate(item, supplied)["revision"], "b" * 40)
        self.assertEqual(reads.count("route.py"), 2)
        self.assertEqual(self.navigate(item, supplied, ["boundary.py"])["hints"], [])

    def test_invalid_and_unreadable_optional_source_discloses_omissions(self):
        item, _ = self.snapshot({
            "boundary.py": "def active(row):\n    return row.enabled\n",
            "bad.py": "this is not valid python !!!", "missing.py": "active(row)",
            "route.py": "active(row)",
        })
        (item.files / "missing.py").unlink()
        result = self.navigate(item, {"boundary.py": item.source("boundary.py")})
        self.assertTrue(result["truncated"])
        self.assertEqual(result["omissions"]["unreadable"], 1)
        self.assertEqual(result["omissions"]["syntax_or_file_limit"], 1)
        self.assertEqual({hint["path"] for hint in result["hints"]}, {"route.py"})

    def test_scan_and_hint_limits_are_explicit(self):
        item, reads = self.snapshot({"00_boundary.py": "def verify():\n    pass\n",
                                     **{f"{number:02}_route.py": "verify()" for number in range(1, 8)}})
        with patch.multiple(context, MAX_FILES=4, MAX_HINT_PATHS=2):
            result = self.navigate(item, {"00_boundary.py": item.source("00_boundary.py")})
        self.assertEqual(result["searched_files"], 4)
        self.assertEqual(result["omissions"]["scan_limit"], 4)
        self.assertEqual(len(result["hints"]), 2)
        self.assertTrue(result["truncated"])
        self.assertNotIn("04_route.py", reads)

    def test_oversize_file_is_not_read_and_cumulative_bytes_are_bounded(self):
        item, reads = self.snapshot({"00_boundary.py": "def guard():\n    pass\n",
                                     "01_large.py": "guard()\n" + "#" * 200,
                                     "02_route.py": "guard()\n"})
        with patch.multiple(context, MAX_FILE_BYTES=100, MAX_BYTES=24):
            result = self.navigate(item, {"00_boundary.py": item.source("00_boundary.py")})
        self.assertNotIn("01_large.py", reads)
        self.assertNotIn("02_route.py", reads)
        self.assertEqual(result["hints"], [])
        self.assertTrue(result["truncated"])

    def test_cancel_before_build_or_while_waiting_propagates_without_partial_cache(self):
        item, _ = self.snapshot({"boundary.py": "def guard():\n    pass\n"})
        with self.assertRaises(ReviewStopped):
            self.navigate(item, {}, stopped=lambda: True)
        self.assertFalse(hasattr(item, "_security_source_navigation"))
        self.navigate(item, {})
        cancelled = Event()
        item._security_source_navigation.lock.acquire()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.navigate, item, {}, None, cancelled.is_set)
                cancelled.set()
                with self.assertRaises(ReviewStopped):
                    pending.result(timeout=2)
        finally:
            item._security_source_navigation.lock.release()

    def test_non_python_and_oversize_identifiers_are_honest_gaps(self):
        name = "s" * (context.MAX_NAME_BYTES + 1)
        item, _ = self.snapshot({"boundary.py": f"def {name}():\n    pass\n", "route.py": f"{name}()",
                                 "client.ts": "function guard() {}"})
        result = self.navigate(item, {"boundary.py": item.source("boundary.py")})
        self.assertEqual(result["hints"], [])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["omissions"]["unsupported_language"], 1)

    def test_invalid_path_is_rejected_before_local_stat_or_read(self):
        item, reads = self.snapshot({"boundary.py": "pass"})
        item.inventory["../outside.py"] = {"code": True}
        with self.assertRaises(ValueError):
            self.navigate(item, {})
        self.assertEqual(reads, [])

    def test_relative_dependencies_reuse_conservative_resolution_and_eligible_paths(self):
        item, reads = self.snapshot({
            "pkg/boundary.py": "from .guard import protect\nfrom . import unknown\nfrom external import helper\n",
            "pkg/guard.py": "def protect(value):\n    return value\n",
            "pkg/hidden.py": "pass", "unrelated/guard.py": "pass",
        })
        supplied = {"pkg/boundary.py": item.source("pkg/boundary.py")}
        result = self.navigate(item, supplied, ["pkg/boundary.py", "pkg/guard.py", "unrelated/guard.py"])
        self.assertEqual(result["hints"], [{"path": "pkg/guard.py", "from_path": "pkg/boundary.py",
                                            "line": 1, "relation": "imports_file"}])
        self.assertNotIn("pkg/hidden.py", reads)

    def test_hint_payload_has_a_separate_byte_limit(self):
        item, _ = self.snapshot({"boundary.py": "def guard():\n    pass\n",
                                 "route.py": "guard()", "worker.py": "guard()"})
        with patch.object(context, "MAX_HINT_BYTES", 150):
            result = self.navigate(item, {"boundary.py": item.source("boundary.py")})
        self.assertEqual(len(result["hints"]), 1)
        self.assertLessEqual(len(json.dumps(result["hints"]).encode()), 150)
        self.assertTrue(result["truncated"])

    def test_cancellation_during_build_never_publishes_a_partial_index(self):
        item, _ = self.snapshot({"boundary.py": "def guard():\n    pass\n", "route.py": "guard()"})
        original = item.source
        cancelled = Event()

        def read(path):
            source = original(path)
            cancelled.set()
            return source

        item.source = read
        with self.assertRaises(ReviewStopped):
            self.navigate(item, {}, stopped=cancelled.is_set)
        self.assertIsNone(item._security_source_navigation.value)
        self.assertIsNone(item._security_source_navigation.key)

    def test_already_supplied_python_sources_skip_index_and_parsing(self):
        files = {"boundary.py": "from .guard import check\ndef verify(value):\n    return check(value)\n",
                 "guard.py": "def check(value):\n    return value\n", "client.ts": "verify(value)"}
        item, reads = self.snapshot(files)
        supplied = {path: text for path, text in files.items() if path.endswith(".py")}
        with patch.object(context, "_tree", side_effect=AssertionError("Redundant source parsing")):
            result = self.navigate(item, supplied)
        self.assertEqual(reads, [])
        self.assertFalse(hasattr(item, "_security_source_navigation"))
        self.assertEqual(result["hints"], [])
        self.assertEqual(result["searched_files"], 0)
        self.assertEqual(result["omissions"]["unsupported_language"], 1)
