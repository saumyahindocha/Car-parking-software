from relay.plates import approx_matches, mask, normalise
from relay.web import rupees

from .conftest import push

RECEIPT = {"code": "Xy7kQ2mN", "number": "R260926-0000042", "created_at": "2026-09-26T04:05:00+00:00",
           "data": {"number": "R260926-0000042", "link": "https://pay.example.in/r/Xy7kQ2mN",
                    "lot_name": "Station Parking", "lot_address": "Station Road", "gstin": "27ABCDE1234F1Z5",
                    "plate": "MH 12 AB 1234", "vehicle_class": "BIKE", "mode": "UPI", "amount_paise": 2500,
                    "base_paise": 2000, "dues_cleared_paise": 500, "entry_time": "2026-09-26T08:30:00+05:30",
                    "duration_paid_minutes": 240, "paid_at": "2026-09-26T09:35:00+05:30", "upi_ref": "412345678901",
                    "txn_ref": "PS15XA1B2C3", "collected_by": "Self-pay", "gst_rate_percent": 18.0,
                    "taxable_paise": 2119, "gst_paise": 381,
                    "footer": "Final charge is calculated on actual time; any difference is adjusted on your next visit."}}


def test_receipt_page_renders_all_fields(client):
    push(client, receipts=[RECEIPT])
    r = client.get("/r/Xy7kQ2mN")
    assert r.status_code == 200 and r.headers["x-robots-tag"] == "noindex"
    t = r.text
    for s in ("Station Parking", "Station Road", "R260926-0000042", "MH 12 AB 1234", "Bike", "26 Sep 2026, 08:30 am",
              "4 h", "₹25", "₹5", "412345678901", "27ABCDE1234F1Z5", "₹21.19", "₹3.81", "18.0%", "UPI",
              "Final charge is calculated on actual time; any difference is adjusted on your next visit."):
        assert s in t, s


def test_receipt_missing_page(client):
    r = client.get("/r/NotYet99")
    assert r.status_code == 404 and "try again in a minute" in r.text.lower()


def test_languages(client):
    r = client.get("/?lang=hi")
    assert "अभी पार्किंग का भुगतान करें" in r.text and 'lang="hi"' in r.text
    assert client.cookies.get("lang") == "hi"
    assert "अभी पार्किंग का भुगतान करें" in client.get("/").text  # remembered
    r = client.get("/pay?lang=mr")
    assert "आपला वाहन क्रमांक लिहा" in r.text
    client.cookies.clear()
    r = client.get("/", headers={"accept-language": "mr-IN,mr;q=0.9,en;q=0.5"})
    assert "आता पार्किंगचे पैसे भरा" in r.text


def test_masking_and_helpers():
    assert mask("MH12AB1234") == "MH12••••34"
    assert mask("DL3CUV8901") == "DL3C••••01"
    assert mask("KA01") == "KA••"
    assert normalise(" mh-12 ab.1234 ") == "MH12AB1234"
    # 8/B confusable -> distance 1; two edits away or another state -> not offered
    assert approx_matches("MH12A81234", ["MH12AB1234", "KA01AB1234", "MH12AB1235"]) == [("MH12AB1234", 1)]
    assert approx_matches("MH12AB1234", ["MH12AB1234"]) == []  # exact is handled separately
    assert rupees(123456789) == "₹12,34,567.89" and rupees(-500) == "-₹5" and rupees(2000) == "₹20"


def test_healthz(client):
    assert client.get("/healthz").json()["ok"] is True
