"""Tests for the normalization module."""

from app.services.matching.normalize import normalize_text


class TestAbbreviationExpansion:
    """Verify that construction abbreviations expand to catalog prose."""

    def test_concrete_ready_mix(self):
        assert "ready-mix concrete" in normalize_text("CONC RM 30MPA")

    def test_concrete_ready_mix_double_space(self):
        """Multi-space between CONC and RM should still expand."""
        assert "ready-mix concrete" in normalize_text("CONC  RM 30MPA")

    def test_batt_insulation_mineral_wool(self):
        result = normalize_text("BATT INSUL MW R-22")
        assert "batt insulation" in result
        assert "mineral wool" in result

    def test_batt_insulation_fibreglass(self):
        result = normalize_text("BATT INSUL FG R-28")
        assert "batt insulation" in result
        assert "fibreglass" in result

    def test_steel_wide_flange_beam(self):
        result = normalize_text("STL BM W360X57")
        assert "steel wide flange beam" in result

    def test_steel_hss(self):
        result = normalize_text("STL HSS 6X6X1/4")
        assert "steel hss" in result

    def test_steel_channel(self):
        result = normalize_text("STL CHAN C310X31")
        assert "steel channel" in result

    def test_gypsum_board(self):
        result = normalize_text("GYP BD 5/8in TYPE X")
        assert "gypsum board" in result

    def test_plywood(self):
        result = normalize_text("PLYWD 18.5MM DFIR")
        assert "plywood sheathing" in result
        assert "douglas fir" in result

    def test_lumber(self):
        result = normalize_text("LBR 38X140MM SPF NO.1/BTR PT")
        assert "dimensional lumber" in result
        assert "pressure treated" in result

    def test_pvc_conduit(self):
        result = normalize_text("PVC  CONDUIT 53MM")
        assert "rigid pvc conduit" in result

    def test_copper_conductor(self):
        result = normalize_text("COPPER CONDUCTOR T90 NYLON #4/0 AWG")
        assert "copper conductor" in result

    def test_cu_rw90(self):
        result = normalize_text("CU RW90 #1/0 AWG")
        assert "copper conductor rw90" in result

    def test_emt(self):
        result = normalize_text("EMT 41MM")
        assert "emt conduit" in result

    def test_rebar(self):
        result = normalize_text("REBAR 15M GR400 EPOXY")
        assert "reinforcing steel bar" in result

    def test_paint_interior_latex(self):
        result = normalize_text("PNT INT LTX 2 COATS SEMI-GLOSS")
        assert "paint interior latex" in result

    def test_asphalt_paving(self):
        result = normalize_text("ASPH PVG HL-3 50MM")
        assert "asphalt" in result
        assert "paving" in result

    def test_cable_tray(self):
        result = normalize_text("CABLE  TRAY ALUMINUM LADDER 600MM")
        assert "cable tray" in result

    def test_stone_veneer(self):
        result = normalize_text("STONE  VENEER NATURAL GRANITE 30MM")
        assert "stone veneer" in result


class TestWhitespace:
    """Verify multi-space collapsing."""

    def test_double_space_collapsed(self):
        result = normalize_text("BATT  INSUL MW R-22")
        assert "  " not in result

    def test_leading_trailing_stripped(self):
        result = normalize_text("  CONC RM 30MPA  ")
        assert not result.startswith(" ")
        assert not result.endswith(" ")

    def test_multiple_spaces(self):
        result = normalize_text("A   B    C")
        assert "   " not in result
        assert "  " not in result


class TestCaseNormalization:
    """Everything should be lowercased."""

    def test_all_upper(self):
        assert normalize_text("CONC RM 30MPA") == normalize_text("conc rm 30mpa")

    def test_mixed_case(self):
        result = normalize_text("Conc Rm 30MPa")
        assert result == result.lower()


class TestShortcutNormalization:
    """W/ → with and similar shortcuts."""

    def test_w_slash_becomes_with(self):
        result = normalize_text("CONC RM 30MPA W/ 25% FA")
        assert "with" in result
        assert "w/" not in result

    def test_w_slash_case_insensitive(self):
        result = normalize_text("w/ something")
        assert "with" in result


class TestUnitNormalization:
    """Units glued to numbers should get a space inserted."""

    def test_mm_suffix(self):
        result = normalize_text("50MM")
        assert "50 mm" in result

    def test_in_suffix(self):
        # 5/8in → 5/8 in
        result = normalize_text("5/8in")
        assert "5/8 in" in result

    def test_mpa_suffix(self):
        result = normalize_text("30MPa")
        assert "30 mpa" in result

    def test_mpa_uppercase(self):
        result = normalize_text("30MPA")
        assert "30 mpa" in result


class TestGradeNormalization:
    """GR B / GR C → grade b / grade c."""

    def test_gr_b(self):
        result = normalize_text("STL HSS 6X6X3/8 GR B")
        assert "grade b" in result

    def test_gr400_expansion(self):
        result = normalize_text("REBAR 15M GR400")
        assert "grade 400w" in result


class TestIdempotency:
    """Already-normalized text should pass through unchanged."""

    def test_catalog_text_unchanged(self):
        catalog = "ready-mix concrete, 30 mpa, 25% fly ash"
        assert normalize_text(catalog) == catalog

    def test_empty_string(self):
        assert normalize_text("") == ""

    def test_whitespace_only(self):
        assert normalize_text("   ") == ""


class TestRealFixturePairs:
    """Spot-check that known source→catalog pairs normalize similarly."""

    def test_src_0054_converges(self):
        """SRC-0054 (CONC RM 30MPa w/ 25% FA) should converge toward
        CAT-0015 (Ready-mix concrete, 30 MPa, 25% fly ash)."""
        src = normalize_text("CONC RM 30MPa w/ 25% FA")
        cat = normalize_text("Ready-mix concrete, 30 MPa, 25% fly ash")
        # After normalization both should contain these key fragments.
        for fragment in ["ready-mix concrete", "30 mpa", "fly ash"]:
            assert fragment in src, f"{fragment!r} not in normalized source: {src!r}"
            assert fragment in cat, f"{fragment!r} not in normalized catalog: {cat!r}"

    def test_src_0001_converges(self):
        """SRC-0001 (BATT  INSUL MW R-22) → CAT-0186 (Batt insulation, mineral wool, R-22)."""
        src = normalize_text("BATT  INSUL MW R-22")
        cat = normalize_text("Batt insulation, mineral wool, R-22")
        for fragment in ["batt insulation", "mineral wool", "r-22"]:
            assert fragment in src, f"{fragment!r} not in source: {src!r}"
        for fragment in ["batt insulation", "mineral wool", "r-22"]:
            assert fragment in cat, f"{fragment!r} not in catalog: {cat!r}"
