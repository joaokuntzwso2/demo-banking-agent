"""Idempotent project preflight for Agent Manager 1.0.0.

All calls here are mocked. No live project writes or credentials are required.
"""
import importlib.util
import json
import pathlib
import subprocess
import unittest
from unittest import mock

ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('aurora_project_reconcile_v9',ROOT/'scripts/aurora-full-reconcile.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def result(data=None, rc=0, error=''):
    return subprocess.CompletedProcess([],rc,json.dumps(data) if data is not None else '',error)


def inventory(names, total=None):
    data={'projects':[{'name':x} for x in names]}
    data['total']=len(names) if total is None else total
    return {'instance':'default','org':'default','data':data}


class ProjectV9Tests(unittest.TestCase):
    def execute(self, responses):
        calls=[]
        def fake_cmd(args, **kwargs):
            calls.append(tuple(args))
            if not responses:
                self.fail('unexpected CLI call: '+str(args))
            return responses.pop(0)
        with mock.patch.object(m,'cmd',side_effect=fake_cmd):
            m.ensure_project()
        return calls

    def test_existing_project_from_list_skips_get_and_create(self):
        calls=self.execute([result(inventory(['default','banco-aurora'],total=2))])
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][1:3],('project','list'))

    def test_existing_project_without_top_level_status(self):
        calls=self.execute([result({'data':{'projects':[{'name':m.PROJECT}], 'total':1}})])
        self.assertEqual(len(calls),1)

    def test_existing_target_even_on_larger_inventory(self):
        calls=self.execute([result(inventory([m.PROJECT],total=50))])
        self.assertEqual(len(calls),1)

    def test_get_success_when_list_stale(self):
        calls=self.execute([result(inventory([])), result({'data':{'name':m.PROJECT}})])
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[1][1:3],('project','get'))

    def test_list_conflict_blocks_create(self):
        with mock.patch.object(m,'cmd',return_value=result(rc=1,error='X Conflict')) as c:
            with self.assertRaisesRegex(RuntimeError,'project creation is blocked'):
                m.ensure_project()
        self.assertEqual(c.call_count,1)

    def test_get_conflict_blocks_create(self):
        with mock.patch.object(m,'cmd',side_effect=[result(inventory([])),result(rc=1,error='X Conflict')]) as c:
            with self.assertRaisesRegex(RuntimeError,'refusing project creation'):
                m.ensure_project()
        self.assertEqual(c.call_count,2)

    def test_get_403_blocks_create(self):
        with mock.patch.object(m,'cmd',side_effect=[result(inventory([])),result(rc=1,error='Forbidden: 403')]) as c:
            with self.assertRaisesRegex(RuntimeError,'refusing project creation'):
                m.ensure_project()
        self.assertEqual(c.call_count,2)

    def test_listing_incomplete_blocks_create(self):
        with mock.patch.object(m,'cmd',return_value=result(inventory(['other'],total=2))) as c:
            with self.assertRaisesRegex(RuntimeError,'paginated'):
                m.ensure_project()
        self.assertEqual(c.call_count,1)

    def test_malformed_inventory_refuses_create(self):
        for payload in [{'data':{}},{'data':{'projects':'wrong'}},
                        {'data':{'projects':[{'id':'missing-name'}], 'total':1}},
                        {'status':'error','data':inventory([])['data']}]:
            with self.subTest(payload=payload),mock.patch.object(m,'cmd',return_value=result(payload)) as c:
                with self.assertRaises(RuntimeError):
                    m.ensure_project()
                self.assertEqual(c.call_count,1)

    def test_double_project_records_refused(self):
        with mock.patch.object(m,'cmd',return_value=result(inventory([m.PROJECT,m.PROJECT]))):
            with self.assertRaisesRegex(RuntimeError,'Multiple project records'):
                m.ensure_project()

    def test_json_error_or_200_error_envelope_never_creates(self):
        cases=[subprocess.CompletedProcess([],0,'X Conflict',''),
               result({'status':'error','data':{'projects':[]}})]
        for case in cases:
            with self.subTest(case=case.stdout),mock.patch.object(m,'cmd',return_value=case) as c:
                with self.assertRaises(RuntimeError):
                    m.ensure_project()
                self.assertEqual(c.call_count,1)

    def test_get_json_wrong_project_is_not_success(self):
        with mock.patch.object(m,'cmd',side_effect=[result(inventory([])),result({'data':{'name':'other'}})]) as c:
            with self.assertRaisesRegex(RuntimeError,'requested project identity'):
                m.ensure_project()
        self.assertEqual(c.call_count,2)

    def test_confirmed_absence_create_once_and_verify(self):
        calls=self.execute([result(inventory([])),
                            result(rc=1,error='HTTP 404 Not Found'),
                            result({'status':'ok'}),
                            result(inventory([m.PROJECT]))])
        self.assertEqual([x[1:3] for x in calls],
                         [('project','list'),('project','get'),('project','create'),('project','list')])

    def test_concurrent_creation_409_reuse_after_inventory_readback(self):
        calls=self.execute([result(inventory([])),
                            result(rc=1,error='X Not Found'),
                            result(rc=1,error='X Conflict'),
                            result(inventory([m.PROJECT]))])
        self.assertEqual(sum(x[2]=='create' for x in calls),1)

    def test_concurrent_409_absence_stops_no_duplicate(self):
        with mock.patch.object(m,'cmd',side_effect=[result(inventory([])),result(rc=1,error='404'),
                                               result(rc=1,error='Conflict'),result(inventory([])),
                                               result(rc=1,error='Not Found')]) as c:
            with self.assertRaisesRegex(RuntimeError,'was not verified'):
                m.ensure_project()
        self.assertEqual(sum(x.args[0][2]=='create' for x in c.call_args_list),1)

    def test_nonconflict_create_error_does_not_retry(self):
        with mock.patch.object(m,'cmd',side_effect=[result(inventory([])),result(rc=1,error='404'),
                                               result(rc=1,error='Forbidden')]) as c:
            with self.assertRaisesRegex(RuntimeError,'Project create failed'):
                m.ensure_project()
        self.assertEqual(c.call_count,3)

    def test_created_success_eventual_consistency(self):
        calls=[result(inventory([])),result(rc=1,error='Not Found'),result({}),
               result(inventory([])),result(inventory([m.PROJECT]))]
        with mock.patch.object(m,'cmd',side_effect=calls) as c, mock.patch.object(m.time,'sleep') as sleeper:
            m.ensure_project()
        self.assertEqual(c.call_count,5)
        sleeper.assert_called_once_with(1)

    def test_create_success_but_no_readback_fails(self):
        responses=[result(inventory([])),result(rc=1,error='404'),result({})]
        responses.extend(result(inventory([])) for _ in range(5))
        responses.append(result(rc=1,error='Not Found'))
        with mock.patch.object(m,'cmd',side_effect=responses) as c, mock.patch.object(m.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'not visible'):
                m.ensure_project()
        self.assertEqual(sum(x.args[0][2]=='create' for x in c.call_args_list),1)

if __name__=='__main__':
    unittest.main()
