# 인증·가입 결정

- 로그인은 Google·Kakao 인가 코드 흐름만 지원한다. 제공자 토큰은 보관하지 않는다.
- access token은 짧은 JWT, refresh token은 DB 해시·기기별 세션·매 사용 시 교체한다. 로그아웃은 현재 세션을 폐기한다.
- 가입 필수값은 닉네임(전역 유일), 활동 지역(`SEOUL`/`BUSAN`), `SERVICE`·`PRIVACY`·`AGE_14` 동의다. 나머지 프로필과 `MARKETING` 동의는 선택이다.
- 약관은 버전과 동의 이력으로 보관하며, 마케팅 재동의는 새 이력을 만든다.
- 초기 약관 문안은 임시 초안이며, 법률 검토 후 새 버전으로 교체한다.
- 구현 위치는 `app/auth`, `app/users`, `app/terms`다. 외부 공급자 연동도 현재는 `app/auth`에 둔다.
