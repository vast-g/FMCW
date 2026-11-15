"""Complete-turn integration tests; no model calls, MySQL or running API."""
import os
os.environ['SLOPE_HEADLESS'] = '1'
import threading
import unittest
from queue import Queue
from types import MethodType
from unittest.mock import Mock, patch
from langchain_core.runnables import RunnableLambda
import end_new as E
from session_store import MemorySessionStore, SessionBusyError, SessionStoreUnavailable


def make_agent(store=None, body=None):
    agent = E.SimpleAgent.__new__(E.SimpleAgent)
    agent._histories = {}
    agent._current_session = 'default'
    agent._session_store = store if store is not None else MemorySessionStore()
    agent._on_history_evict = Mock()
    if body:
        agent._run_once = MethodType(body, agent)
    return agent


class AgentMemoryTurnsTest(unittest.TestCase):
    def test_only_complete_pairs_and_full_answer_are_committed(self):
        def answer(agent, question, session_id=None):
            agent.history.append(('用户', question))
            agent.history.append(('Tool', 'internal observation'))
            agent.history.append(('AI', 'truncated intermediate'))
            return '完整回答' * 100
        agent = make_agent(body=answer)
        for n in range(8):
            agent.run(f'question{n}', session_id='s')
        rows = agent._session_store.load('s')
        self.assertEqual(len(rows), 10)
        self.assertEqual([r for r, _ in rows], ['用户', 'AI'] * 5)
        self.assertEqual(rows[0], ('用户', 'question3'))
        self.assertEqual(rows[-1][1], '完整回答' * 100)
        self.assertEqual(agent._session_store.count_turns('s'), 8)
        evicted = [item for call in agent._on_history_evict.call_args_list for item in call.args[1]]
        self.assertEqual(len(evicted), 6)
        self.assertEqual(evicted[0], ('用户', 'question0'))

    def test_pending_turn_is_invisible_and_same_session_other_instance_is_busy(self):
        store = MemorySessionStore()
        entered, release = threading.Event(), threading.Event()
        errors = Queue()
        def answer(agent, question, session_id=None):
            agent.history.append(('用户', question))
            entered.set()
            if not release.wait(3):
                raise AssertionError('test release timed out')
            return 'answer'
        first = make_agent(store, answer)
        second = make_agent(store, lambda *args, **kwargs: 'second')
        def work():
            try: first.run('question', 's')
            except BaseException as exc: errors.put(exc)
        thread = threading.Thread(target=work)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(store.load('s'), [])
            with self.assertRaises(SessionBusyError): second.run('overlap', 's')
            self.assertEqual(second.run('independent', 'other'), 'second')
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        if not errors.empty(): raise errors.get()
        self.assertEqual(store.load('s'), [('用户','question'),('AI','answer')])
        self.assertEqual(second.run('next', 's'), 'second')

    def test_alternating_instances_reload_latest_history(self):
        store = MemorySessionStore()
        seen = []
        def answer(agent, question, session_id=None):
            seen.append([(m.type, m.content) for m in agent._prompt_history()])
            agent.history.append(('用户', question))
            return 'reply:' + question
        first, second = make_agent(store, answer), make_agent(store, answer)
        first.run('one','s')
        second.run('two','s')
        first.run('three','s')
        self.assertEqual(seen[0], [])
        self.assertEqual(seen[1], [('human','one'),('ai','reply:one')])
        self.assertEqual(seen[2][-2:], [('human','two'),('ai','reply:two')])
        self.assertEqual(store.count_turns('s'),3)

    def test_model_failure_preserves_backend_and_does_not_trigger_summary(self):
        store = MemorySessionStore()
        store.save('s', [('用户',str(i)) if i%2==0 else ('AI',str(i)) for i in range(10)])
        before=store.load('s')
        def fail(agent, question, session_id=None):
            agent.history.append(('用户',question))
            raise ValueError('model failure')
        agent=make_agent(store,fail)
        with self.assertRaises(ValueError): agent.run('bad','s')
        self.assertEqual(store.load('s'),before)
        agent._on_history_evict.assert_not_called()
        self.assertNotIn('s',agent._histories)
        self.assertFalse(agent._turn_active)
        agent._run_once=Mock(return_value='recovered')
        self.assertEqual(agent.run('good','s'),'recovered')

    def test_failed_commit_is_not_a_success_and_has_no_eviction_callback(self):
        store=MemorySessionStore()
        original=store.commit_turn
        store.commit_turn=Mock(side_effect=SessionStoreUnavailable('write failed'))
        agent=make_agent(store,lambda *a,**k:'generated')
        with self.assertRaises(SessionStoreUnavailable): agent.run('q','s')
        self.assertEqual(store.load('s'),[])
        agent._on_history_evict.assert_not_called()
        store.commit_turn=original
        agent.run('q2','s')
        self.assertEqual(store.count_turns('s'),1)

    def test_read_failure_never_calls_model_with_empty_history(self):
        store=MemorySessionStore()
        store.load=Mock(side_effect=SessionStoreUnavailable('read failed'))
        body=Mock(return_value='must not run')
        agent=make_agent(store)
        agent._run_once=body
        with self.assertRaises(SessionStoreUnavailable): agent.run('q','s')
        body.assert_not_called()
        with store.session_guard('s'): pass

    def test_eviction_callback_runs_only_after_complete_commit(self):
        store=MemorySessionStore()
        agent=make_agent(store,lambda *a,**k:'answer')
        for i in range(5): agent.run(str(i),'s')
        seen=[]
        agent._on_history_evict=lambda sid,rows: seen.append((store.count_turns(sid),store.load(sid),rows))
        agent.run('six','s')
        self.assertEqual(seen[0][0],6)
        self.assertEqual(seen[0][1][-2:],[('用户','six'),('AI','answer')])
        self.assertEqual(seen[0][2],[('用户','0'),('AI','answer')])

    def test_existing_fast_route_uses_new_complete_turn_commit(self):
        agent=make_agent()
        agent.stats={'total_queries':0}
        agent._needs_comprehensive=Mock(return_value=False)
        agent._is_knowledge_question=Mock(return_value=False)
        agent._split_complex_query=Mock(return_value=[])
        agent._execute_multi_intent=Mock(return_value='distance result')
        agent._save_to_conversation=Mock()
        with patch('end_new.TraceRecorder'):
            self.assertEqual(agent.run('distance?','s'),'distance result')
        self.assertEqual(agent._session_store.load('s'),[('用户','distance?'),('AI','distance result')])
        self.assertEqual(agent._session_store.count_turns('s'),1)
        agent._save_to_conversation.assert_called_once_with('distance?','distance result')

    def test_fallback_chain_has_no_private_unbounded_history(self):
        agent=make_agent()
        captured=[]
        def model(prompt):
            captured.append([(m.type,m.content) for m in prompt.to_messages()])
            return 'answer'
        with patch('end_new.OllamaLLM',return_value=RunnableLambda(model)):
            agent._init_conversation_chain()
        from langchain_core.messages import HumanMessage,AIMessage
        agent.conversation_chain.invoke({'input':'now','chat_history':[HumanMessage(content='prior q'),AIMessage(content='prior a')]})
        agent.conversation_chain.invoke({'input':'fresh','chat_history':[]})
        self.assertEqual(captured[0][1:],[('human','prior q'),('ai','prior a'),('human','now')])
        self.assertEqual(captured[1][1:],[('human','fresh')])

    def test_legacy_tool_and_incomplete_items_do_not_break_pairs(self):
        self.assertEqual(E.SimpleAgent._complete_history_pairs([
            ('AI','orphan'),('用户','q'),('Tool','readout'),('AI','a'),('用户','unfinished')]),
            [('用户','q'),('AI','a')])


if __name__=='__main__': unittest.main()
