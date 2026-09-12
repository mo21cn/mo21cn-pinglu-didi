"""F5 撮合引擎 Stage1 测试：硬约束过滤 + 多目标评分 + 双向撮合端点。"""

from __future__ import annotations

API_SHIP = "/api/v1/ship/registry"
API_CARGO = "/api/v1/cargo/shipments"
API_MATCH = "/api/v1/match"

CARGO_PAYLOAD = {
    "cargo_name": "水泥熟料",
    "cargo_type": "bulk",
    "weight_t": 1000,
    "origin_port": "NNG",
    "dest_port": "QNZ",
    "expect_date": "2026-10-01",
    "publish_now": True,
}


def _ship_payload(**overrides) -> dict:
    payload = {
        "ship_name": "桂平顺发 88",
        "ship_type": "bulk",
        "deadweight_t": 1500,
        "length_m": 90,
        "width_m": 14,
        "draft_m": 3.5,
        "home_port": "GGU",
        "cert_no": "CERT-F5-000",
        "cert_expiry": "2027-12-31",
    }
    payload.update(overrides)
    return payload


def _make_verified_ship(client, owner, port_user, **overrides) -> int:
    sid = client.post(API_SHIP, json=_ship_payload(**overrides), headers=owner["_headers"]).json()[
        "id"
    ]
    resp = client.post(
        f"{API_SHIP}/{sid}/verify", json={"approved": True}, headers=port_user["_headers"]
    )
    assert resp.status_code == 200, resp.text
    return sid


def _make_cargo(client, shipper, **overrides) -> dict:
    payload = dict(CARGO_PAYLOAD)
    payload.update(overrides)
    resp = client.post(API_CARGO, json=payload, headers=shipper["_headers"])
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---- 正向：为货源找候选船 ----


def test_match_cargo_returns_ranked_verified_ships(owner, port_user, shipper, client):
    """高利用率船排前；待审船被过滤并计入 filter_stats。"""
    _make_verified_ship(
        client, owner, port_user, ship_name="满载号", deadweight_t=1000
    )  # 利用率 1.0
    _make_verified_ship(
        client, owner, port_user, ship_name="半载号", deadweight_t=2000
    )  # 利用率 0.5
    client.post(
        API_SHIP, json=_ship_payload(ship_name="待审号"), headers=owner["_headers"]
    )  # 不审核

    cargo = _make_cargo(client, shipper)
    resp = client.post(f"{API_MATCH}/cargos/{cargo['id']}/ships", headers=shipper["_headers"])
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["total"] == 2
    names = [item["ship_name"] for item in body["items"]]
    assert names == ["满载号", "半载号"]  # 按得分降序
    assert body["items"][0]["score"] > body["items"][1]["score"]
    assert body["filter_stats"] == {"ship_not_verified": 1}
    # 得分拆解四项齐全且和等于总分
    top = body["items"][0]
    assert set(top["breakdown"]) == {"load_utilization", "type_fit", "home_port", "cert_margin"}
    assert abs(sum(top["breakdown"].values()) - top["score"]) < 0.05


def test_match_cargo_hard_filters(owner, port_user, shipper, client):
    """证书过期 / 载重不足 / 船型不兼容 各自被过滤且计数正确。"""
    _make_verified_ship(client, owner, port_user, ship_name="好船", cert_no="C-OK")
    _make_verified_ship(
        client,
        owner,
        port_user,
        ship_name="证书过期",
        cert_no="C-EXP",
        cert_expiry="2026-09-20",  # 覆盖今天但早于装货日
    )
    _make_verified_ship(
        client, owner, port_user, ship_name="小船", cert_no="C-SMALL", deadweight_t=500
    )
    _make_verified_ship(
        client, owner, port_user, ship_name="油轮", cert_no="C-TANK", ship_type="tanker"
    )

    cargo = _make_cargo(client, shipper)
    resp = client.post(f"{API_MATCH}/cargos/{cargo['id']}/ships", headers=shipper["_headers"])
    assert resp.status_code == 200
    body = resp.json()

    assert [i["ship_name"] for i in body["items"]] == ["好船"]
    assert body["filter_stats"] == {
        "cert_expired": 1,
        "deadweight_insufficient": 1,
        "type_incompatible": 1,
    }


def test_match_cargo_type_compat_scores_lower_than_exact(owner, port_user, shipper, client):
    """件杂货船承运散货（兼容 0.6）得分应低于散货船精确匹配。"""
    _make_verified_ship(
        client, owner, port_user, ship_name="散货船", ship_type="bulk", deadweight_t=1000
    )
    _make_verified_ship(
        client, owner, port_user, ship_name="件杂货船", ship_type="general", deadweight_t=1000
    )

    cargo = _make_cargo(client, shipper)
    resp = client.post(f"{API_MATCH}/cargos/{cargo['id']}/ships", headers=shipper["_headers"])
    body = resp.json()
    by_name = {i["ship_name"]: i for i in body["items"]}

    assert by_name["散货船"]["breakdown"]["type_fit"] == 20.0
    assert by_name["件杂货船"]["breakdown"]["type_fit"] == 12.0
    assert by_name["散货船"]["score"] > by_name["件杂货船"]["score"]


