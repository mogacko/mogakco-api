# 소셜 인증 API 명세

Google·Kakao만 허용한다. 기존 소셜 계정은 로그인, 신규 계정은 필수 약관·프로필 입력 후 가입한다. 앱 복귀는 Universal Link/App Link만 사용하며 URL은 설정으로 관리한다.

| API | 역할 |
|---|---|
| `GET /auth/{provider}/authorize`, `callback` | 소셜 인가·앱 링크 복귀 |
| `POST /auth/token`, `signup`, `refresh`, `logout` | 로그인·가입·세션 |
| `GET/PATCH /me` | 프로필 |
| `GET /terms/current`, `PUT /me/marketing-consent` | 약관·동의 |

핵심 모델: `users`(이메일 미저장, 닉네임 유일), `social_accounts`(공급자·외부 ID 유일), `auth_sessions`(refresh 해시), 약관·약관 버전·동의 이력.
