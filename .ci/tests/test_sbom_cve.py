"""Check declared CVE evidence using the pinned OE parser and SPDX model."""
import builtins
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if not (ROOT / 'sources/oe-core/meta/lib/oe').is_dir() or not (ROOT / 'sources/bitbake/lib/bb').is_dir():
    raise unittest.SkipTest('Pinned OE-Core and BitBake sources are needed for SPDX checks')
for relative in ('sources/bitbake/lib', 'sources/oe-core/meta/lib', 'meta-naos/lib'):
    sys.path.insert(0, str(ROOT / relative))

import bb
import bb.parse
with patch.object(builtins, 'bb', bb, create=True):
    import oe.cve_check
    import oe.patch
oe.cve_check.bb = bb
oe.patch.bb = bb
import oe.spdx30 as spdx
from naos import sbom_metadata as metadata
from naos import sbom_render as renderer


CVES = ('CVE-2026-109435', 'CVE-2026-109436')


class CveSbomTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='naos-cve-sbom-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.data = bb.data.init()
        for key, value in {'PN': 'linux-naos-rockchip', 'BPN': 'linux-naos-rockchip',
                           'PV': '6.12.69', 'LICENSE': 'GPL-2.0-only',
                           'CVE_PRODUCT': 'linux_kernel linux:linux',
                           'SPDX_INCLUDE_VEX': 'current'}.items():
            self.data.setVar(key, value)
        for reason, status in (('backported-patch', 'Patched'), ('fixed-version', 'Patched'),
                               ('vulnerable-investigating', 'Unpatched'),
                               ('not-applicable-config', 'Ignored'), ('unknown', 'Unknown')):
            self.data.setVarFlag('CVE_CHECK_STATUSMAP', reason, status)
        for cve in CVES:
            self.data.setVarFlag('CVE_STATUS', cve, 'backported-patch: Fixed by ' + 'a' * 40)
        patches = patch.object(oe.patch, 'src_patches', return_value=[])
        patches.start()
        self.addCleanup(patches.stop)

    def component(self):
        with patch.object(metadata, 'sources', return_value=[{
                'uri': 'https://example.invalid/kernel.git', 'revision': 'a' * 40}]), \
             patch.object(metadata, 'license_info', return_value={
                'license_expression': 'GPL-2.0-only', 'license_texts': {}}):
            return metadata.base_component(self.data)

    def render(self, component=None, filename='product.spdx.json'):
        kernel = component or self.component()
        bootloader = {'recipe': 'u-boot-naos-rockchip', 'name': 'u-boot-naos-rockchip',
                      'version': '2025.04', 'license': 'GPL-2.0-or-later',
                      'license_expression': 'GPL-2.0-or-later', 'sources': [], 'payload_paths': []}
        output = self.directory / filename
        summary = renderer.render_product(
            {'files': [], 'packages': []}, [kernel, bootloader],
            {'files': {'factory.img': {'sha256': hashlib.sha256(b'firmware').hexdigest(), 'size': 8}}},
            output, machine='fixture', version='1.0.0', build_id='fixture-cve', epoch=1735689600,
            license_list_version='3.27')
        document = json.loads(output.read_text())
        nodes = {node.get('spdxId', node.get('@id')): node for node in document['@graph']}
        return output, summary, document, nodes

    def test_declared_backports_reach_the_correct_component_and_closed_model(self):
        output, summary, document, nodes = self.render()
        kernel = next(node for node in nodes.values() if node.get('name') == 'linux-naos-rockchip')
        bootloader = next(node for node in nodes.values() if node.get('name') == 'u-boot-naos-rockchip')
        fixes = [node for node in nodes.values() if node['type'] == 'security_VexFixedVulnAssessmentRelationship']
        self.assertEqual(len(fixes), 2)
        identifiers = set()
        for fix in fixes:
            self.assertEqual(fix['relationshipType'], 'fixedIn')
            self.assertEqual(fix['to'], [kernel['spdxId']])
            self.assertNotIn(bootloader['spdxId'], fix['to'])
            self.assertIn('a' * 40, fix['security_statusNotes'])
            vulnerability = nodes[fix['from']]
            self.assertEqual(vulnerability['type'], 'security_Vulnerability')
            identifiers.update(value['identifier'] for value in vulnerability['externalIdentifier'])
        self.assertEqual(identifiers, set(CVES))
        spdx_document = next(node for node in nodes.values() if node['type'] == 'SpdxDocument')
        self.assertIn('security', spdx_document['profileConformance'])
        decoded = spdx.SHACLObjectSet()
        spdx.JSONLDDeserializer().deserialize_data(document, decoded)
        self.assertFalse(decoded.link())
        self.assertTrue(summary['roundtrip_references_closed'])
        self.assertEqual(summary['vulnerability_assessments'], 2)
        self.assertNotIn(str(ROOT), output.read_text())
        self.assertNotIn(str(self.directory), output.read_text())

    def test_nonfixed_states_are_never_rendered_as_fixed(self):
        self.data.setVarFlag('CVE_STATUS', CVES[0], 'vulnerable-investigating: Awaiting validation')
        self.data.setVarFlag('CVE_STATUS', CVES[1], 'not-applicable-config: Driver not compiled')
        self.data.setVarFlag('CVE_STATUS', 'CVE-2026-199999', 'unknown: Investigation pending')
        _, summary, _, nodes = self.render()
        assessments = [node for node in nodes.values() if node['type'].startswith('security_Vex')]
        self.assertEqual({node['relationshipType'] for node in assessments},
                         {'affects', 'doesNotAffect', 'underInvestigationFor'})
        self.assertFalse(any(node.get('relationshipType') == 'fixedIn' for node in nodes.values()))
        self.assertEqual(summary['vulnerability_assessments'], 3)

    def test_work_directory_patch_paths_are_not_exported(self):
        raw = {CVES[0]: {'abbrev-status': 'Patched', 'status': 'fix-file-included',
                        'resource': [str(self.directory / 'workdir/0001-mpp.patch')]}}
        with patch.object(oe.cve_check, 'get_patched_cves', return_value=raw):
            component = self.component()
        self.assertEqual(component['cve_status'][CVES[0]]['patches'], ['0001-mpp.patch'])
        output, _, _, _ = self.render(component)
        self.assertNotIn(str(self.directory), output.read_text())
        self.assertIn('0001-mpp.patch', output.read_text())

    def test_upstream_fixed_filter_and_explicit_disable_match_native_spdx(self):
        self.data.setVarFlag('CVE_STATUS', CVES[1], 'fixed-version: Fixed upstream')
        self.assertEqual(set(metadata.cve_status(self.data)), {CVES[0]})
        self.data.setVar('SPDX_INCLUDE_VEX', 'all')
        self.assertEqual(set(metadata.cve_status(self.data)), set(CVES))
        self.data.setVar('SPDX_INCLUDE_VEX', 'none')
        _, summary, _, nodes = self.render()
        self.assertEqual(summary['vulnerability_assessments'], 0)
        self.assertFalse(any(node['type'].startswith('security_') for node in nodes.values()))

    def test_component_and_assessment_order_do_not_change_output_bytes(self):
        component = self.component()
        first, _, _, _ = self.render(component, 'first.json')
        reordered = deepcopy(component)
        reordered['cve_status'] = dict(reversed(list(component['cve_status'].items())))
        second, _, _, _ = self.render(reordered, 'second.json')
        self.assertEqual(first.read_bytes(), second.read_bytes())


if __name__ == '__main__':
    unittest.main()
