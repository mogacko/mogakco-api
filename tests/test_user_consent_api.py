from datetime import datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from test_user_profile_api import AUTH, ME, MOCK_VIEWER_UUID
from test_user_profile_api import api as api  # noqa: PLC0414 -- pytest fixture 재사용

from app.models import MarketingConsentHistory, TermAgreement, User
from app.services import consent
from app.time import KST

URL = ME + "/marketing-consent"


def test_first_change_and_same_value_retry_share_profile_state(api, monkeypatch):
    client, engine = api
    now = datetime(2026, 9, 21, 23, 59, 59, tzinfo=KST)
    monkeypatch.setattr(consent, "kst_now", lambda: now)
    empty = client.patch(URL, headers=AUTH, json={"marketingAgreed": False})
    assert empty.json() == {"marketingAgreed": False, "changedAt": None}
    with Session(engine) as db:
        assert (
            db.scalar(sa.select(sa.func.count()).select_from(MarketingConsentHistory))
            == 0
        )
        assert db.scalar(sa.select(sa.func.count()).select_from(TermAgreement)) == 0
    changed = client.patch(URL, headers=AUTH, json={"marketingAgreed": True})
    assert changed.status_code == 200
    assert changed.json() == {"marketingAgreed": True, "changedAt": now.isoformat()}
    with Session(engine) as db:
        agreement = db.scalar(sa.select(TermAgreement))
        history = db.scalar(sa.select(MarketingConsentHistory))
        assert agreement.is_agreed is True
        assert agreement.signed_at == history.changed_at
        assert history.previous_is_agreed is None
        assert history.version == agreement.version == "1.0.0"
    monkeypatch.setattr(
        consent, "kst_now", lambda: datetime(2026, 9, 22, 10, tzinfo=KST)
    )
    assert (
        client.patch(URL, headers=AUTH, json={"marketingAgreed": True}).json()
        == changed.json()
    )
    profile = client.get(ME, headers=AUTH).json()
    assert profile["marketingConsent"] is True
    assert profile["marketingConsentChangedAt"] == now.isoformat()
    patched = client.patch(ME, headers=AUTH, json={"bio": "변경"}).json()
    assert patched["marketingConsentChangedAt"] == now.isoformat()
    with Session(engine) as db:
        assert (
            db.scalar(sa.select(sa.func.count()).select_from(MarketingConsentHistory))
            == 1
        )
    withdrawn = client.patch(URL, headers=AUTH, json={"marketingAgreed": False})
    assert withdrawn.status_code == 200
    assert withdrawn.json()["changedAt"] != now.isoformat()
    with Session(engine) as db:
        history = db.scalars(
            sa.select(MarketingConsentHistory).order_by(MarketingConsentHistory.id)
        ).all()
        assert [(row.previous_is_agreed, row.is_agreed) for row in history] == [
            (None, True),
            (True, False),
        ]
        assert history[0].changed_at.replace(tzinfo=KST) == now


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"agreed": True},
        {"marketingAgreed": None},
        {"marketingAgreed": "true"},
        {"marketingAgreed": 1},
        {"marketingAgreed": False, "version": "2.0.0"},
    ],
)
def test_consent_requires_exact_boolean_and_server_owned_identity(api, body):
    client, _ = api
    assert client.patch(URL, headers=AUTH, json=body).status_code == 422
    assert client.get(ME, headers=AUTH).json()["marketingConsent"] is False


def test_auth_and_extra_query(api):
    client, _ = api
    assert client.patch(URL, json={"marketingAgreed": True}).status_code == 401
    assert (
        client.patch(
            URL + "?regionName=seoul", headers=AUTH, json={"marketingAgreed": True}
        ).status_code
        == 422
    )


