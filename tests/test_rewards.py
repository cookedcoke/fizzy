import pytest

from fizzy import config, db, logic, rewards
from tests.test_app import client, login  # noqa: F401  (fixture)


def test_offers_redeem_limit_end_and_undo(conn, cust):
    c = cust()
    rid = rewards.create(conn, "Free Coke", 1000, "apply the Points: Free Coke discount", 1, "Will")
    offer = rewards.offers_for(conn, c["id"])[0]
    assert not offer["can_redeem"] and offer["short"] == 1000
    with pytest.raises(rewards.RewardError, match="needs 1000 more"):
        rewards.redeem(conn, rid, c, "Will")
    logic.adjust_points(conn, c["id"], 2500, "seed", "Will")
    assert rewards.offers_for(conn, c["id"])[0]["can_redeem"]
    red = rewards.redeem(conn, rid, c, "Will")
    assert logic.points_balance(conn, c["id"]) == 1500
    assert logic.spend_cents(conn, c["id"]) == 0                     # redeeming isn't spending
    offer = rewards.offers_for(conn, c["id"])[0]
    assert offer["limit_hit"] and not offer["can_redeem"]              # 1 per customer
    with pytest.raises(rewards.RewardError, match="limit"):
        rewards.redeem(conn, rid, c, "Will")
    rewards.undo(conn, red, "Will")
    assert logic.points_balance(conn, c["id"]) == 2500
    assert rewards.offers_for(conn, c["id"])[0]["can_redeem"]          # undone doesn't count
    rewards.end(conn, rid, "Will")
    assert rewards.offers_for(conn, c["id"]) == []
    with pytest.raises(rewards.RewardError, match="ended"):
        rewards.redeem(conn, rid, c, "Will")


def test_no_limit_means_repeatable(conn, cust):
    c = cust()
    rid = rewards.create(conn, "$2 off", 500, "", None, "Will")
    logic.adjust_points(conn, c["id"], 1200, "seed", "Will")
    rewards.redeem(conn, rid, c, "Will")
    rewards.redeem(conn, rid, c, "Will")
    assert logic.points_balance(conn, c["id"]) == 200
    assert not rewards.offers_for(conn, c["id"])[0]["can_redeem"]


def test_reward_cant_use_the_tab_discount(conn):
    with pytest.raises(rewards.RewardError, match="Fizzy Tab"):
        rewards.create(conn, "Free Coke", 1000, "apply the Fizzy Tab discount", None, "Will")


def test_undo_window(conn, cust):
    c = cust()
    rid = rewards.create(conn, "Free Coke", 100, "", None, "Will")
    logic.adjust_points(conn, c["id"], 100, "seed", "Will")
    red = rewards.redeem(conn, rid, c, "Will")
    conn.execute("UPDATE redemptions SET created_at = '2020-01-01T00:00:00+00:00'")
    with pytest.raises(rewards.RewardError, match="Too old"):
        rewards.undo(conn, red, "Will")


def test_rewards_pages_and_counter_flow(client):
    login(client)
    client.post("/tools/add_customer", data={"name": "Rich Kid", "email": "rns5@reddamnorthshore.nsw.edu.au"})
    client.post("/tools/add_customer", data={"name": "Poor Kid", "email": "rns6@reddamnorthshore.nsw.edu.au"})
    client.post("/tools/bulk_points", data={"delta": "1500", "note": "seed", "people": "Rich Kid"})
    r = client.post("/rewards", data={"name": "Free Coke", "points": "1000", "square_note": "apply Points: Free Coke"})
    assert "is live" in r.text and "Rewards on:" in client.get("/").text
    page = client.get("/customers/1").text
    assert "can have this now" in page and 'data-key="o"' in page and "In Square: apply Points: Free Coke" in page
    assert "Needs 1,000 more points" in client.get("/customers/2").text
    tags = {r["name"]: r["reward"] for r in client.get("/api/index").json()}
    assert tags == {"Rich Kid": True, "Poor Kid": False}
    r = client.post("/customers/1/redeem/1")
    assert "Redeemed Free Coke" in r.text and "Redeemed Free Coke</b>" in r.text   # flash + undo strip
    r = client.post("/redemptions/1/undo")
    assert "points given back" in r.text
    client.post("/rewards/1/end")
    assert "can have this now" not in client.get("/customers/1").text
    assert client.post("/rewards", data={"name": "", "points": "10"}).status_code == 200   # message, not a crash
    assert client.post("/rewards", data={"name": "X", "points": "abc"}).status_code == 200