def test_match_cargo_home_port_bonus(owner, port_user, shipper, client):
    """船籍港等于起运港（NNG）的船获 20 分就近加分。"""
    _make_verified_ship(
        client, owner, port_user, ship_name="本地船", home_port="NNG", cert_no="C-LOCAL"
    )
    _make_verified_ship(
        client, owner, port_user, ship_name="外地船", home_port="WUZ", cert_no="C-FAR"
    )

    cargo = _make_cargo(client, shipper)  # 起运港 NNG
    resp = client.post(f"{API_MATCH}/cargos/{cargo['id']}/ships", headers=shipper["_headers"])
    body = resp.json()
    by_name = {i["ship_name"]: i for i in body["items"]}

    assert by_name["本地船"]["breakdown"]["home_port"] == 20.0
    assert by_name["外地船"]["breakdown"]["home_port"] == 0.0


def test_match_cargo_requires_published(shipper, client):
    """draft 货源不允许撮合（须先发布进入撮合池）。"""
    cargo = _make_cargo(client, shipper, publish_now=False)
    resp = client.post(f"{API_MATCH}/cargos/{cargo['id']}/ships", headers=shipper["_headers"])
    assert resp.status_code == 400
    assert "发布" in resp.json()["detail"]


def test_match_cargo_not_owned_404(shipper, client):
    resp = client.post(f"{API_MATCH}/cargos/9999/ships", headers=shipper["_headers"])
    assert resp.status_code == 404


def test_match_cargo_role_guard(owner, shipper, client):
    """船东角色不能调用货主撮合端点。"""
    cargo = _make_cargo(client, shipper)
    resp = client.post(f"{API_MATCH}/cargos/{cargo['id']}/ships", headers=owner["_headers"])
    assert resp.status_code == 403


# ---- 反向：为船找候选货源 ----


def test_match_ship_returns_published_cargos(owner, port_user, shipper, client):
    """已发布货源入列；draft 货源被过滤并计数。"""
    _make_verified_ship(client, owner, port_user, cert_no="C-REV")
    _make_cargo(client, shipper, cargo_name="已发布货")  # publish_now=True
    _make_cargo(client, shipper, cargo_name="草稿货", publish_now=False)

    ships = client.get(API_SHIP, headers=owner["_headers"]).json()["items"]
    sid = ships[0]["id"]
    resp = client.post(f"{API_MATCH}/ships/{sid}/cargos", headers=owner["_headers"])
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert [i["cargo_name"] for i in body["items"]] == ["已发布货"]
    assert body["filter_stats"] == {"cargo_not_published": 1}
    top = body["items"][0]
    assert top["origin_port"] == "NNG" and top["dest_port"] == "QNZ"


def test_match_ship_requires_verified(owner, client):
    """未审核船舶无撮合资格。"""
    sid = client.post(API_SHIP, json=_ship_payload(), headers=owner["_headers"]).json()["id"]
    resp = client.post(f"{API_MATCH}/ships/{sid}/cargos", headers=owner["_headers"])
    assert resp.status_code == 400
    assert "审核" in resp.json()["detail"]


def test_match_ship_not_owned_404(owner, port_user, shipper, client):
    """不能撮合他人的船。"""
    sid = _make_verified_ship(client, owner, port_user)
    resp = client.post(f"{API_MATCH}/ships/{sid}/cargos", headers=shipper["_headers"])
    assert resp.status_code in (403, 404)  # 货主角色先行拦截


def test_match_ship_role_guard(owner, port_user, shipper, client):
    sid = _make_verified_ship(client, owner, port_user)
    resp = client.post(f"{API_MATCH}/ships/{sid}/cargos", headers=shipper["_headers"])
    if resp.status_code == 404:  # 非本人船路径 → 404；先造本人船测 403
        sid = client.post(
            API_SHIP, json=_ship_payload(ship_name="shipper的船"), headers=shipper["_headers"]
        ).json()["id"]
        resp = client.post(f"{API_MATCH}/ships/{sid}/cargos", headers=shipper["_headers"])
    assert resp.status_code == 403


# ---- 纯引擎单测：确定性内核数学性质 ----


def test_engine_pure_functions():
    """engine 不依赖 ORM/DB，直接验证评分数学性质与排序稳定性。"""
    from datetime import date

    from app.modules.match.engine import CargoInput, ShipInput, match_cargo_to_ships

    cargo = CargoInput(
        id=1,
        cargo_type="bulk",
        weight_t=800,
        origin_port="NNG",
        dest_port="QNZ",
        expect_date=date(2026, 10, 1),
        status="published",
    )
    ships = [
        ShipInput(
            id=1,
            ship_type="bulk",
            deadweight_t=800,
            draft_m=3.0,
            home_port="NNG",
            cert_expiry=date(2026, 12, 1),
            status="verified",
        ),  # 理论满分 80
        ShipInput(
            id=2,
            ship_type="bulk",
            deadweight_t=1600,
            draft_m=3.0,
            home_port="WUZ",
            cert_expiry=date(2026, 10, 15),
            status="verified",
        ),
        ShipInput(
            id=3,
            ship_type="tanker",
            deadweight_t=800,
            draft_m=3.0,
            home_port="NNG",
            cert_expiry=date(2027, 12, 31),
            status="verified",
        ),
    ]
    result = match_cargo_to_ships(cargo, ships)

    assert [c.ref_id for c in result.candidates] == [1, 2]
    # 满分项：利用率 40 + 船型 20 + 船籍港 20；证书余量 61 天 → 20*61/90 = 13.56
    assert result.candidates[0].score == 93.56
    assert result.candidates[0].breakdown == {
        "load_utilization": 40.0,
        "type_fit": 20.0,
        "home_port": 20.0,
        "cert_margin": 13.56,
    }
    assert result.filter_stats == {"type_incompatible": 1}