def test_legacy_missing_timestamp_and_existing_version_are_preserved(api):
    client, engine = api
    with Session(engine) as db:
        user = db.scalar(sa.select(User).where(User.uuid == MOCK_VIEWER_UUID))
        db.add(
            TermAgreement(
                user_id=user.id,
                type="MARKETING",
                version="0.9.0",
                is_required=False,
                is_agreed=True,
                signed_at=None,
            )
        )
        db.commit()
    assert client.patch(URL, headers=AUTH, json={"marketingAgreed": True}).json() == {
        "marketingAgreed": True,
        "changedAt": None,
    }
    assert client.get(ME, headers=AUTH).json()["marketingConsentChangedAt"] is None
    assert (
        client.patch(URL, headers=AUTH, json={"marketingAgreed": False}).status_code
        == 200
    )
    with Session(engine) as db:
        assert db.scalar(sa.select(MarketingConsentHistory.version)) == "0.9.0"
        assert db.scalar(sa.select(MarketingConsentHistory.previous_is_agreed)) is True


def test_preserves_old_user_consent_without_inventing_history(api):
    client, engine = api
    with Session(engine) as db:
        db.execute(
            sa.update(User)
            .where(User.uuid == MOCK_VIEWER_UUID)
            .values(marketing_consent=True)
        )
        db.commit()
    assert client.get(ME, headers=AUTH).json()["marketingConsent"] is True
    assert (
        client.patch(URL, headers=AUTH, json={"marketingAgreed": True}).json()[
            "changedAt"
        ]
        is None
    )
    assert (
        client.patch(URL, headers=AUTH, json={"marketingAgreed": False}).status_code
        == 200
    )
    with Session(engine) as db:
        assert db.scalar(sa.select(MarketingConsentHistory.previous_is_agreed)) is True
        assert (
            db.scalar(sa.select(sa.func.count()).select_from(MarketingConsentHistory))
            == 1
        )


def test_latest_numeric_version_is_read_and_only_that_version_is_changed(api):
    client, engine = api
    with Session(engine) as db:
        user_id = db.scalar(sa.select(User.id).where(User.uuid == MOCK_VIEWER_UUID))
        db.add_all(
            [
                TermAgreement(
                    user_id=user_id,
                    type="MARKETING",
                    version=version,
                    is_required=False,
                    is_agreed=version != "1.10.0",
                )
                for version in ("1.0.0", "1.10.0", "1.9.0")
            ]
        )
        db.commit()
    profile = client.get(ME, headers=AUTH)
    assert profile.status_code == 200
    assert profile.json()["marketingConsent"] is False
    assert client.patch(ME, headers=AUTH, json={"bio": "변경"}).status_code == 200
    response = client.patch(URL, headers=AUTH, json={"marketingAgreed": True})
    assert response.status_code == 200
    with Session(engine) as db:
        agreements = db.scalars(sa.select(TermAgreement)).all()
        assert all(row.is_agreed for row in agreements)
        assert all(
            row.signed_at is None for row in agreements if row.version != "1.10.0"
        )
        history = db.scalars(sa.select(MarketingConsentHistory)).one()
        assert history.version == "1.10.0"
        assert history.previous_is_agreed is False
        assert history.is_agreed is True


def test_history_failure_rolls_back_existing_consent_and_time(api):
    client, engine = api
    initial = client.patch(URL, headers=AUTH, json={"marketingAgreed": True}).json()

    def fail_history(connection, cursor, statement, parameters, context, executemany):
        if (
            statement.lstrip()
            .upper()
            .startswith("INSERT INTO MARKETING_CONSENT_HISTORY")
        ):
            raise sa.exc.OperationalError(statement, parameters, RuntimeError("failed"))

    # ORM flush를 건너뛰지 않고 실제 이력 INSERT 직후 저장 오류를 발생시킨다.
    sa.event.listen(engine, "after_cursor_execute", fail_history)
    try:
        result = client.patch(URL, headers=AUTH, json={"marketingAgreed": False})
    finally:
        sa.event.remove(engine, "after_cursor_execute", fail_history)
    assert result.status_code == 500
    assert result.json() == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "수신 동의를 변경하지 못했습니다.",
    }
    profile = client.get(ME, headers=AUTH).json()
    assert profile["marketingConsent"] is True
    assert profile["marketingConsentChangedAt"] == initial["changedAt"]
    with Session(engine) as db:
        assert (
            db.scalar(sa.select(sa.func.count()).select_from(MarketingConsentHistory))
            == 1
        )
