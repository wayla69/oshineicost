"""The Grand Menu changeover, pinned: from 1 Sep 2026 only the new prices exist.

The old price list (350 / 590 / 649 / 689 / 799 / 999, quoted BEFORE the 5%
service charge and 7% VAT) was withdrawn on 2026-09-01 and replaced by the Grand
Menu's NET prices (459 / 629 / 789 / 999 / 1299, both charges already inside).
The management report §11 already read the era off the day it was reporting;
these tests pin the same rule for every other section that names a buffet price,
so no report can go on offering — or worse, quietly relabelling a cover as — a
price that is not sold any more.
"""
import datetime as dt

import pytest

import rules


# ─── the rule itself ─────────────────────────────────────────────────────────

def test_price_list_switches_on_1_sep_2026():
    assert rules.price_tiers("2026-08-31") == rules.PRICE_TIERS_LEGACY
    assert rules.price_tiers("2026-09-01") == rules.PRICE_TIERS_GRAND
    assert rules.price_tiers(dt.date(2026, 9, 1)) == rules.PRICE_TIERS_GRAND
    assert rules.price_tiers("2026-09") == rules.PRICE_TIERS_GRAND      # month grain
    assert rules.price_tiers("2026-08") == rules.PRICE_TIERS_LEGACY


def test_a_september_window_offers_no_retired_price():
    codes = rules.tier_codes_between("2026-09-01", "2026-09-30")
    assert codes == ("1299", "999", "789", "629", "459", "promo", "child")
    for retired in ("350", "590", "649", "689", "799"):
        assert retired not in codes


def test_a_window_that_straddles_the_change_carries_both():
    # August AND September were both sold in this window, so both lists belong.
    codes = rules.tier_codes_between("2026-08-01", "2026-09-30")
    assert "590" in codes and "789" in codes


def test_an_august_window_carries_only_the_old_list():
    codes = rules.tier_codes_between("2026-06-01", "2026-08-31")
    assert "590" in codes and "789" not in codes and "459" not in codes


def test_net_and_base_are_the_same_price_on_two_bases():
    # ฿789 on the Grand Menu is what the guest pays; the POS stores 789/1.05/1.07.
    assert round(rules.net_to_base(789), 2) == 702.27
    assert round(rules.base_to_net(702.27), 2) == pytest.approx(789.0, abs=0.01)


def test_a_september_cover_at_an_old_price_is_a_discount_not_a_tier():
    # ฿590 in September is 6% off ฿629 — naming it "tier 590" resurrects a price
    # that is not on the menu.
    assert rules.tier_of_price(590, "2026-09-05") == "promo"
    assert rules.tier_of_price(590, "2026-08-05") == "590"
    assert rules.tier_of_price(789, "2026-09-05") == "789"
    assert rules.tier_of_price(799, "2026-09-05") == "promo"


def test_upsell_pair_follows_the_menu():
    assert rules.upsell_pair("2026-08-01") == ("590", "799")
    assert rules.upsell_pair("2026-09-01") == ("629", "789")


def test_tier_label_prints_the_menu_number():
    assert rules.tier_label("789") == "฿789"
    assert rules.tier_label("1299") == "฿1,299"
    assert rules.tier_label("promo") == rules.TIER_LABEL_TH["promo"]
    assert rules.tier_label("promo", th=False) == rules.TIER_LABEL_EN["promo"]


# ─── the fact builder reads the rule, it does not keep its own copy ──────────

def test_build_menu_fact_shares_one_price_list():
    import build_menu_fact as bmf
    assert bmf.PRICE_TIERS == rules.PRICE_TIERS_LEGACY
    assert bmf.PRICE_TIERS_NEW == rules.PRICE_TIERS_GRAND
    assert bmf.NEW_MENU_FROM == rules.GRAND_MENU_FROM


# ─── the tier picker on the 🧪 tab ───────────────────────────────────────────

def test_menu_impact_tier_options_are_window_scoped(client):
    r = client.get("/api/reports/menu-impact/tiers",
                   params={"date_from": "2026-09-01", "date_to": "2026-09-30"})
    assert r.status_code == 200
    body = r.json()
    assert body["grand_menu_from"] == "2026-09-01"
    offered = [t["tier"] for t in body["tiers"]]
    assert offered == ["1299", "999", "789", "629", "459", "promo"]
    assert body["tiers"][0]["label"] == "฿1,299"

    r2 = client.get("/api/reports/menu-impact/tiers",
                    params={"date_from": "2026-06-01", "date_to": "2026-08-31"})
    offered2 = [t["tier"] for t in r2.json()["tiers"]]
    assert "590" in offered2 and "789" not in offered2


def test_menu_impact_rejects_a_tier_the_window_never_sold(client):
    # Asking for the retired ฿590 over a September window must not filter on it;
    # the endpoint falls back to "all" rather than reporting an empty comparison.
    from routers import reports
    assert "590" not in reports._mi_tiers_for("2026-09-01", "2026-09-30")
    assert "590" in reports._mi_tiers_for("2026-07-01", "2026-07-31")


# ─── the buffet mix chart buckets each month on its own menu ─────────────────

def test_buffet_mix_prices_a_september_cover_on_the_new_list():
    from routers.analysis import buffet_mix  # noqa: F401  (import guard)
    # A ฿702.27 ex-VAT cover is ฿789 net — the Grand Menu's mainstream tier.
    assert rules.tier_of_price(rules.base_to_net(702.27), "2026-09") == "789"
    # The same stored price in August is not a tier at all; it is a promo price
    # against the old base list.
    assert rules.tier_of_price(702.27, "2026-08") == "promo"


# ─── the spend-band KPI names a spend, not a retired tier ────────────────────

def test_spend_head_high_band_is_named_by_its_spend(client):
    """The Guest tab's 'high spender' KPI used to read '% เกิน Buffet 590'.

    Two things were wrong with it and only one was the retired price: the
    threshold has never been ฿590 either — the bands that clear it start at
    ฿700. The payload now carries the cut-off it actually uses, under a name
    that says so, with the old key kept as an alias.
    """
    r = client.get("/api/analysis/sph")
    assert r.status_code == 200
    ov = r.json().get("overall") or {}
    assert ov.get("high_spend_from") == 700
    assert "pct_above_700" in ov
    # the alias still carries the identical number, so a cached page keeps working
    assert ov["pct_above_700"] == ov["pct_above_590"]


def test_spend_bands_are_plain_ranges_not_tier_names(client):
    """A band must never be labelled with a tier, or a September bill lands in a
    bucket named after a price it could not have been sold at."""
    r = client.get("/api/analysis/sph")
    for b in r.json().get("distribution") or []:
        assert "tier" not in b["bucket"].lower()
        assert "buffet" not in b["bucket"].lower()
