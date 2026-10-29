"""vico_birds: species parsing and the per-species grouping."""
import unittest

from vico_birds import group_by_species, merge_by_trace, species_of

TIT = {"objectType": "bird", "objectName": "Great Tit", "birdStdName": "Parus major", "url": "https://x/crop1"}


class SpeciesOfTest(unittest.TestCase):
    def test_bird_entry_gives_name_latin_and_image(self):
        self.assertEqual(species_of({"subcategoryInfoList": [TIT]}),
                         {"name": "Great Tit", "latin": "Parus major", "image": "https://x/crop1"})

    def test_non_bird_entries_are_ignored(self):
        item = {"subcategoryInfoList": [{"objectType": "pet", "objectName": "cat"}, TIT]}
        self.assertEqual(species_of(item)["name"], "Great Tit")

    def test_no_identification_is_none(self):
        self.assertIsNone(species_of({"tags": "bird"}))
        self.assertIsNone(species_of({"subcategoryInfoList": []}))
        self.assertIsNone(species_of({"subcategoryInfoList": [{"objectType": "bird", "objectName": ""}]}))


class GroupTest(unittest.TestCase):
    def test_merge_keeps_the_first_copy_of_a_clip(self):
        a = [{"trace": "t1", "file": "a.mp4"}]
        b = [{"trace": "t1", "video": "hls"}, {"trace": "t2", "video": "hls2"}, {"video": "no-trace"}]
        self.assertEqual(merge_by_trace(a, b), [a[0], b[1], b[2]])

    def test_groups_by_species_most_clips_first_newest_clip_first(self):
        tit = {"name": "Great Tit", "latin": "Parus major", "image": None}
        sparrow = {"name": "Eurasian Tree Sparrow", "latin": "Passer montanus", "image": None}
        clips = [{"trace": "1", "time": 10, "species": sparrow},
                 {"trace": "2", "time": 20, "species": tit},
                 {"trace": "3", "time": 30, "species": None},
                 {"trace": "4", "time": 40, "species": tit},
                 {"trace": "5", "time": 50}]
        groups = group_by_species(clips)
        self.assertEqual([(g["name"], g["latin"], g["count"], g["last"]) for g in groups],
                         [("Great Tit", "Parus major", 2, 40), ("Eurasian Tree Sparrow", "Passer montanus", 1, 10)])
        self.assertEqual([c["trace"] for c in groups[0]["clips"]], ["4", "2"])

    def test_same_latin_name_is_one_species(self):
        clips = [{"trace": "1", "time": 1, "species": {"name": "Great Tit", "latin": "Parus major"}},
                 {"trace": "2", "time": 2, "species": {"name": "Kohlmeise", "latin": "parus major"}}]
        self.assertEqual([g["count"] for g in group_by_species(clips)], [2])


if __name__ == "__main__":
    unittest.main()
