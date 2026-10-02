import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np

HELPERS = Path(__file__).parents[1] / "helpers"
sys.path.insert(0, str(HELPERS))
SPEC = importlib.util.spec_from_file_location("video_edit_audio_env", HELPERS / "audio_env.py")
audio_env = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audio_env)


def env_from(spans, total=10.0, loud=-20.0, quiet=-55.0):
    """10 ms envelope: loud inside spans, quiet elsewhere."""
    n = int(total / audio_env.HOP_S)
    e = np.full(n, quiet)
    for a, b in spans:
        e[round(a / audio_env.HOP_S):round(b / audio_env.HOP_S)] = loud
    return e


class IslandTests(unittest.TestCase):
    def test_long_pause_splits_short_gap_merges(self):
        env = env_from([(1.0, 2.0), (2.2, 3.0), (6.0, 7.0)])
        mask, _ = audio_env.speech_mask(env, threshold=-40)
        self.assertEqual(audio_env.islands(env, mask), [(1.0, 3.0), (6.0, 7.0)])

    def test_tighten_cuts_internal_pause_keeps_air(self):
        env = env_from([(1.0, 2.0), (2.3, 3.0)])
        mask, _ = audio_env.speech_mask(env, threshold=-40)
        r = audio_env.tighten(env, mask, [(0.95, 3.05)], min_gap=0.16)
        self.assertEqual(len(r), 2)
        self.assertAlmostEqual(r[0][1], 2.04, places=2)
        self.assertAlmostEqual(r[1][0], 2.27, places=2)

    def test_tighten_ignores_short_breath(self):
        env = env_from([(1.0, 2.0), (2.1, 3.0)])
        mask, _ = audio_env.speech_mask(env, threshold=-40)
        self.assertEqual(audio_env.tighten(env, mask, [(0.95, 3.05)]), [(0.95, 3.05)])


if __name__ == "__main__":
    unittest.main()
