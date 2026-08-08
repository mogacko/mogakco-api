"""seed initial terms

Revision ID: 20260808_0002
Revises: 20260803_0001
Create Date: 2026-08-08
"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "20260808_0002"
down_revision = "20260803_0001"
branch_labels = None
depends_on = None


TERMS = [
    ("SERVICE", True, "서비스 이용약관", "모각코 서비스 이용을 위해 계정을 만들고, 다른 이용자의 권리를 침해하거나 서비스 운영을 방해하는 행위를 해서는 안 됩니다. 운영상 필요한 경우 서비스 내용을 변경하거나 이용을 제한할 수 있습니다."),
    ("PRIVACY", True, "개인정보 수집·이용 동의", "모각코는 계정 식별, 프로필 제공, 모임 서비스 운영을 위해 가입 시 입력한 프로필 정보와 소셜 계정 식별자를 처리합니다. 법령상 보관 의무가 없는 정보는 서비스 목적이 끝나면 삭제하거나 익명화합니다."),
    ("AGE_14", True, "만 14세 이상 확인", "본인은 만 14세 이상이며, 제공한 정보가 사실임을 확인합니다."),
    ("MARKETING", False, "마케팅 정보 수신 동의", "모각코의 행사, 모임 소식, 혜택 안내를 받을 수 있습니다. 동의하지 않아도 서비스 이용에는 제한이 없으며, 언제든 철회할 수 있습니다."),
]


def upgrade() -> None:
    terms = sa.table("terms", sa.column("id", sa.Integer), sa.column("code", sa.String), sa.column("required", sa.Boolean))
    versions = sa.table("term_versions", sa.column("term_id", sa.Integer), sa.column("version", sa.String), sa.column("content", sa.Text), sa.column("effective_at", sa.DateTime(timezone=True)))
    op.bulk_insert(terms, [{"id": index, "code": code, "required": required} for index, (code, required, _, _) in enumerate(TERMS, 1)])
    op.bulk_insert(versions, [{"term_id": index, "version": "1.0", "content": f"{title}\n\n{content}\n\n※ 본 문안은 법률 검토 전 임시 초안입니다.", "effective_at": datetime(2026, 8, 8)} for index, (_, _, title, content) in enumerate(TERMS, 1)])


def downgrade() -> None:
    op.execute("DELETE FROM terms WHERE code IN ('SERVICE', 'PRIVACY', 'AGE_14', 'MARKETING')")
