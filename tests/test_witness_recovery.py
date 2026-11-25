import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import requests

from epl_agent.evidence import publish
from epl_agent.market import canonical, database, digest, freeze_protocol, read_value


class WitnessRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = temp.name
        self.now = datetime(2027, 1, 1, 12, tzinfo=timezone.utc)
        self.issue = "https://github.com/example/soccer/issues/42"
        self.client = Mock()
        self.comments = []
        self.other = {"user":{"id":99}, "body":"third party", "created_at":self.now.isoformat(),
                      "updated_at":self.now.isoformat(), "html_url":self.issue+"#issuecomment-6"}
        self.client.get.side_effect = self.get
        self.client.post.side_effect = self.post
        self.env = patch.dict(os.environ, {"EPL_EVIDENCE_REPO":"example/soccer",
                          "EPL_EVIDENCE_ISSUE":"42", "EPL_GITHUB_TOKEN":"test"})
        self.env.start()
        self.addCleanup(self.env.stop)
        with database(self.root) as db:
            reg = freeze_protocol(db, "A1", self.now-timedelta(days=5))
            p = {"id":"game1", "season":"2026-27", "kickoff":(self.now+timedelta(days=1,minutes=1)).isoformat(),
                 "cutoff":(self.now+timedelta(minutes=1)).isoformat(), "frozen":self.now.isoformat(),
                 "a":[.5,.3,.2], "market":None,"b":None,"model_version":"A1","protocol_hash":digest(reg)}
            db.execute("INSERT INTO captures (fixture,kickoff,revision,hash,payload) VALUES(?,?,1,?,?)",
                       (p["id"],p["kickoff"],digest(p),canonical(p)))

    def get(self, url, **kwargs):
        if url.endswith("/user"):
            result = {"id":7}
        elif url.endswith("/comments"):
            result = [self.other, *self.comments]
        else:
            result = {"private":False}
        return Mock(json=lambda:result)

    def post(self, url, *, headers, json, timeout):
        stamp = self.now.isoformat()
        comment = {"user":{"id":7}, "html_url":self.issue+"#issuecomment-"+str(10+len(self.comments)),
                   "created_at":stamp, "updated_at":stamp, "body":json["body"]}
        self.comments.append(comment)
        return Mock(json=lambda:comment)

    def publish(self):
        return publish(self.root, session=self.client, clock=lambda:self.now)

    def test_response_lost_after_publication_recovers_original_receipt(self):
        def timeout(*args, **kwargs):
            self.post(*args, **kwargs)
            self.other["body"] = self.comments[0]["body"]
            # The writer lock is available while GitHub is processing the request.
            with database(self.root) as db:
                db.execute("BEGIN IMMEDIATE")
            raise requests.Timeout("response lost")
        self.client.post.side_effect = timeout
        with self.assertRaises(requests.Timeout):
            self.publish()
        original_time = self.now.isoformat()
        self.now += timedelta(minutes=15)
        self.assertEqual(self.publish()["at"], original_time)
        self.client.post.assert_called_once()
        with database(self.root) as db:
            self.assertEqual(read_value(db,"registration_evidence")["url"],self.comments[0]["html_url"])
            self.assertIsNone(read_value(db,"witness_outbox"))
            self.assertEqual(db.execute("SELECT witnessed FROM captures").fetchone()[0], original_time)

    def test_retry_after_no_remote_receipt_keeps_actual_late_time(self):
        self.client.post.side_effect = requests.Timeout()
        with self.assertRaises(requests.Timeout):
            self.publish()
        self.now += timedelta(minutes=5)
        self.client.post.side_effect = self.post
        self.assertEqual(self.publish()["published"],1)
        with database(self.root) as db:
            row = db.execute("SELECT payload,witnessed FROM captures").fetchone()
            self.assertGreater(row["witnessed"], json.loads(row["payload"])["cutoff"])

    def test_second_publisher_skips_inflight_claim(self):
        def nested(*args, **kwargs):
            self.assertEqual(self.publish()["published"],0)
            return self.post(*args, **kwargs)
        self.client.post.side_effect = nested
        self.assertEqual(self.publish()["published"],1)
        self.assertEqual(self.client.post.call_count,1)

    def test_expired_recovery_lease_cannot_post_after_replacement_finishes(self):
        self.client.post.side_effect = requests.Timeout()
        with self.assertRaises(requests.Timeout):
            self.publish()
        self.client.post.side_effect = self.post
        def slow_inventory(url, **kwargs):
            if url.endswith("/comments"):
                self.client.get.side_effect = self.get
                self.now += timedelta(seconds=61)
                self.assertEqual(self.publish()["published"],1)
                return Mock(json=lambda:[])  # response made before the replacement's POST
            return self.get(url,**kwargs)
        self.client.get.side_effect = slow_inventory
        self.assertEqual(self.publish()["status"],"claim_changed")
        self.assertEqual(len(self.comments),1)
