"""Regression tests for launcher update channels (ocm/updater.py): an install follows the newest release of its own
major version, never GitHub's "latest" release, and setup always gets the exact tag to install.

    python -m unittest discover -s tests -v

GitHub is never contacted: the release list and the setup download are synthetic."""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "ocm"))
import updater as U  # noqa: E402

SETUP = b"@echo off\r\nrem OCM_SETUP_TAG\r\n#PS-START\r\n$Repo = '" + U.UPDATE_REPO.encode() + b"'\r\n"


def gh(*rels):
    """A GitHub /releases answer: (tag, prerelease[, draft])."""
    return json.dumps([{"tag_name": t, "prerelease": p, "draft": bool(d[0]) if d else False} for t, p, *d in rels]).encode()


BEFORE_V3 = gh(("v3.0.0-rc.6", True), ("v2.3.11", False), ("v3.0.0-rc.5", True), ("v2.3.10", False))
AFTER_V3 = gh(("v2.3.12", False), ("v3.0.0", False), ("v3.0.0-rc.6", True), ("v2.3.11", False),
              ("v3.0.1", False, True), ("v3.1.0-rc.1", True), ("not-a-tag", False))


class Channels(unittest.TestCase):
    def check(self, installed, answer, preview=None):
        with mock.patch.object(U, "_get", return_value=answer), \
             mock.patch.object(U, "launcher_installed", return_value=installed), \
             mock.patch.object(U, "is_git_clone", return_value=False):
            if preview is not None:
                with mock.patch.object(U, "is_preview", return_value=preview):
                    return U.launcher_check()
            return U.launcher_check()

    def test_stable_v3_follows_v3_only(self):
        # a newer v2 release (published after v3.0.0) and the draft v3.0.1 are ignored, so are previews
        self.assertEqual(self.check("3.0.0", AFTER_V3), ("3.0.0", "3.0.0", False))
        newer = gh(("v3.0.1", False), ("v2.9.9", False), ("v3.2.0-rc.1", True))
        self.assertEqual(self.check("3.0.0", newer), ("3.0.0", "3.0.1", True))

    def test_preview_follows_previews_and_then_stable(self):
        self.assertEqual(self.check("3.0.0-rc.5", BEFORE_V3), ("3.0.0-rc.5", "3.0.0-rc.6", True))
        self.assertEqual(self.check("3.0.0-rc.6", AFTER_V3), ("3.0.0-rc.6", "3.1.0-rc.1", True))
        self.assertEqual(self.check("3.0.0-rc.6", gh(("v3.0.0", False), ("v3.0.0-rc.6", True))),
                         ("3.0.0-rc.6", "3.0.0", True))

    def test_no_v3_release_yet_means_no_update(self):
        self.assertEqual(self.check("3.0.0", gh(("v2.3.11", False))), ("3.0.0", "3.0.0", False))

    def test_back_to_stable_is_v3_or_v2_before_v3(self):
        with mock.patch.object(U, "_get", return_value=BEFORE_V3):
            self.assertEqual(U.stable_target(), "2.3.11")
        with mock.patch.object(U, "_get", return_value=AFTER_V3):
            self.assertEqual(U.stable_target(), "3.0.0")

    def test_preview_latest_and_newest(self):
        with mock.patch.object(U, "_get", return_value=AFTER_V3):
            rels = U.releases()
        self.assertNotIn(("v3.0.1", False), rels)                       # draft
        self.assertNotIn(("not-a-tag", False), rels)
        self.assertEqual(U.newest(2, rels=rels), "2.3.12")
        self.assertEqual(U.preview_latest(rels), "3.1.0-rc.1")

    def test_unreadable_answer_is_an_update_error(self):
        with mock.patch.object(U, "_get", return_value=b"{}"):
            with self.assertRaises(U.UpdateError):
                U.releases()


class Install(unittest.TestCase):
    def run_update(self, version, answer=AFTER_V3):
        with mock.patch.object(U, "_get", side_effect=lambda url, *a, **k: SETUP if url == U.SETUP_URL else answer), \
             mock.patch.object(U, "launcher_blocker", return_value=None), \
             mock.patch.object(U.subprocess, "Popen") as popen:
            U.launcher_update(out=lambda *_: None, version=version)
        return popen.call_args.kwargs["env"]

    def test_setup_always_gets_the_exact_tag(self):
        self.assertEqual(self.run_update("3.0.0")["OCM_SETUP_TAG"], "v3.0.0")
        self.assertEqual(self.run_update("3.1.0-rc.1")["OCM_SETUP_TAG"], "v3.1.0-rc.1")
        self.assertEqual(self.run_update(None)["OCM_SETUP_TAG"], "v3.0.0")   # default: newest v3, not "latest"

    def test_no_v3_release_refuses(self):
        with self.assertRaises(U.UpdateError):
            self.run_update(None, answer=gh(("v2.3.11", False)))

    def test_bad_version_refused(self):
        with self.assertRaises(U.UpdateError):
            self.run_update("3.0.0;calc")


class GitClones(unittest.TestCase):
    def test_old_testing_branch_follows_v3_testing(self):
        with mock.patch.object(U, "_git", return_value="testing"):
            self.assertEqual(U.git_target(), U.PREVIEW_BRANCH)
        with mock.patch.object(U, "_git", return_value="main"):
            self.assertEqual(U.git_target(), "main")
        with mock.patch.object(U, "_git", return_value="my-feature"):
            self.assertEqual(U.git_target(), "main")


if __name__ == "__main__":
    unittest.main()
