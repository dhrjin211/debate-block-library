import unittest
from organize import heading_topic


class HeadingTopicTests(unittest.TestCase):
    def test_new_answer_targets_stay_distinct(self):
        self.assertEqual(heading_topic('AT: Deterrence'), 'Deterrence')
        self.assertEqual(heading_topic('AT: NATO'), 'NATO and Alliances')
        self.assertIsNone(heading_topic('AT: TPNW'))  # topic-specific: named by the heading itself
        self.assertEqual(heading_topic('AT: Deterrence Good---2AC'), 'Deterrence')
        self.assertEqual(heading_topic('A2 South Korea'), 'Korea and Japan')
        self.assertEqual(heading_topic('AT: Prolif---2AC'), 'Proliferation')
        self.assertEqual(heading_topic('AT: Coercion---2AC'), 'Deterrence')


    def test_typos_join_known_categories_and_filler_is_ignored(self):
        from organize import heading_label
        self.assertEqual(heading_topic('AT: Detterence'), 'Deterrence')
        self.assertIsNone(heading_topic('AT Relations ADV'))
        self.assertEqual(heading_label('AT Relations ADV'), 'Relations')
        for filler in ('AT: Aff', 'AT Cards', 'AT: Link', 'AT OFF'):
            self.assertEqual(heading_label(filler), '')

    def test_no_built_in_category_is_tied_to_one_topic(self):
        from organize import heading_label
        for heading in ('AT: TPNW', 'AT: Golden Dome', 'AT: Sudan', 'AT: Victim Assistance'):
            self.assertIsNone(heading_topic(heading))
            self.assertTrue(heading_label(heading))
        self.assertEqual(heading_topic('AT: Tariffs'), 'Trade')
        self.assertEqual(heading_topic('AT: Iran'), 'Middle East')


class TopicAwareSortingTests(unittest.TestCase):
    card = dict(tag='Military readiness collapses without arms sales', body='military troops readiness',
                read='', cite='Smith 24', arg_heading='', block_heading='')

    def test_the_resolution_subject_does_not_swallow_headed_cards(self):
        from organize import classify, topic_categories
        topic = 'Resolved: The United States should substantially reduce its arms sales'
        self.assertEqual(topic_categories(topic), {'Military and Security'})
        headed = dict(self.card, arg_heading='AT: Readiness Collapse')
        self.assertEqual(classify(headed, topic)[0], 'Readiness Collapse')
        self.assertEqual(classify(headed, '')[0], 'Military and Security')

    def test_cards_without_headings_still_get_the_broad_category(self):
        from organize import classify
        self.assertEqual(classify(self.card, 'reduce its arms sales')[0], 'Military and Security')


class SharedHeadingTests(unittest.TestCase):
    def test_spellings_of_one_heading_become_one_category(self):
        import dashboard
        rows = [dict(arg=a, source=s, topic_reason='Answer heading from the source') for a, s in
                [('Victim Assistance', 'A'), ('Victim Assitance', 'B'), ('victim assistances', 'C'), ('Action', 'A')]]
        dashboard._keep_shared_heading_categories(rows)
        self.assertEqual([r['arg'] for r in rows], ['Victim Assistance'] * 3 + ['Unsorted'])


if __name__ == '__main__':
    unittest.main()
