# 오류·예외 처리 가이드

새로운 API 오류를 추가하거나 기존 오류를 수정할 때 아래 규칙을 따른다.

## 기준 파일

- 예외 계층과 `ErrorSpec`: `app/exceptions.py`
- 공통 오류: `app/common/errors.py`
- 도메인 오류: `app/<domain>/errors.py`
- OpenAPI 오류 응답: `app/schemas/error.py`
- 글로벌 예외 핸들러: `app/main.py`

오류 정의의 기준은 문서가 아니라 위 소스 파일이다.

## 새 오류 추가 순서

1. 같은 의미의 기존 `ErrorSpec`을 재사용할 수 있는지 먼저 확인한다.
2. 새 오류가 필요하면 해당 도메인의 `*Errors` 카탈로그에 추가한다.
3. HTTP 상태에 맞는 `*Exception`과 조합해 발생시킨다.
4. 해당 라우터의 `error_responses()`에 같은 `ErrorSpec`을 추가한다.
5. 실제 오류 응답과 OpenAPI examples가 일치하는지 테스트한다.

```python
class EventErrors:
    NOT_FOUND = ErrorSpec(
        NotFoundException,
        "EVENT_NOT_FOUND",
        "이벤트를 찾을 수 없습니다.",
    )


raise NotFoundException(EventErrors.NOT_FOUND)
```

## HTTP 분류 기준

- `BadRequestException`(400): 요청 형식은 맞지만 현재 업무 상태에서 처리할 수 없음
- `AuthenticationException`(401): 인증 필요
- `ForbiddenException`(403): 권한 없음
- `NotFoundException`(404): 리소스 없음
- `MethodNotAllowedException`(405): 허용하지 않는 HTTP 메서드
- `ConflictException`(409): 중복, 정원 마감 등 리소스 상태 충돌
- `DomainValidationException`(422): 요청값 또는 도메인 값 조합 검증 실패
- `TooManyRequestsException`(429): 일시적인 요청 경합 또는 과다 요청
- `InternalServerException`(500): 서버 설정 등 내부 오류
- `ServiceUnavailableException`(503): 외부 서비스 사용 불가

## 금지 사항

- `AppException`을 직접 발생시키지 않는다.
- 업무 오류마다 새 예외 클래스를 만들지 않는다.
- 라우터와 서비스에 오류 코드나 메시지를 하드코딩하지 않는다.
- 모든 오류를 모은 단일 `Errors` 클래스나 중첩 카탈로그를 만들지 않는다.
- 도메인 카탈로그가 다른 도메인 카탈로그를 참조하지 않게 한다.
- 게시판과 카테고리 조합 같은 업무 규칙을 Pydantic validator에서 검증하지 않는다.
- 엔드포인트에서 글로벌 예외와 동일한 변환을 반복하지 않는다.

## 외부 예외

- 원인을 명확히 해석할 수 있을 때만 도메인 예외로 변환한다.
- 대체 처리로 정상 흐름을 유지할 수 있는 부가 기능 실패는 로그만 남긴다.
- 분류할 수 없는 오류와 내부 정합성 오류는 글로벌 500 처리에 맡긴다.

## 완료 확인

- 오류 코드가 기존 카탈로그와 중복되지 않는가?
- 예외 클래스와 `ErrorSpec`의 분류가 일치하는가?
- 라우터의 `error_responses()`에 오류가 선언되었는가?
- OpenAPI 예시의 상태, 코드, 메시지가 런타임 응답과 같은가?
- 관련 테스트와 전체 테스트가 통과하는가?
