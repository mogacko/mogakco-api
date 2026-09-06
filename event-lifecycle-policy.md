# 이벤트 생명주기 정책

## 참가 신청 취소

- 참가자는 행사 시작 시각의 정확히 2시간 전까지 참가 신청을 취소할 수 있다.
- 행사 시작 2시간 전을 지난 요청은 `CANCEL_PERIOD_EXPIRED`로 거절한다.
- 행사 일시는 KST 기준 `date + start_at`으로 계산한다.

## 이벤트 상태

이벤트의 저장 상태에 `COMPLETED`를 추가한다.

| 상태 | 의미 | 종료 상태 |
|---|---|---|
| `PENDING` | 승인 대기 | 아니요 |
| `APPROVED` | 예정 또는 진행 중 | 아니요 |
| `COMPLETED` | 정상 종료 | 예 |
| `CANCEL` | 취소 | 예 |
| `REJECTED` | 승인 거절 | 예 |

- 정상 승인된 행사의 종료 여부는 KST 기준 `date + end_at`으로 판단한다.
- 상태가 바뀌어도 `date`, `start_at`, `end_at`은 변경하지 않는다.
- `PENDING` 상태로 행사 시간이 지난 이벤트는 `REJECTED`로 처리한다.

## 종료 상태 갱신

- 스케줄러는 매일 새벽 03:00 KST에 한 번 실행한다.
- 종료 시각이 지난 `APPROVED` 이벤트를 `COMPLETED`로 변경한다.
- 시작 시각이 지난 `PENDING` 이벤트를 `REJECTED`로 변경한다.
- 갱신 조건에 현재 상태를 포함하여 반복 실행해도 결과가 같은 멱등 작업으로 구현한다.

## 호스트 참조

- `events.host_id` 컬럼은 `NULL`을 허용한다.
- 외래 키 삭제 정책은 `ON DELETE SET NULL`로 유지한다.
- 종료되지 않은 이벤트는 반드시 호스트를 가져야 한다.
- 종료된 이벤트는 사용자 정보가 영구 삭제된 후 `host_id=NULL`을 허용한다.

DB에서 다음 조건을 강제한다.

```sql
CHECK (
    host_id IS NOT NULL
    OR status IN ('COMPLETED', 'CANCEL', 'REJECTED')
)
```

따라서 활성 이벤트를 가진 사용자를 실수로 물리 삭제하면 외래 키의 `SET NULL` 결과가 위 조건을 위반하여 삭제가 차단된다.

사용자 탈퇴와 장기 개인정보 파기에 관한 후속 정책은 [사용자 탈퇴 및 개인정보 파기 고려사항](user-withdrawal-data-retention-policy.md)에서 별도로 관리한다.
