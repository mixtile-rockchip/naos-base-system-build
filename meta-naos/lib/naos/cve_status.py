# SPDX-License-Identifier: MIT
"""Read source-revision-bound CVE assessments maintained alongside a recipe."""
import configparser
from pathlib import Path
import re


def load(path, revision):
    parser = configparser.ConfigParser(interpolation=None)
    with Path(path).open(encoding='utf-8') as stream:
        parser.read_file(stream)
    if parser.defaults():
        raise ValueError('CVE status INI must not use DEFAULT values')
    if not parser.has_section('source') or not parser.get('source', 'revision', fallback='').strip():
        raise ValueError('CVE status INI requires [source] revision')

    result = {}
    for cve in parser.sections():
        if cve == 'source':
            continue
        if not re.fullmatch(r'CVE-[0-9]{4}-[0-9]{4,}', cve):
            raise ValueError('Invalid CVE status section: ' + cve)
        section = parser[cve]
        unknown = set(section) - {'status', 'description'}
        if unknown:
            raise ValueError('Unknown CVE status fields: ' + ', '.join(sorted(unknown)))
        status = section.get('status', '').strip()
        if not re.fullmatch(r'[a-z][a-z0-9-]*', status):
            raise ValueError('Invalid or missing status for ' + cve)
        description = section.get('description', '').strip()
        result[cve] = status + (': ' + description if description else '')

    declared = parser.get('source', 'revision').strip()
    if result and declared != revision:
        raise ValueError('CVE status revision ' + declared + ' does not match source revision ' + str(revision))
    return result
