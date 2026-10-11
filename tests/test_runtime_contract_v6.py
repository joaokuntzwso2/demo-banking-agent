"""Regression fixtures drawn from live Banco Aurora AMP 1.0.0 responses."""
import importlib.util
from pathlib import Path
import unittest
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import aurora_runtime_contract as c
spec=importlib.util.spec_from_file_location('reconcile',ROOT/'scripts'/'aurora-full-reconcile.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class RuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.name='aurora-account'
        self.url='http://default-default.am-gateway.localhost:19080/aurora-account'
        self.deploy={'default':{'endpoints':[{'name':'aurora-account-endpoint','url':self.url,'visibility':'external'}],
                                'environmentDisplayName':'Default','imageId':'image:v1-63479426','status':'active'}}
        self.svc={'metadata':{'name':self.name},'spec':{'ports':[{'port':8000,'targetPort':8000}]}}
        self.pod={'metadata':{'name':'aurora-account-7gtcz'},'spec':{'containers':[
            {'name':'main','startupProbe':{'tcpSocket':{'port':8000}},'env':[{'name':'PORT','value':'8000'}]}]},
            'status':{'phase':'Running','containerStatuses':[{'name':'main','ready':True}],
                'conditions':[{'type':'Ready','status':'True'}]}}

    def test_actual_environment_keyed_deployment_url(self):
        self.assertEqual(c.invoke_url(self.deploy,self.name),self.url)
    def test_failed_deployment_has_url_but_cannot_invoke(self):
        self.deploy['default']['status']='failed'
        with self.assertRaisesRegex(c.ContractError,'not active'):
            c.invoke_url(self.deploy,self.name)
    def test_in_progress_not_invocable(self):
        self.deploy['default']['status']='in-progress'
        with self.assertRaises(c.ContractError):
            c.invoke_url(self.deploy,self.name)
    def test_unknown_status_refuses(self):
        self.deploy['default']['status']='whatever'
        with self.assertRaises(c.ContractError):
            c.deployment_entry(self.deploy,self.name)
    def test_url_does_not_come_from_agent_get_shape(self):
        with self.assertRaises(c.ContractError):
            c.invoke_url({'data':{'name':self.name}},self.name)
    def test_external_url_cannot_be_an_arbitrary_host(self):
        self.deploy['default']['endpoints'][0]['url']='https://evil.example/aurora-account'
        with self.assertRaises(c.ContractError):
            c.invoke_url(self.deploy,self.name)
    def test_wrong_agent_endpoint_fails(self):
        self.deploy['default']['endpoints'][0]['url']=self.url.replace('aurora-account','aurora-card')
        with self.assertRaises(c.ContractError):
            c.invoke_url(self.deploy,self.name)
    def test_duplicate_external_endpoints_fail(self):
        self.deploy['default']['endpoints']*=2
        with self.assertRaises(c.ContractError):
            c.invoke_url(self.deploy,self.name)
    def test_active_pod_8000_is_ready(self):
        self.assertTrue(c.pod_readiness([self.pod],self.name)[0])
        c.service_port(self.svc,self.name)
    def test_actual_8080_probe_mismatch_detected(self):
        self.pod['spec']['containers'][0]['startupProbe']['tcpSocket']['port']=8080
        with self.assertRaises(c.ContractError):
            c.pod_readiness([self.pod],self.name)
    def test_actual_8080_port_env_unhealthy(self):
        self.pod['spec']['containers'][0]['env'][0]['value']='8080'
        result=c.pod_readiness([self.pod],self.name)
        self.assertFalse(result[0]); self.assertEqual(result[2],'8080')
    def test_service_8000_required(self):
        self.svc['spec']['ports'][0]['targetPort']=8080
        with self.assertRaises(c.ContractError):
            c.service_port(self.svc,self.name)
    def test_empty_pods_unhealthy(self):
        self.assertFalse(c.pod_readiness([],self.name)[0])
    def test_deployment_build_uses_real_commit_contract(self):
        sha='634794269c3f'+'e'*28
        record={'agentName':self.name,'projectName':c.PROJECT,'status':'Completed','buildName':'build-1',
                'startedAt':'2026-10-10T17:01:22Z',
                'buildParameters':{'appPath':'/deployment/agent-manager/apps/'+self.name,
                                   'branch':'main','repoUrl':'https://github.com/joaokuntzwso2/demo-banking-agent',
                                   'commitId':'63479426'}}
        obj={'data':{'builds':[record]}}
        self.assertEqual(c.select_build(obj,self.name,sha)['buildName'],'build-1')
        obj['data']['builds'][0]['buildParameters']['commitId']='12345678'
        with self.assertRaises(c.ContractError):
            c.select_build(obj,self.name,sha)
    def test_runtime_resolver_uses_deployments_not_agent_get(self):
        with patch.object(m,'api',return_value=self.deploy), patch.object(m,'runtime_pod_ready',return_value=(True,'ready')):
            self.assertEqual(m.resolve_invoke_url(self.name),self.url)
    def test_runtime_resolver_rejects_unready_pod(self):
        with patch.object(m,'api',return_value=self.deploy), patch.object(m,'runtime_pod_ready',return_value=(False,'restart')):
            with self.assertRaisesRegex(RuntimeError,'not Ready'):
                m.resolve_invoke_url(self.name)
    def test_reconciler_deploy_merges_port_and_pins_build(self):
        sha='634794269c3f'+'e'*28
        build={'buildName':'build-1'}
        with patch.object(m,'get_git_commit',return_value=sha), patch.object(m,'latest_build',return_value=build),\
             patch.object(m,'verified_build_revision',return_value=sha), patch.object(m,'status_label',return_value='completed'),\
             patch.object(m,'state_get',return_value={}),patch.object(m,'state_save') as save,\
             patch.object(m,'cmd') as cli, patch.object(m,'wait_runtime_ready') as wait:
            m.deploy_agent(self.name,{'USE_LLM_PROVIDER':'true'})
            command=cli.call_args[0][0]
            self.assertIn('PORT=8000',command)
            self.assertIn('USE_LLM_PROVIDER=true',command)
            self.assertIn('--build-name',command)
            self.assertIn('build-1',command)
            wait.assert_called_once_with(self.name)
            save.assert_called_once()
    def test_reconciler_skips_verified_unchanged_deployment(self):
        sha='634794269c3f'+'e'*28
        build={'buildName':'build-1'}
        import hashlib,json
        desired={'PORT':'8000','USE_LLM_PROVIDER':'true'}
        digest=hashlib.sha256(json.dumps({'sha':sha,'build':'build-1','envvars':desired},sort_keys=True).encode()).hexdigest()
        with patch.object(m,'get_git_commit',return_value=sha),patch.object(m,'latest_build',return_value=build),\
             patch.object(m,'verified_build_revision',return_value=sha),patch.object(m,'status_label',return_value='completed'),\
             patch.object(m,'state_get',return_value={'runtime_deployments':{self.name:digest}}),\
             patch.object(m,'runtime_ready',return_value=(True,'Ready')),patch.object(m,'cmd') as cli:
            m.deploy_agent(self.name,{'USE_LLM_PROVIDER':'true'})
            cli.assert_not_called()

if __name__=='__main__':unittest.main()
