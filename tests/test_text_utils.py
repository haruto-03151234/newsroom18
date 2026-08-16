import unittest

from scripts.news_pipeline.text_utils import (
    canonical_url,
    clean_text,
    clip_balanced_title,
    complete_text,
    has_balanced_brackets,
    looks_japanese,
    title_similarity,
)


class TextUtilsTests(unittest.TestCase):
    def test_tracking_parameters_and_fragment_are_removed(self):
        value = canonical_url("HTTPS://Example.COM/news/?id=7&utm_source=x&fbclid=y#top")
        self.assertEqual(value, "https://example.com/news?id=7")

    def test_non_web_and_private_urls_are_rejected(self):
        for value in ("javascript:alert(1)", "http://127.0.0.1/a", "https://user:pass@example.com/a"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_url(value)

    def test_html_and_control_characters_are_removed(self):
        self.assertEqual(clean_text("<script>alert(1)</script><b>本文</b>\x00"), "alert(1) 本文")

    def test_japanese_title_variants_are_similar(self):
        similarity = title_similarity("政府、新制度を発表｜新聞A", "政府が新制度を発表 - 新聞B")
        self.assertGreater(similarity, 0.78)

    def test_language_detection(self):
        self.assertTrue(looks_japanese("政府が新制度を発表しました"))
        self.assertFalse(looks_japanese("Government announces a new policy"))

    def test_overlong_unclosed_sentence_is_omitted_not_hard_clipped(self):
        value = (
            "気象庁は大雨への警戒を呼びかけました。"
            "これで「富山県気象解説情報（大雨・落雷"
            + "詳細情報" * 80
            + "。"
        )

        clipped = complete_text(value, 240)

        self.assertEqual(clipped, "気象庁は大雨への警戒を呼びかけました。")
        self.assertNotIn("これで「富山県気象解説情報（大雨・落", clipped)

    def test_midword_sentence_ending_is_omitted(self):
        value = (
            "発達した積乱雲の近づく兆しがあ。"
            "雷を伴う雨雲が近づいた場合は建物内へ移動してください。"
        )

        self.assertEqual(
            complete_text(value, 240),
            "雷を伴う雨雲が近づいた場合は建物内へ移動してください。",
        )

    def test_title_clip_marks_omission_and_closes_nested_brackets(self):
        title = "自治体が「富山県気象解説情報（大雨・落雷" + "詳細" * 90 + "）」を更新"

        clipped = clip_balanced_title(title, 120)

        self.assertLessEqual(len(clipped), 120)
        self.assertIn("…", clipped)
        self.assertTrue(has_balanced_brackets(clipped))


if __name__ == "__main__":
    unittest.main()
