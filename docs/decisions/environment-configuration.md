# 환경 설정 결정

동일 코드에 환경변수만 주입한다. 로컬은 SQLite와 `.env.local`을 `uv run --env-file`로 읽고, 스테이징·운영은 PostgreSQL과 배포 환경의 비밀값 저장소를 사용한다.

`AUTH_API_BASE_URL`은 HTTPS를 기본으로 하되 로컬의 `localhost`·`127.0.0.1` HTTP만 허용한다. `AUTH_APP_LINK_BASE_URL`은 Universal Link/App Link 검증을 위해 항상 HTTPS여야 한다. 환경 파일에는 비밀값을 커밋하지 않는다.

## 배포는 미니PC부터, 사용자가 늘면 AWS로

처음에는 미니PC에 Docker Compose로 올린다. 사용자가 늘면 AWS로 옮긴다. **그래서 외부 의존은 전부 환경 변수 뒤에 둔다.** 컨테이너 PostgreSQL에서 RDS로, 로컬 Redis에서 ElastiCache로 갈아타도 `DATABASE_URL`·`REDIS_URL`·`S3_*`만 바뀌고 코드는 그대로다.

환경에 묶이는 조각은 **주기 실행 하나뿐**이다. 그래서 앱 안에 스케줄러를 넣지 않는다. 웹 프로세스에 붙이면 복제본 수만큼 중복 실행되고 배포할 때마다 타이머가 초기화된다. 대신 `python -m ...`으로 부를 수 있는 진입점만 두고, 부르는 쪽은 환경이 정한다.

- 미니PC: systemd 타이머 ([`deploy/systemd/`](../../deploy/systemd))
- AWS: EventBridge Scheduler → ECS RunTask, 같은 이미지에 같은 커맨드

옮길 때 버리는 것은 유닛 파일뿐이다. 미니PC는 재부팅·절전·정전으로 꺼져 있는 시간이 있으므로 `cron` 대신 `Persistent=true`를 주는 systemd 타이머를 쓴다.

미니PC가 **사용자 데이터의 유일한 사본**이 된다. Redis는 사본이라 날아가도 되지만 PostgreSQL은 아니다. 백업은 아직 없다.
