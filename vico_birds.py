"""Who visited: the cloud's bird identification per clip, and the grouping behind /api/birds.

A library item of a bird clip carries `subcategoryInfoList` with the species the cloud
recognised: common name, Latin name and a signed, short-lived link to a crop of the bird from
that clip. vico_pull.py keeps both beside the archived clip (see vico_archive.py), so the list
of visitors outlives the few days the cloud holds clips.
"""


def species_of(item: dict):
    """{"name", "latin", "image"} from a library item, or None when no bird was identified."""
    for s in item.get("subcategoryInfoList") or []:
        name = s.get("objectName") or s.get("birdStdName")
        if s.get("objectType") == "bird" and name:
            return {"name": name, "latin": s.get("birdStdName") or "", "image": s.get("url") or None}
    return None


def merge_by_trace(first: list[dict], second: list[dict]) -> list[dict]:
    """Both lists, a clip that is in both (same `trace`) only once, from `first`."""
    seen = {c.get("trace") for c in first if c.get("trace")}
    return list(first) + [c for c in second if not c.get("trace") or c["trace"] not in seen]


def group_by_species(clips: list[dict]) -> list[dict]:
    """Clips with a `species` grouped per species: name, latin, count, last visit, clips newest
    first. Species with the most clips first; clips without an identified bird are left out."""
    groups = {}
    for c in clips:
        sp = c.get("species")
        if not sp:
            continue
        key = (sp.get("latin") or sp.get("name") or "").lower()
        g = groups.setdefault(key, {"name": sp.get("name"), "latin": sp.get("latin") or "", "clips": []})
        g["clips"].append(c)
    out = []
    for g in groups.values():
        g["clips"].sort(key=lambda c: c.get("time") or 0, reverse=True)
        g["count"] = len(g["clips"])
        g["last"] = g["clips"][0].get("time") or 0
        out.append(g)
    out.sort(key=lambda g: (g["count"], g["last"]), reverse=True)
    return out
