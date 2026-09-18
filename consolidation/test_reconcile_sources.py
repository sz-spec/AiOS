import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import reconcile_sources as rs

class ReconciliationTests(unittest.TestCase):
    def test_exclusions_preserve_templates_and_source(self):
        self.assertIsNone(rs.omission('.env.example'))
        self.assertIsNone(rs.omission('kernel/src/mm/build_policy.c'))
        for path in ['.env.production','backend/.coverage','kernel/build-hyperv/object.o',
                     'backend/data/session.json','kernel/boot/limine/autom4te.cache/output.0']:
            self.assertIsNotNone(rs.omission(path))

    def test_scan_does_not_follow_external_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'a.c').write_text('source');(p/'escape').symlink_to('/etc')
            files,excluded=rs.scan(p)
            self.assertEqual(set(files),{'a.c'})
            self.assertTrue(any(x['path']=='escape' for x in excluded))

    def test_symbols_are_review_hints_not_text_matches(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a.py';p.write_text('"def fake():"\nclass Real:\n def method(self): pass\n')
            self.assertEqual(rs.symbols(p),['Real','method'])

    def test_real_git_modified_untracked_and_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'donor';source.mkdir()
            def git(*args):subprocess.run(['git','-C',str(source),*args],check=True,capture_output=True)
            git('init');(source/'edit.c').write_text('old');(source/'deleted.c').write_text('old')
            git('add','.');git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','fixture')
            (source/'edit.c').write_text('new');(source/'deleted.c').unlink();(source/'new.c').write_text('new')
            files,_=rs.scan(source)
            with patch.object(rs,'SOURCE_ROOT',root):result=rs.git_snapshot('donor',files)
            self.assertEqual({x['path']:x['state'] for x in result['local_changes']},
                             {'edit.c':'modified-versus-HEAD','deleted.c':'deleted-or-unfollowed-symlink','new.c':'untracked'})

    def test_missing_head_is_not_clean(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'donor').mkdir();(root/'donor/a.c').write_text('a')
            files,_=rs.scan(root/'donor')
            with patch.object(rs,'SOURCE_ROOT',root):result=rs.git_snapshot('donor',files)
            self.assertIsNone(result['head'])
            self.assertEqual(result['local_changes'][0]['state'],'history-unavailable')

    def test_generated_ledger_has_complete_source_and_component_accounting(self):
        report=json.loads((rs.OUT/'file-ledger.json').read_text())
        catalog=json.loads((rs.ROOT/'consolidation/component-decisions.json').read_text())
        self.assertEqual(set(report['sources']),set(rs.SOURCES))
        self.assertEqual(sum(report['file_status_counts'].values()),len(report['files']))
        self.assertEqual(len({r['path'] for r in report['files']}),len(report['files']))
        for row in report['files']:
            self.assertIn(row['component'],catalog)
            self.assertEqual(set(row['sources']),set(row['exact_matches_elsewhere'])|set(row['unretained_source_variants']))
            if row['status']=='selected-content-retained':
                self.assertTrue(row['exact_source_matches'])
                for source in row['exact_source_matches']:
                    self.assertEqual(row['canonical_sha256'],row['sources'][source]['sha256'])

    def test_generated_ledger_embeds_native_path_dispositions(self):
        report=json.loads((rs.OUT/'file-ledger.json').read_text())
        decisions=json.loads((rs.ROOT/'consolidation/native-source-dispositions.json').read_text())
        expected={path for group in decisions['dispositions'] for path in group['paths']}
        embedded={row['path'] for row in report['files'] if 'path_disposition' in row}
        self.assertEqual(embedded,expected)

if __name__=='__main__':unittest.main()
