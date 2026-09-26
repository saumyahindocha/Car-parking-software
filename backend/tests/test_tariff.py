from datetime import datetime, timedelta, timezone

import pytest

from app.domain.tariff import TariffSpec, calculate_charge, count_overnights, tariff_for
from zoneinfo import ZoneInfo

from .conftest import ist

T = TariffSpec()  # seed default: ₹10 / 2 h, ₹5 per extra hour, ₹30 cap per 12 h, 10 min grace
E = ist(2026, 3, 10, 8, 0)


def ch(minutes, t=T, entry=E):
    return calculate_charge("BIKE", entry, entry + timedelta(minutes=minutes), t)


@pytest.mark.parametrize("minutes,paise", [
    (0, 1000), (1, 1000), (60, 1000), (120, 1000), (130, 1000),   # within first slab + grace
    (131, 1500), (190, 1500), (191, 2000), (250, 2000), (251, 2500),
    (310, 2500), (311, 3000), (600, 3000), (720, 3000),            # capped at ₹30 per 12 h block
    (730, 3000),                                                    # grace after a full block
    (731, 4000), (850, 4000), (851, 4500),                          # second block restarts slab
    (1440, 6000), (1450, 6000), (1451, 7000), (3 * 1440, 18000),
])
def test_default_bike_tariff(minutes, paise):
    assert ch(minutes) == paise


def test_day_boundary_same_price_as_daytime():
    night = ist(2026, 3, 10, 23, 0)
    assert ch(180, entry=night) == ch(180)


def test_month_and_year_boundary():
    assert ch(24 * 60 + 30, entry=ist(2026, 1, 31, 20, 0)) == ch(24 * 60 + 30)
    assert ch(300, entry=ist(2026, 12, 31, 22, 0)) == 2500


def test_leap_day():
    assert ch(2 * 1440, entry=ist(2028, 2, 28, 12, 0)) == 12000


def test_free_minutes():
    t = TariffSpec(free_minutes=5)
    assert ch(5, t) == 0
    assert ch(6, t) == 1000


def test_daily_cap():
    t = TariffSpec(daily_cap_paise=5000)
    assert ch(1440, t) == 5000
    assert ch(1440 + 60, t) == 6000
    assert ch(2 * 1440, t) == 10000


def test_overnight_charge():
    t = TariffSpec(overnight_paise=2000, overnight_cutoff_hour=0)
    assert ch(180, t, entry=ist(2026, 3, 10, 22, 0)) == 1500 + 2000   # crosses midnight once
    assert ch(180, t, entry=ist(2026, 3, 10, 9, 0)) == 1500
    assert count_overnights(ist(2026, 3, 10, 22), ist(2026, 3, 13, 1), 0, ZoneInfo("Asia/Kolkata")) == 3


def test_no_block_cap():
    t = TariffSpec(block_cap_paise=None, block_minutes=0)
    assert ch(600, t) == 1000 + 8 * 500


def test_validation():
    with pytest.raises(ValueError):
        calculate_charge("CAR", E, E, T)
    with pytest.raises(ValueError):
        calculate_charge("BIKE", E, E - timedelta(minutes=1), T)
    with pytest.raises(ValueError):
        calculate_charge("BIKE", datetime(2026, 1, 1), datetime(2026, 1, 1, 1), T)


def test_tariff_in_force_at_entry_applies_mid_stay_change():
    old = TariffSpec(effective_from=ist(2025, 1, 1), version=1)
    new = TariffSpec(effective_from=ist(2026, 3, 10, 12, 0), version=2, first_slab_paise=2000, block_cap_paise=5000)
    entry = ist(2026, 3, 10, 10, 0)
    exit_ = ist(2026, 3, 10, 15, 0)
    t = tariff_for([old, new], "BIKE", entry)
    assert t.version == 1
    assert calculate_charge("BIKE", entry, exit_, t) == 2500
    t2 = tariff_for([old, new], "BIKE", ist(2026, 3, 10, 13, 0))
    assert t2.version == 2
    with pytest.raises(LookupError):
        tariff_for([new], "BIKE", entry)


def test_pure_utc_vs_ist_inputs_equivalent():
    e_utc = E.astimezone(timezone.utc)
    assert calculate_charge("BIKE", e_utc, e_utc + timedelta(hours=5), T) == ch(300)
