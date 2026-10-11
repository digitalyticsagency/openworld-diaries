"""What the player is thinking about during quiet travel: family, friends, the future and more, chosen per video.

Only the quiet-travel thoughts follow the choice. Reactions to fights, shops, animals and speech stay grounded in what
happens. Every line in a theme is generic: no real character's name and no story fact, so nothing can be got wrong."""
import re

THEMES = {
    "family": {"name": "Family and home", "lines": [
        "the people he comes from, and what he owes them",
        "what it would be to have a family of his own someday",
        "missing a table, a voice calling him in, a door that is always open",
        "whether he would be a good parent, and what kind",
        "someone waiting for him to come home",
        "how a home is made of small habits, not walls"]},
    "friends": {"name": "Friends and loneliness", "lines": [
        "an old friend he has not heard from in too long",
        "who he can really trust, and how few there are",
        "the people who left, and whether he blames them",
        "how easy it is to be alone out here, and how it stops being easy",
        "making a new friend without saying so",
        "being missed by someone"]},
    "future": {"name": "Future and dreams", "lines": [
        "where his life goes next, and whether it can go anywhere new",
        "what he would do with enough money to stop",
        "a place of his own, however small",
        "a second chance, and whether he deserves one",
        "being someone different a year from now",
        "a plan that is only a feeling so far"]},
    "past": {"name": "Past and regrets", "lines": [
        "a choice he would take back, if he could",
        "who he used to be, and what became of him",
        "an apology he never made",
        "whether a man can be forgiven by himself",
        "the road behind him, and how long it is",
        "what he learned too late"]},
    "fear": {"name": "Fear and doubt", "lines": [
        "worry about not being good enough",
        "what happens if all of this goes wrong",
        "a danger he keeps not thinking about",
        "the quiet voice that says turn back",
        "how courage is mostly going on while afraid",
        "being tired in a way sleep does not fix"]},
    "gratitude": {"name": "Gratitude and small joys", "lines": [
        "a good meal and a warm place to eat it",
        "a horse or a road that has never let him down",
        "the weather, and the luck of being out in it",
        "how good it is to be alive on an ordinary day",
        "a small thing that went right today",
        "a comfort he forgot he had"]},
    "meaning": {"name": "Faith, luck and meaning", "lines": [
        "whether any of it adds up to anything",
        "luck, and who gets it",
        "what he believes when nobody is asking",
        "the size of the sky and the smallness of a man",
        "fate against choice",
        "what a good life would even look like"]},
    "work": {"name": "Work, money and survival", "lines": [
        "making an honest living, and what that would be",
        "a debt he is still carrying",
        "what a day's work is worth",
        "how little a man really needs",
        "the trade he would learn if he started over",
        "counting what he has and what he needs"]},
}
MAX_NOTE = 200


def clean_ids(ids):
    """The chosen theme ids that exist, in the catalogue's order, without repeats. Anything else is dropped."""
    got = {i for i in (ids or []) if isinstance(i, str)}
    return [k for k in THEMES if k in got]


def clean_note(text):
    """The owner's free-text note, trimmed to one short line of plain text."""
    t = re.sub(r"[<>{}\\]", "", re.sub(r"\s+", " ", str(text or ""))).strip()
    return t[:MAX_NOTE]


def catalog():
    return [{"id": k, "name": v["name"]} for k, v in THEMES.items()]


def prompts(ids, note=""):
    """[(label, line)] cycled through for the quiet-travel reflections: each chosen theme in turn, line by line."""
    ids = clean_ids(ids)
    out = []
    depth = max((len(THEMES[i]["lines"]) for i in ids), default=0)
    for n in range(depth):
        for i in ids:
            lines = THEMES[i]["lines"]
            out.append((THEMES[i]["name"], lines[n % len(lines)]))
    return out
