from app.domain import plates


def test_normalise_and_display():
    assert plates.normalise(" mh 43-ab.1234 ") == "MH43AB1234"
    assert plates.display("MH43AB1234") == "MH 43 AB 1234"
    assert plates.display("22BH1234AA") == "22 BH 1234 AA"
    assert plates.display("MH431234") == "MH 43 1234"


def test_validation_formats():
    assert plates.is_valid("MH43AB1234")
    assert plates.is_valid("MH4A1234")
    assert plates.is_valid("DL1CAB1234")
    assert plates.is_valid("22BH1234AA")
    assert not plates.is_valid("XX43AB1234")  # unknown state code
    assert not plates.is_valid("MH43AB123")
    assert plates.is_valid("XX43AB1234", ["XX"])


def test_confusion_correction_position_aware():
    assert plates.correct("MH43AB1Z34").plate == "MH43AB1234"   # Z in digit block -> 2
    assert plates.correct("MH43A81234").plate == "MH43AB1234"   # 8 in letter block -> B
    assert plates.correct("M H 4 3 A B l 2 3 4").plate == "MH43AB1234"
    c = plates.correct("0L5CAB1234")  # 0->D? no: state slot fixed to a valid code
    assert c.valid and c.plate.startswith("DL5")
    assert plates.correct("22BH1234AA").substitutions == 0
    assert plates.correct("22B41234AA").valid is False or plates.correct("22B41234AA").plate == "22BH1234AA"


def test_canonical_and_fuzzy():
    assert plates.canonical("MH43AB1234") == plates.canonical("MH43A81234")
    assert plates.fuzzy_distance("MH43AB1234", "MH43AB1Z34") == 0
    assert plates.fuzzy_distance("MH43AB1234", "MH43AB1235") == 1
    assert plates.fuzzy_distance("MH43AB1234", "MH43AB123") == 1
    assert plates.fuzzy_distance("MH43AB1234", "KA01ZZ9999", limit=1) == 2


def test_levenshtein():
    assert plates.levenshtein("kitten", "sitting") == 3
    assert plates.levenshtein("", "abc") == 3
    assert plates.levenshtein("abc", "abc") == 0
    assert plates.levenshtein("abcdef", "a", limit=1) == 2


def test_rank_and_mask():
    ranked = plates.rank_candidates("MH43AB1Z34", ["MH43AB1234", "MH43AB1235", "KA01AA0001"], 1)
    assert ranked[0] == ("MH43AB1234", 0)
    assert ("MH43AB1235", 1) in ranked
    assert plates.mask("MH43AB1234") == "MH43••••34"
