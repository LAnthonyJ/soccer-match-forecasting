"""Regression checks for anonymous release verification of the complete matchday."""
from unittest import TestCase
from unittest.mock import patch
import json

from scripts.verify_public_round import card, compare_card, verify_round


class PublicRoundTests(TestCase):
    def setUp(self):
        self.game = {"id":"2026-27_arsenal_chelsea","home":"Arsenal","away":"Chelsea","date":"2026-11-28",
                     "season":"2026-27","round":"12","hg":None,"ag":None,"schedule_status":"verified",
                     "kickoff_utc":"2026-11-28T15:00:00Z","prediction":{"source":"preview","cutoff":"2026-11-26T15:00:00Z",
                                                                       "a":[.38,.25,.37]}}
        self.board = {"current_id":self.game["id"],"current_season":"2026-27","current_week":"第 12 轮",
                      "updated_at":"2026-11-26T15:00:00Z","batch_id":"a"*64,"public_snapshot":True,"fixtures":[self.game]}
        self.assets = {"/board.json":self.board,"/rounds/2026-27/r12.json":[self.game],
                       "/fixtures/"+self.game["id"]+".json":self.game}

    def run_audit(self):
        with patch("scripts.verify_public_round.read",side_effect=lambda _,p:json.dumps(self.assets[p]).encode()):
            return verify_round(None,"",self.board)

    def test_every_public_representation_is_compared(self):
        self.assertEqual(self.run_audit(),(self.board,0.0))
        self.assets["/fixtures/"+self.game["id"]+".json"] = dict(self.game,prediction=None)
        with self.assertRaisesRegex(ValueError,"detail"):
            self.run_audit()

    def test_one_missing_or_duplicated_fixture_blocks_release(self):
        for rows in ([],[self.game,self.game]):
            with self.subTest(count=len(rows)):
                self.assets["/rounds/2026-27/r12.json"] = rows
                with self.assertRaisesRegex(ValueError,"fixture IDs"):
                    self.run_audit()

    def test_only_machine_roundoff_in_recomputed_previews_is_tolerated(self):
        p=self.game["prediction"]
        def changed(delta,source):
            return dict(self.game,prediction={**p,"source":source,"a":[.38+delta,.25,.37-delta]})
        self.assertGreater(compare_card(changed(1e-16,"preview"),card(self.game),"preview"),0)
        with self.assertRaisesRegex(ValueError,"preview probability mismatch"):
            compare_card(changed(1e-8,"preview"),card(self.game),"preview")
        with self.assertRaisesRegex(ValueError,"prediction"):
            compare_card(changed(1e-16,"frozen"),card(changed(0,"frozen")),"frozen")
