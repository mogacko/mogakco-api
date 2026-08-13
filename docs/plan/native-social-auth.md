# Google·Apple·Kakao 소셜 로그인 전환

## 목표와 경계

- Flutter Android·iOS 앱에서 Google·Apple·Kakao 로그인을 제공한다.
- 앱이 공급자 인증을 완료한 뒤 API에 **ID token**을 HTTPS로 보내고, API는 해당 token의 검증된 `sub`만 계정 식별자로 쓴다. 공급자 access/refresh token·이메일·이름은 저장하거나 로그에 남기지 않는다.
- **플랫폼 차이는 앱에서 끝낸다.** iOS/Android 어느 쪽이든 동일한 `POST /auth/social-login`에 같은 provider의 ID token을 보내며, API는 `provider`별 검증만 하고 OS·SDK·리다이렉트 방식을 분기하지 않는다.
- 현재 사용자·소셜 계정 데이터는 없다. 따라서 기존 웹 OAuth 계정·콜백과의 호환이나 데이터 보존은 하지 않는다.
- 새 방식으로 이미 가입한 계정이 다시 로그인하면 Mogakco access·refresh token을 받고, 첫 로그인만 기존 `POST /auth/signup`에 쓸 단회 가입 코드를 받는다.
- 이 저장소는 API만 변경한다. Flutter SDK·Apple Developer/Google Cloud/Kakao Developers 콘솔 설정은 앱 저장소/운영 작업이다.

## 공통 API 계약

`POST /auth/social-login`

```json
{"provider":"GOOGLE|APPLE|KAKAO", "id_token":"provider-issued-JWT", "nonce":"optional-original-nonce"}
```

- `provider`는 대문자 세 값만 허용한다.
- `id_token`은 앱이 해당 로그인 시점에 받은 JWT다. API는 로그·DB에 원문을 남기지 않는다.
- `nonce`는 Kakao에 필수로 도입하고, Google·Apple도 SDK가 nonce를 지원하면 같은 방식으로 보낸다. API는 각 SDK가 ID token claim에 넣는 형식(raw 또는 hash)에 맞춰 비교한다. API가 로그인 시작 상태를 만들지 않는 구조이므로 앱은 secure storage에 생성·보관한 난수를 검증 직후 삭제한다.
- 가입 완료 계정 응답: `{"signup_required": false, "access_token":"...", "refresh_token":"...", "token_type":"bearer"}`.
- 첫 로그인 응답: `{"signup_required": true, "code":"..."}`. `code`는 현재 `LoginCode`의 해시·60초 TTL·단회 사용을 그대로 쓰며 `/auth/signup` 본문 `code`와 호환한다.
- 잘못된 provider는 404, 형식·누락은 422, 서명/claim/nonce 검증 실패는 401, 외부 JWKS timeout·5xx는 503으로 처리한다. 알 수 없는 `kid`는 JWKS를 한 번 새로 읽은 뒤에도 없으면 401로 처리한다. 사용자에게 공급자 token 세부 오류를 노출하지 않는다.

## 공급자별 앱 흐름과 서버 검증

| 공급자 | 앱(Android/iOS) | API가 받는 값 | 서버 검증 | 운영 설정 |
| --- | --- | --- | --- | --- |
| Google | Google Identity Services/Flutter 플러그인으로 로그인. backend용 **Web client ID**를 server client ID로 지정해 ID token을 받는다. | Google ID token | Google JWKS, `RS256`, `iss`=`accounts.google.com` 또는 `https://accounts.google.com`, `aud`=허용 Web client ID, `exp`, `sub` | Google Cloud에서 Android package+SHA-1, iOS bundle ID, Web client ID를 한 프로젝트에 등록. API에는 `GOOGLE_OAUTH_CLIENT_IDS`만 주입. |
| Apple | iOS는 AuthenticationServices 기반 Sign in with Apple 네이티브 인증을 사용한다. Android는 Apple 웹 인증을 시스템 브라우저/Custom Tab으로 연 뒤 HTTPS return URL로 앱에 돌아온다. **둘 다 앱이 최종 identity token만 같은 API로 전송한다.** | Apple identity token | Apple JWKS, `RS256`, `iss`=`https://appleid.apple.com`, `aud`=허용 목록의 iOS bundle ID 또는 Android 웹 Service ID, `exp`, `sub`, 앱 nonce를 썼다면 nonce | Apple Developer에서 capability를 App ID에 켜고 iOS bundle ID와 Android용 Service ID·도메인·return URL을 등록. API에는 쉼표 구분 `APPLE_CLIENT_IDS`를 주입. Android return URL은 앱 복귀용으로만 유지하며 API OAuth callback은 만들지 않는다. |
| Kakao | Flutter Kakao SDK로 카카오톡 로그인 가능 여부를 확인 후 카카오계정 로그인으로 폴백한다. Kakao OIDC를 활성화해 ID token을 받는다. | Kakao ID token과 앱 nonce | Kakao JWKS, `RS256`, `iss`=`https://kauth.kakao.com`, `aud`=Native app key, `exp`, `sub`, nonce | Kakao Developers에서 Android package+key hash, iOS bundle ID, Kakao Login, OpenID Connect를 활성화. API에는 `KAKAO_NATIVE_APP_KEY`만 주입. |

