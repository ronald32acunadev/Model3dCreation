"""Regions are shared between the cards so they finish together, the largest on the first card."""

from retexture import share


def test_the_largest_region_goes_to_the_first_card_and_loads_balance():
    jobs = [{"name": n} for n in ("torso", "head", "arm_px", "arm_nx", "leg_px", "leg_nx")]
    sizes = [144, 238, 97, 84, 133, 164]  # the man's faces in thousands, the head counted twice
    first, second = share(jobs, sizes, ["0", "1"])
    assert first[0]["name"] == "head"
    load = lambda part: sum(sizes[jobs.index(job)] for job in part)
    assert abs(load(first) - load(second)) <= max(sizes)
    assert sorted(j["name"] for j in first + second) == sorted(j["name"] for j in jobs)


def test_one_card_gets_every_region():
    jobs = [{"name": "torso"}, {"name": "head"}]
    assert share(jobs, [1, 2], ["0"]) == [[{"name": "head"}, {"name": "torso"}]]
