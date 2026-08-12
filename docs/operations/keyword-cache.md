# 자동완성 캐시 운영

`GET /keywords/suggest`가 읽는 Redis 캐시를 다룬다. 설계 배경은 [프로필 이미지와 키워드 자동완성](../complete/profile-media-and-keywords.md)에 있다.

## 무엇이 들어 있나

원본은 언제나 PostgreSQL의 `user_keywords`다. Redis에는 **노출 대상 집계만 사본으로** 담는다. 지워도 되고, 지우면 다시 만들면 된다.

```
keywords:v1:FIELD      JSON [[keyword, display], …]
keywords:v1:STACK
keywords:v1:INTEREST
```

목록은 (서로 다른 사용자 수 내림차순, 키워드순)으로 **미리 정렬돼** 있고, 5명 미만이 쓴 키워드는 애초에 담기지 않는다. 접두사 필터와 개수 절단만 API가 Python에서 한다.

메모리는 크게 잡아도 kind당 수백 KB다. 별도 인스턴스가 필요하지 않다.

## 설정

| 환경 변수 | 기본값 | 설명 |
|---|---|---|
| `REDIS_URL` | 없음 | 없으면 캐시를 쓰지 않고 PostgreSQL 집계로만 돈다 |
| `KEYWORD_CACHE_TTL_SECONDS` | `86400` | 갱신이 한 번 유실돼도 이 시간 안에 스스로 맞춰진다 |

`REDIS_URL`을 비워 두는 것은 **정상 동작**이다. 로컬 개발과 테스트가 이 상태로 돈다.

## 갱신은 언제 도나

가입(`POST /auth/signup`)과 프로필 수정(`PATCH /me`)이 성공한 **뒤에** `BackgroundTasks`로 돈다. 응답을 이미 보낸 다음이라 캐시가 죽어 있어도 저장은 실패하지 않는다. 크론은 필요 없다.

갱신은 증분이 아니라 세 kind 전체 재집계다. 저장 후 갱신과 유실 후 재구축이 같은 동작이라 코드도 `rebuild()` 하나를 공유한다.

## Redis가 죽으면

**자동완성은 계속 된다.** 미스·연결 실패·미설정을 구분하지 않고 PostgreSQL 재집계로 넘어간다. 응답 내용은 캐시가 살아 있을 때와 같고, 느려질 뿐이다.

폴백은 조용히 일어나지 않는다. 이런 경고가 애플리케이션 로그에 남는다.

```
자동완성 캐시를 읽지 못해 PostgreSQL로 넘어간다. kind=STACK
```

이 로그가 계속 찍히면 Redis를 확인한다. **급한 조치는 없다.** 캐시가 없어도 기능은 정상이다.

## 유실 후 재구축

Redis를 비웠거나 새 인스턴스로 옮겼다면 부른다. 안 불러도 다음 프로필 저장이나 첫 조회(미스) 때 저절로 채워지므로, 첫 조회들이 PostgreSQL을 치는 것을 피하고 싶을 때 미리 도는 용도다.

```
uv run python -m app.keywords.cache
```

```
FIELD 12개
STACK 87개
INTEREST 24개
```

## 확인

```
redis-cli GET keywords:v1:STACK     # 목록이 들어 있는지
redis-cli TTL keywords:v1:STACK     # 86400 이하의 남은 시간
```

TTL이 `-2`면 키가 없다는 뜻이다. 조회를 한 번 하거나 위 재구축 커맨드를 돌리면 채워진다.
