# 소셜 인증

이 문서는 Google·Apple·Kakao 네이티브 ID token 방식의 인증 계약과 검증 결과를 기록한다.

## API 계약

| API | 역할 |
|---|---|
| `POST /auth/social-login` | 네이티브 공급자 ID token 검증·로그인 시작 |
| `POST /auth/signup`, `refresh`, `logout` | 가입·세션 |
| `GET/PATCH /me` | 프로필 |
| `GET /terms/current`, `PUT /me/marketing-consent` | 약관·동의 |

핵심 모델: `users`(이메일 미저장, 닉네임 유일), `social_accounts`(공급자·외부 ID 유일), `auth_sessions`(refresh 해시), 약관·약관 버전·동의 이력.

## 구현 체크리스트

- [x] 설정, 모델·마이그레이션
- [x] 프로필·약관 API
- [x] 토큰·세션
- [x] 웹 인가 코드 제거
- [x] 단위·통합 테스트

## 남은 검증

실제 공급자 콘솔 설정과 Android·iOS 기기로 네이티브 ID token 로그인을 검증한다.

참조: [인증 결정](../decisions/authentication.md).
