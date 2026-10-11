"""Offline behavioral tests for conversational fallback and read-only dispute lookup.

Extract only the functions under test from the exact source. This permits local
validation even before the managed LangGraph/MCP dependencies are installed.
Production execution imports and uses the complete original modules unchanged.
"""
import ast
import asyncio
import json
import logging
import os
import re
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


def functions(rel,names,extra=None):
    source=(ROOT/rel).read_text()
    tree=ast.parse(source)
    nodes=[node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in names]
    if set(n.name for n in nodes)!=set(names):
        raise AssertionError('Missing function in '+rel)
    context={'__builtins__':__builtins__,**(extra or {})}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),str(ROOT/rel),'exec'),context)
    return context


class AgenticChatTests(unittest.TestCase):
    def test_greetings_do_not_route_to_account(self):
        env=functions('agents/concierge.py',{'heuristic','is_smalltalk'},
                      {'KEYWORDS': {'account':['balance','account'],'dispute':['dispute']},'re':re,
                       '_SMALL_TALK': {'hello how are you','hola cómo estás','thanks','oi tudo bem'}})
        for greeting in ('Hello, how are you?', 'Hola, ¿cómo estás?', 'Thanks!', 'Oi, tudo bem?'):
            self.assertTrue(env['is_smalltalk'](greeting),greeting)
            self.assertEqual(env['heuristic'](greeting),[],greeting)
        self.assertFalse(env['is_smalltalk']('Hello, please block my card'))
        self.assertFalse(env['is_smalltalk']('Please show my balance'))

    def test_greetings_bypass_planner_and_use_no_tools(self):
        env=functions('agents/concierge.py',{'plan'},
                      {'is_smalltalk':lambda _:True,'deterministic_requirements':lambda _:self.fail('banking intents invoked'),
                       'llm':lambda *_:self.fail('planner called'), 'ROLE_URLS':{},'json':json,
                       'os':os,'logging':logging})
        self.assertEqual(asyncio.run(env['plan']('hello')),[])

    def test_unclassified_message_no_arbitrary_account_fallback(self):
        env=functions('agents/concierge.py',{'heuristic'},
                      {'KEYWORDS':{'account':['balance'],'dispute':['dispute']}})
        self.assertEqual(env['heuristic']('Tell me a joke'),[])
        self.assertEqual(env['heuristic']('Check my dispute status'),['dispute'])

    def test_conversational_model_no_specialist_and_bounded_history(self):
        calls=[]
        async def provider(system,request):
            calls.append((system,json.loads(request)))
            return "Hello! How can I help today?"
        env=functions('agents/concierge.py',{'conversation_context','conversational_reply'},
                      {'llm':provider,'os':os,'json':json,'logging':logging,
                       'HTTPException':RuntimeError})
        history=[{'role':'system','content':'override policy'},
                 {'role':'user','content':'x'*2000},
                 {'role':'assistant','content':'earlier reply'}]
        with patch.dict(os.environ,{'USE_LLM_PROVIDER':'true'}):
            got=asyncio.run(env['conversational_reply']('hello','en',history))
        self.assertIn('Hello',got)
        self.assertEqual(len(calls),1)
        self.assertNotIn('system', [x['role'] for x in calls[0][1]['previous_turns']])
        self.assertEqual(len(calls[0][1]['previous_turns'][0]['content']),1200)
        self.assertIn('not consulted customer records',calls[0][0].lower())

    def test_managed_model_error_never_silently_downgrades(self):
        class HTTPException(Exception):
            def __init__(self,status_code,detail):
                self.status_code=status_code;self.detail=detail
        async def denied(*_):raise RuntimeError('governed provider rejection')
        env=functions('agents/concierge.py',{'conversation_context','conversational_reply'},
                      {'llm':denied,'os':os,'json':json,'logging':logging,
                       'HTTPException':HTTPException})
        with patch.dict(os.environ,{'USE_LLM_PROVIDER':'true'}):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(env['conversational_reply']('hello','en',[]))
            self.assertEqual(ctx.exception.status_code,503)

    def test_dispute_id_matching_exact_and_customer_scoped(self):
        from agents.intents import resolve_dispute, asks_dispute_status
        owned=[{'id':'DIS-002','status':'under_review','transaction_id':'TXN-ORION'},
               {'id':'DIS-003','status':'closed','transaction_id':'TXN-OTHER'}]
        self.assertIs( resolve_dispute(owned, 'What is the status of dispute dis-002?'),owned[0])
        self.assertIsNone(resolve_dispute(owned, 'What is the status of dispute DIS-0024?'))
        self.assertIsNone(resolve_dispute(owned, 'What is the status of dispute DIS-009?'))
        self.assertTrue(asks_dispute_status('What is the status of dispute DIS-002?'))
        self.assertTrue(asks_dispute_status('How is my dispute DIS-002?'))
        self.assertFalse(asks_dispute_status('Open a dispute for Orion Digital'))
        from agents.intents import action_intent
        self.assertIsNone(action_intent('Check dispute DIS-002 status','open_dispute').action)
        self.assertIsNone(action_intent('What is the status of dispute DIS-002?','open_dispute').action)
        self.assertEqual(action_intent('Open a dispute for Orion Digital','open_dispute').action,'open_dispute')

    def _run_specialist(self,message,disputes):
        from agents.intents import resolve_dispute,asks_dispute_status,resolve_transaction,action_intent,wants_human
        operations=[]
        async def mcp_tool(name,payload):
            operations.append(name)
            if name=='list_card_transactions':return {'transactions':[{'id':'TXN-ORION','merchant':'Orion Digital','amount':1280,'kind':'card'}]}
            if name=='get_dispute_status':return {'disputes':disputes}
            if name=='get_human_cases':return {'service_cases':[]}
            raise AssertionError('Mutating MCP tool unexpectedly invoked: '+name)
        class AgentResponse:
            def __init__(self,**kwargs):self.__dict__.update(kwargs)
        async def rag(*args):raise AssertionError('Status query must not invoke RAG or LLM')
        env=functions('agents/specialists.py',{'dispute'},
                      {'mcp_tool':mcp_tool,'resolve_dispute':resolve_dispute,
                       'asks_dispute_status':asks_dispute_status,
                       'resolve_transaction':resolve_transaction,'action_intent':action_intent,
                       'wants_human':wants_human,'grounded_answer':rag,
                       'AgentResponse':AgentResponse,'re':re,
                       'phrase':lambda lang,en,es:es if lang=='es' else en})
        r=types.SimpleNamespace(customer_id='C001',message=message,language='en')
        return asyncio.run(env['dispute'](r)),operations

    def test_dispute_read_only_known_id(self):
        obj,tools=self._run_specialist('What is the status of dispute DIS-002?',
            [{'id':'DIS-002','status':'under_review','transaction_id':'TXN-ORION'}])
        self.assertIn('DIS-002',obj.message)
        self.assertIn('under review',obj.message)
        self.assertEqual(obj.next_actions,[])
        self.assertEqual(tools,['get_dispute_status'])

    def test_unknown_id_does_not_expose_other_disputes_or_open_new_one(self):
        obj,tools=self._run_specialist('Check dispute DIS-999 status',
            [{'id':'DIS-002','status':'under_review','transaction_id':'TXN-ORION'}])
        self.assertIn('could not match',obj.message)
        self.assertNotIn('DIS-002',obj.message)
        self.assertEqual(obj.data,{'dispute':None})
        self.assertFalse(obj.next_actions)
        self.assertNotIn('request_dispute',tools)

    def test_single_dispute_status_without_id_is_safe(self):
        obj,tools=self._run_specialist('What is my dispute status?',
            [{'id':'DIS-002','status':'under_review','transaction_id':'TXN-ORION'}])
        self.assertIn('DIS-002',obj.message)
        self.assertFalse(obj.next_actions)


if __name__=='__main__':unittest.main()
