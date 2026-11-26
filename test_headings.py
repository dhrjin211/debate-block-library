import unittest
from organize import heading_topic


class HeadingTopicTests(unittest.TestCase):
    def test_new_answer_targets_stay_distinct(self):
        self.assertEqual(heading_topic('AT: Deterrence'), 'Nuclear Deterrence')
        self.assertEqual(heading_topic('AT: NATO'), 'NATO and Alliances')
        self.assertEqual(heading_topic('AT: TPNW'), 'Treaty Compliance')
        self.assertEqual(heading_topic('AT: Deterrence Good---2AC'), 'Nuclear Deterrence')
        self.assertEqual(heading_topic('A2 South Korea'), 'Korea and Japan')
        self.assertEqual(heading_topic('AT: Prolif---2AC'), 'Nonproliferation')
        self.assertEqual(heading_topic('AT: Coercion---2AC'), 'Nuclear Deterrence')


    def test_typos_join_known_categories_and_filler_is_ignored(self):
        from organize import heading_label
        self.assertEqual(heading_topic('AT: Detterence'), 'Nuclear Deterrence')
        self.assertIsNone(heading_topic('AT Relations ADV'))
        self.assertEqual(heading_label('AT Relations ADV'), 'Relations')
        for filler in ('AT: Aff', 'AT Cards', 'AT: Link', 'AT OFF'):
            self.assertEqual(heading_label(filler), '')


if __name__ == '__main__':
    unittest.main()
