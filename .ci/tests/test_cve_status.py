"""Validate the manual INI contract without fetching or compiling sources."""
import configparser
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('naos_cve_status', ROOT / 'meta-naos/lib/naos/cve_status.py')
cve_status = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cve_status)


class CveStatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='naos-cve-ini-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'cve.ini'
        self.revision = 'a' * 40

    def load(self, content, revision=None):
        self.path.write_text(content)
        return cve_status.load(self.path, self.revision if revision is None else revision)

    def test_matching_revision_exports_standard_status_with_literal_description(self):
        result = self.load('[source]\nrevision=' + self.revision + '\n'
            '[CVE-2026-109435]\nstatus=backported-patch\n'
            'description=Fixed by abc; https://example.invalid/patch?coverage=100%\n'
            '[CVE-2026-109436]\nstatus=backported-patch\n')
        self.assertEqual(result, {
            'CVE-2026-109435': 'backported-patch: Fixed by abc; https://example.invalid/patch?coverage=100%',
            'CVE-2026-109436': 'backported-patch'})

    def test_nonempty_list_cannot_mark_another_revision(self):
        with self.assertRaisesRegex(ValueError, 'does not match source revision'):
            self.load('[source]\nrevision=' + 'b' * 40 + '\n'
                      '[CVE-2026-109435]\nstatus=backported-patch\n')

    def test_empty_list_allows_a_revision_change(self):
        self.assertEqual(self.load('[source]\nrevision=' + 'b' * 40 + '\n'), {})

    def test_other_assessments_are_preserved_without_becoming_fixed(self):
        self.assertEqual(self.load('[source]\nrevision=' + self.revision + '\n'
            '[CVE-2026-109435]\nstatus=vulnerable-investigating\ndescription=Awaiting vendor confirmation\n'),
            {'CVE-2026-109435': 'vulnerable-investigating: Awaiting vendor confirmation'})

    def test_missing_source_revision_and_invalid_fields_fail(self):
        for content in ('[source]\n', '[CVE-2026-109435]\nstatus=backported-patch\n',
                        '[source]\nrevision=a\n[not-a-cve]\nstatus=backported-patch\n',
                        '[source]\nrevision=a\n[CVE-2026-109435]\nstatus=\n',
                        '[source]\nrevision=a\n[CVE-2026-109435]\nstatus=Patched\n',
                        '[source]\nrevision=a\n[CVE-2026-109435]\nstatus=backported-patch\nextra=value\n',
                        '[DEFAULT]\nstatus=backported-patch\n[source]\nrevision=a\n'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.load(content)

    def test_duplicate_sections_are_rejected(self):
        with self.assertRaises(configparser.DuplicateSectionError):
            self.load('[source]\nrevision=a\n[source]\nrevision=a\n')

if __name__ == '__main__':
    unittest.main()
