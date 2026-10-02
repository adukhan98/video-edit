import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HELPERS = Path(__file__).parents[1] / "helpers"
sys.path.insert(0, str(HELPERS))
SPEC = importlib.util.spec_from_file_location("video_edit_captions", HELPERS / "captions.py")
captions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(captions)


def w(text, start, end):
    return {"type": "word", "text": text, "start": start, "end": end}


class ColorAndTimeTests(unittest.TestCase):
    def test_hex_to_ass(self):
        self.assertEqual(captions.ass_color("#4DA8FF"), "&H00FFA84D")
        self.assertEqual(captions.ass_color("#fff"), "&H00FFFFFF")
        self.assertEqual(captions.ass_color("#000000", 0.6), "&H99000000")
        self.assertEqual(captions.ass_inline_color("#0071F3"), "&HF37100&")

    def test_css_alpha_is_opacity(self):
        self.assertEqual(captions.ass_color("#FF000080"), "&H7F0000FF")

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            captions.ass_color("blue")

    def test_ass_time(self):
        self.assertEqual(captions.ass_time(0), "0:00:00.00")
        self.assertEqual(captions.ass_time(83.456), "0:01:23.46")
        self.assertEqual(captions.ass_time(3725.0), "1:02:05.00")


class ChunkTests(unittest.TestCase):
    def test_max_chars_breaks_before_overflow(self):
        words = [w("Distribution", 0, 0.5), w("matters", 0.52, 0.8), w("more", 0.82, 1.0)]
        chunks = captions.chunk_words(words, words_per_cue=3, max_words=3, max_chars=18)
        self.assertEqual([" ".join(x["text"] for x in c) for c in chunks], ["Distribution", "matters more"])

    def test_defaults_match_render_legacy(self):
        words = [w("a", 0, 0.1), w("b", 0.11, 0.2), w("c", 0.21, 0.3)]
        self.assertEqual(len(captions.chunk_words(words)), 1)   # 3-word cap, too fast for 2


class TransformTests(unittest.TestCase):
    def style(self, **kw):
        return captions.resolve_style(kw)

    def test_fixes_keep_punctuation(self):
        self.assertEqual(captions.transform_word("Mehta,", self.style(fixes={"Mehta": "Meta"}, strip_punct=False)), "Meta,")

    def test_censor(self):
        self.assertEqual(captions.transform_word("shits", self.style(censor=["shits"])), "s***s")

    def test_strip_and_case(self):
        st = self.style(case="upper")
        self.assertEqual(captions.transform_word("wasted.", st), "WASTED")
        self.assertEqual(captions.transform_word("why?", st), "WHY?")
        self.assertEqual(captions.transform_word("3.5", st), "3.5")

    def test_braces_cannot_inject_tags(self):
        self.assertNotIn("{", captions.transform_word("{\\b1}x", self.style()))


class OutputTimelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.edit = Path(self.tmp.name)
        (self.edit / "transcripts").mkdir()
        (self.edit / "transcripts" / "A.json").write_text(json.dumps({"words": [
            w("one", 1.0, 1.3), w("two", 1.4, 1.7), w("three", 5.0, 5.4)]}))
        self.edl = {"sources": {"A": "A.mp4"}, "ranges": [
            {"source": "A", "start": 0.9, "end": 1.8}, {"source": "A", "start": 4.9, "end": 5.6}]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_nominal_offsets(self):
        words = captions.output_words(self.edl, self.edit)
        self.assertEqual([x["text"] for x in words], ["one", "two", "three"])
        self.assertAlmostEqual(words[2]["start"], 0.9 + 0.1, places=3)

    def test_measured_timeline_wins(self):
        (self.edit / "timeline.json").write_text(json.dumps({"segments": [
            {"source": "A", "start": 0.9, "end": 1.8, "out_start": 0.0},
            {"source": "A", "start": 4.9, "end": 5.6, "out_start": 0.9333}]}))
        words = captions.output_words(self.edl, self.edit)
        self.assertAlmostEqual(words[2]["start"], 0.9333 + 0.1, places=3)

    def test_stale_timeline_ignored(self):
        (self.edit / "timeline.json").write_text(json.dumps({"segments": [
            {"source": "A", "start": 0.0, "end": 1.0, "out_start": 0.0}]}))
        self.assertEqual(captions.segment_offsets(self.edl, self.edit), [0.0, 0.9])

    def test_word_cut_by_padding_gets_no_caption(self):
        edl = {"sources": {"A": "A.mp4"}, "ranges": [{"source": "A", "start": 1.25, "end": 1.8}]}
        self.assertEqual([x["text"] for x in captions.output_words(edl, self.edit)], ["two"])


class WriteAssTests(unittest.TestCase):
    def test_highlight_events_and_windows(self):
        chunks = [[w("hello", 0.0, 0.4), w("world", 0.45, 0.9)]]
        style = captions.resolve_style({"highlight": "#FF0000", "windows": [{"start": 0.3, "end": 2, "y": 1000}]})
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "c.ass"
            captions.write_ass(chunks, style, 1080, 1920, out)
            text = out.read_text()
        self.assertIn("PlayResX: 1080", text)
        events = [l for l in text.splitlines() if l.startswith("Dialogue:")]
        self.assertEqual(len(events), 3)             # word 1 split by the window, then word 2
        self.assertIn("\\pos(540,1360)", events[0])   # vertical default caption line
        self.assertIn("\\pos(540,1000)", events[1])
        self.assertIn("\\c&H0000FF&}world", events[2])
        self.assertIn("\\fscx108", events[0])
        self.assertNotIn("\\fscx108", events[1])

    def test_split_overlays_move_captions_to_seam(self):
        edl = {"overlays": [{"layout": "split", "start_in_output": 5, "duration": 3}]}
        self.assertEqual(captions.split_windows(edl, 1920, []), [{"start": 5.0, "end": 8.0, "y": 1037}])
        self.assertEqual(captions.split_windows(edl, 1920, [{"start": 4, "end": 9, "y": 900}]), [])


class FilterQuoteTests(unittest.TestCase):
    def test_paths_with_spaces_and_colons(self):
        q = captions.ff_quote("/Users/x/Application Support/a:b.ass")
        self.assertEqual(q, "'/Users/x/Application Support/a\\:b.ass'")


if __name__ == "__main__":
    unittest.main()
