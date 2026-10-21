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


def _race(fn, n=2):
    import threading
    barrier, errs = threading.Barrier(n), []

    def run():
        barrier.wait()
        try:
            fn()
        except rewards.RewardError as e:
            errs.append(str(e))
    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errs


def test_concurrent_redeems_cant_overspend_or_pass_the_limit(conn, cust, tmp_path):
    c = cust()
    rid = rewards.create(conn, "Free Coke", 1000, "", 1, "Will")
    logic.adjust_points(conn, c["id"], 1500, "seed", "Will")
    conn.commit()

    def go():
        other = db.connect(str(tmp_path / "t.db"))
        try:
            rewards.redeem(other, rid, c, "Will")
            other.commit()
        finally:
            other.close()
    errs = _race(go)
    assert len(errs) == 1
    assert logic.points_balance(conn, c["id"]) == 500
    assert conn.execute("SELECT COUNT(*) FROM redemptions").fetchone()[0] == 1


def test_concurrent_undos_give_points_back_once(conn, cust, tmp_path):
    c = cust()
    rid = rewards.create(conn, "Free Coke", 1000, "", None, "Will")
    logic.adjust_points(conn, c["id"], 1500, "seed", "Will")
    red = rewards.redeem(conn, rid, c, "Will")
    conn.commit()

    def go():
        other = db.connect(str(tmp_path / "t.db"))
        try:
            rewards.undo(other, red, "Will")
            other.commit()
        finally:
            other.close()
    errs = _race(go)
    assert errs == ["Already undone."]
    assert logic.points_balance(conn, c["id"]) == 1500


def test_merge_moves_redemptions(client):
    login(client)
    client.post("/tools/add_customer", data={"name": "Kim Keep", "email": "rns7@reddamnorthshore.nsw.edu.au"})
    client.post("/tools/add_customer", data={"name": "Kim Dup", "email": "rns8@reddamnorthshore.nsw.edu.au"})
    client.post("/tools/bulk_points", data={"delta": "1000", "note": "seed", "people": "Kim Dup"})
    client.post("/rewards", data={"name": "Free Coke", "points": "500", "limit": "1"})
    client.post("/customers/2/redeem/1")
    client.post("/tools/merge", data={"keep": "Kim Keep", "dup": "Kim Dup"})
    page = client.get("/customers/1").text
    assert "can have this now" not in page                             # limit carried over
    client.post("/redemptions/1/undo")
    conn = db.connect()
    try:
        assert logic.points_balance(conn, 1) == 1000                    # points back on the kept customer
        assert logic.points_balance(conn, 2) == 0
    finally:
        conn.close()


def test_huge_numbers_give_a_message_not_a_crash(client):
    login(client)
    for data in ({"name": "X", "points": "99999999999999999999999"},
                 {"name": "X", "points": "10", "limit": "99999999999999999999999"}):
        r = client.post("/rewards", data=data)
        assert r.status_code == 200 and "must be between" in r.text


def test_server_search_shows_reward_tag(client):
    login(client)
    client.post("/tools/add_customer", data={"name": "Rich Kid", "email": "rns5@reddamnorthshore.nsw.edu.au"})
    client.post("/tools/bulk_points", data={"delta": "1500", "note": "seed", "people": "Rich Kid"})
    client.post("/tools/add_customer", data={"name": "Rich Kidd", "email": "rns6@reddamnorthshore.nsw.edu.au"})
    client.post("/rewards", data={"name": "Free Coke", "points": "1000"})
    page = client.get("/customers?q=rich").text                      # two matches: a results table
    assert page.count('tag reward">Reward') == 1