Google은 backend용 Web client ID audience를 확인해야 하며, Google 공식 문서는 서명·`aud`·`iss`·`exp` 검증을 요구한다. [Google backend authentication guide](https://developers.google.com/identity/sign-in/android/backend-auth)

Kakao는 OIDC를 켜야 ID token을 발급하며, 공식 문서는 JWKS 서명과 `iss`·`aud`·`exp`·nonce 검증을 요구한다. [Kakao OIDC guide](https://developers.kakao.com/docs/en/kakaologin/utilize)

Apple은 identity token을 반환하며, 이름·이메일은 최초 승인 응답에만 올 수 있으므로 이 서비스는 저장하지 않고 `sub`만 사용한다. [Apple identity token guide](https://developer.apple.com/documentation/signinwithapple/receiving-a-users-identity-token)

## API 구현 순서

1. `pyproject.toml`에 `cryptography`를 추가한다. 이미 있는 PyJWT의 `PyJWKClient`로 공급자 JWKS를 조회·캐시하고 `algorithms=["RS256"]`를 고정한다. JWKS URL·허용 issuer·audience는 세 공급자 상수/환경 설정으로만 둔다. 새 포트·어댑터 계층은 만들지 않는다.
2. `app/auth/config.py`에서 기존 OAuth callback/client-secret 설정을 `ProviderSettings`의 audience/key 설정으로 교체한다. 제거: `AUTH_API_BASE_URL`, `GOOGLE_CLIENT_SECRET`, `KAKAO_CLIENT_SECRET`. 추가: `GOOGLE_OAUTH_CLIENT_IDS`, `APPLE_CLIENT_IDS`, `KAKAO_NATIVE_APP_KEY`.
3. `app/auth/schemas.py`에 `SocialLoginRequest`와 두 응답 형태를 추가하고, `router.py`에 한국어 summary의 `POST /auth/social-login`을 추가한다.
4. `service.py`에 한 개의 `verify_provider_id_token(provider, id_token, nonce, settings) -> str`를 둔다. provider별 JWKS/issuer/audience/nonce 차이만 내부 분기로 처리해, 검증된 `sub`를 반환한다. Apple은 iOS bundle ID와 Android Service ID를 동일한 `APPLE_CLIENT_IDS` audience 허용 목록으로 검증한다. raw JWT, decode된 email/name은 반환·저장하지 않는다.
5. `find_social_user_id` 결과가 있으면 기존 `_issue_tokens`를 호출한다. 없으면 기존 `create_login_code(..., provider, sub)`를 호출한다. `signup`, refresh, logout 및 약관 로직은 그대로 둔다.
6. 웹 인가 경로·코드를 삭제한다: `GET /auth/{provider}/authorize`, `GET /auth/{provider}/callback`, `authorization_url`, `create_oauth_attempt`, `consume_oauth_attempt`, `provider_user_id`, `OAuthAttempt`, `CallbackSettings`.
7. 인증 스키마에 보존할 데이터가 없으므로, 초기 migration `20260803_0001_create_users.py`와 ORM 모델을 직접 고친다. provider check constraint를 `GOOGLE`·`APPLE`·`KAKAO`로 바꾸고 `oauth_attempts`와 인덱스를 제거한다. 이미 만들어진 로컬 개발 DB는 삭제 후 migration을 다시 적용한다. 배포 DB가 이미 있다면 적용 전에 별도 migration으로 전환한다.
8. `.env.example`, `.env.local.example`, `docs/decisions/authentication.md`, `docs/complete/social-auth.md`, API 설명을 새 계약으로 바꾸고, 구현·검증 완료 뒤 이 문서를 `docs/complete/`로 이동한다.

## Flutter·운영 체크리스트

- [ ] Google Cloud: Android SHA-1/package name, iOS bundle ID, Web OAuth client ID 생성 및 API audience 값 배포.
- [ ] Apple Developer: Sign in with Apple capability, iOS App ID, Android용 Service ID·도메인·HTTPS return URL 등록. Android return URL 소유·앱 복귀를 E2E 검증.
- [ ] Kakao Developers: Android key hash/package name, iOS bundle ID, Kakao Login 및 OIDC 활성화. Native app key를 API 환경에 주입.
- [ ] Flutter: 세 SDK/흐름에서 token·nonce를 얻어 `/auth/social-login` 호출, 가입 필요 시 `code`만 보관해 `/auth/signup` 호출, Mogakco refresh token은 OS 보안 저장소에 보관.
- [ ] Flutter: 공급자 access/refresh token·Apple의 최초 이름/이메일은 API에 보내거나 앱 영속 저장하지 않는다.

## Google API 진행 상태

- [x] `POST /auth/social-login`의 `provider=GOOGLE` 구현: Google JWKS·`RS256`·issuer·Web client ID audience·만료·`sub` 검증.
- [x] 가입 완료 계정의 Mogakco 토큰 발급과 첫 로그인 단회 가입 코드 발급 구현.
- [x] `cryptography` 의존성과 RSA 서명 JWT 테스트 추가.
- [ ] Flutter Google 로그인에서 backend용 Web client ID로 ID token을 받아 실제 API에 연결.
- [ ] Google Cloud 콘솔 client ID를 `GOOGLE_OAUTH_CLIENT_IDS`로 배포 환경에 주입하고 Android·iOS 실기기 로그인 확인.

## Kakao API 진행 상태

- [x] `POST /auth/social-login`의 `provider=KAKAO` 구현: Kakao JWKS·`RS256`·issuer·Native app key audience·만료·`sub`·필수 nonce 검증.
- [x] 첫 로그인 단회 가입 코드와 가입 완료 계정 토큰 발급 검증.
- [ ] Kakao Developers에서 OpenID Connect를 활성화하고 `KAKAO_NATIVE_APP_KEY`를 배포 환경에 주입한 뒤 Android·iOS 실기기 로그인을 확인.

## Apple API 진행 상태

- [x] `POST /auth/social-login`의 `provider=APPLE` 구현: Apple JWKS·`RS256`·issuer·iOS bundle ID/Android Service ID audience·만료·`sub`·전달된 nonce 검증.
- [x] 신규 계정 가입 코드와 기존 계정 토큰 발급을 Apple에도 적용.
- [ ] Apple Developer 값을 `APPLE_CLIENT_IDS`로 배포 환경에 주입하고 iOS·Android 실기기 로그인을 확인.

## API 테스트와 배포 검증

- [ ] JWK fixture로 각 공급자의 유효 JWT가 가입 완료 계정에는 Mogakco 토큰, 첫 로그인에는 단회 가입 코드를 주는지 확인한다.
- [ ] 모든 공급자에서 잘못된 서명/알고리즘, `iss`, `aud`, `exp`, 빈 `sub`를 401로 거부한다. Kakao와 nonce를 보낸 Google/Apple은 SDK별 raw/hash 형식의 불일치 nonce도 401로 거부한다.
- [ ] JWKS timeout·5xx는 503, 알 수 없는 `kid`는 JWKS 새로고침 뒤 401, 지원하지 않는 provider는 404, 가입 코드 재사용은 401인지 확인한다.
- [ ] 빈 DB에 초기 migration을 적용했을 때 `GOOGLE`·`APPLE`·`KAKAO` 계정을 모두 생성할 수 있고 `oauth_attempts`가 생성되지 않는지 확인한다.
- [ ] 실제 Android와 iOS에서 Google·Apple·Kakao 로그인을 각각 확인한다. Apple Android 웹 인증의 취소·return URL·토큰 전달까지 별도 확인한다.

## 확인 결과

- 2026-08-13: 기존 `tests/test_auth_flow.py tests/test_auth_config.py` 통과 (3 passed).
- 2026-08-13: 현재 가상환경에 `cryptography`가 없어 ID token의 로컬 서명 검증은 아직 불가하다.
