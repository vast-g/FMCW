"""Opt-in Lua/lease tests against an existing Redis, using disposable keys only."""
import json
import os
import time
import unittest
from uuid import uuid4
from session_store import RedisSessionStore, SessionBusyError, SessionLeaseLostError, SessionStoreUnavailable


@unittest.skipUnless(os.getenv('SLOPE_TEST_REDIS')=='1','set SLOPE_TEST_REDIS=1 to verify real Redis')
class LiveRedisSessionTests(unittest.TestCase):
    def setUp(self):
        self.url=os.getenv('SLOPE_TEST_REDIS_URL','redis://127.0.0.1:6379/0')
        self.prefix='slope:verify:'+uuid4().hex+':'
        self.store=RedisSessionStore(self.url,ttl=60,prefix=self.prefix,lock_ttl=1)
        self.other=RedisSessionStore(self.url,ttl=60,prefix=self.prefix,lock_ttl=1)

    def tearDown(self):
        keys=[self.prefix+sid+suffix for sid in ('s','other','legacy','bad') for suffix in ('',':meta',':lock')]
        self.store._r.delete(*keys)
        self.store._r.close()
        self.other._r.close()

    def test_window_cumulative_counts_utf8_and_cross_instance_reload(self):
        for turn in range(1,9):
            store=self.store if turn%2 else self.other
            with store.session_guard('s'):
                items=store.load('s')+[('用户',f'问题{turn}'),('AI',f'回答{turn}')]
                self.assertEqual(store.commit_turn('s',items[-10:]),turn)
        self.assertEqual(self.other.count_turns('s'),8)
        rows=self.other.load('s')
        self.assertEqual(rows[0],('用户','问题4'))
        self.assertEqual(rows[-1],('AI','回答8'))
        self.assertEqual([r for r,_ in rows],['用户','AI']*5)
        history_ttl=self.store._r.pttl(self.prefix+'s')
        meta_ttl=self.store._r.pttl(self.prefix+'s:meta')
        self.assertLessEqual(abs(history_ttl-meta_ttl),50)
        self.assertGreater(history_ttl,0)

    def test_renewal_exclusion_independent_session_and_release(self):
        with self.store.session_guard('s'):
            with self.assertRaises(SessionBusyError):
                with self.other.session_guard('s'): pass
            with self.other.session_guard('other'):
                self.other.commit_turn('other',[('用户','q'),('AI','a')])
            # Longer than the original 1-second lease proves a renewal occurred.
            time.sleep(1.4)
            self.store.commit_turn('s',[('用户','续期'),('AI','成功')])
        self.assertIsNone(self.store._r.get(self.prefix+'s:lock'))
        with self.other.session_guard('s'): pass

    def test_old_owner_cannot_write_or_delete_new_owners_lock(self):
        with self.assertRaises(SessionLeaseLostError):
            with self.store.session_guard('s'):
                self.store._r.set(self.prefix+'s:lock','new-owner',ex=5)
                with self.assertRaises(SessionLeaseLostError):
                    self.store.commit_turn('s',[('用户','stale'),('AI','stale')])
        self.assertEqual(self.other._r.get(self.prefix+'s:lock'),'new-owner')
        self.assertEqual(self.other.load('s'),[])
        self.assertEqual(self.other.count_turns('s'),0)

    def test_legacy_migration_and_corrupt_data_are_not_empty_sessions(self):
        key=self.prefix+'legacy'
        self.store._r.setex(key,60,json.dumps([['用户','old q'],['AI','old a']]))
        self.assertEqual(self.store.count_turns('legacy'),1)
        with self.store.session_guard('legacy'):
            rows=self.store.load('legacy')+[('用户','new q'),('AI','new a')]
            self.assertEqual(self.store.commit_turn('legacy',rows),2)
        self.store._r.setex(self.prefix+'bad',60,'{broken json')
        with self.assertRaises(SessionStoreUnavailable): self.store.load('bad')
        with self.store.session_guard('bad'):
            with self.assertRaises(SessionStoreUnavailable):
                self.store.commit_turn('bad',[('用户','q'),('AI','a')])
        self.assertEqual(self.store._r.get(self.prefix+'bad'),'{broken json')
        self.store.delete('legacy')
        self.assertEqual(self.store.count_turns('legacy'),0)
        self.assertIsNone(self.store._r.get(key+':meta'))

if __name__=='__main__': unittest.main()
