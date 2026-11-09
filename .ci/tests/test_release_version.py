import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

try:
    import yaml
except ImportError:
    yaml = None


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".ci/release-version.py"
spec = importlib.util.spec_from_file_location("release_version", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseVersionTests(unittest.TestCase):
    def test_hyphenated_machine_has_an_unambiguous_version(self):
        self.assertEqual(release.release_version("mixtile-core3588e-v2", "mixtile-core3588e-v2-1.0.0"), "1.0.0")
        self.assertEqual(release.release_version("board-v2", "board-v2-10.20.300"), "10.20.300")

    def test_tag_must_match_the_exact_machine(self):
        for tag in ("other-board-1.0.0", "board-v2-extra-1.0.0", "1.0.0", ""):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.release_version("board-v2", tag)

    def test_version_must_have_three_numeric_fields_without_suffixes(self):
        for version in ("v1.0.0", "1.0", "1.0.0.1", "1.0.0-dev", "1.0.0-r0",
                        "1.0.0+build", "01.0.0", "1.02.3", "1.2.03", "1.0.0\n"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                release.release_version("board", "board-" + version)

    def test_machine_does_not_allow_path_or_shell_injection(self):
        for machine in ("../board", "board/child", "board; command", "", "."):
            with self.subTest(machine=machine), self.assertRaises(ValueError):
                release.release_version(machine, machine + "-1.0.0")

    def test_cli_outputs_only_the_version(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--machine", "mixtile-core3588e-v2",
                                 "--tag", "mixtile-core3588e-v2-1.0.0"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "1.0.0\n")
        self.assertEqual(result.stderr, "")

    def test_cli_rejects_a_mismatched_board(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--machine", "board-v2",
                                 "--tag", "other-board-1.0.0"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")


@unittest.skipIf(yaml is None, "PyYAML is needed for the CI configuration check")
class PipelineTests(unittest.TestCase):
    def test_four_independent_yocto_products_have_separate_artifacts(self):
        config = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        self.assertEqual(config["stages"], ["build", "package"])
        self.assertNotIn("NAOS_PROFILE", config["variables"])
        self.assertNotIn("NAOS_PACKAGE", config["variables"])
        build = config[".yocto-build"]
        self.assertEqual(build["resource_group"], "yocto-${CI_PROJECT_ID}")
        self.assertEqual(build["rules"][0]["when"], "on_success")
        self.assertIn("$CI_COMMIT_TAG", build["rules"][0]["if"])
        self.assertEqual(build["rules"][-1], {"when": "manual"})
        self.assertFalse(build["allow_failure"])
        for profile in ("development", "production"):
            for kind, mode in (("image", "0"), ("inputs", "1")):
                with self.subTest(profile=profile, kind=kind):
                    job = config["build-" + profile + "-" + kind]
                    self.assertEqual(job["variables"]["NAOS_PROFILE"], profile)
                    self.assertEqual(job["extends"], ".yocto-" + kind)
                    template = config[job["extends"]]
                    self.assertEqual(template["variables"]["NAOS_PACKAGE"], mode)
                    self.assertIn("-" + kind + "-", template["artifacts"]["name"])
                    if kind == "image":
                        self.assertFalse(any(path.startswith("package/") for path in template["artifacts"]["paths"]))
                    else:
                        self.assertEqual(template["artifacts"]["paths"], ["package/${NAOS_PROFILE}/${MACHINE}/"])

    def test_each_apk_job_downloads_only_its_matching_input_archive(self):
        config = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        package = config[".package-inputs"]
        self.assertEqual(package["tags"], ["apk-builder-runner"])
        self.assertEqual(package["resource_group"], "apk-repository-${NAOS_PROFILE}")
        self.assertIn("$CI_COMMIT_TAG", package["rules"][0]["if"])
        self.assertEqual(package["rules"][-1], {"when": "never"})
        self.assertEqual(package["artifacts"]["paths"],
                         ["apk-output/${NAOS_PROFILE}/*.apk", "apk-output/${NAOS_PROFILE}/package.json"])
        for profile in ("development", "production"):
            with self.subTest(profile=profile):
                job = config["package-" + profile]
                self.assertEqual(job["extends"], ".package-inputs")
                self.assertEqual(job["variables"]["NAOS_PROFILE"], profile)
                self.assertEqual(job["needs"], [{"job": "build-" + profile + "-inputs", "artifacts": True}])


if __name__ == "__main__":
    unittest.main()
