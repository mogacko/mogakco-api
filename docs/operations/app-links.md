# 앱 링크 운영

콜백은 `AUTH_APP_LINK_BASE_URL/auth/callback`으로 보낸다. 변경 시 새 도메인에 HTTPS로 다음을 배포하고, 앱에 도메인을 추가한 뒤 환경값을 전환한다. 이전 도메인은 구버전 앱이 사라질 때까지 유지한다.

- iOS: Associated Domains `applinks:<도메인>`, `/.well-known/apple-app-site-association`
- Android: `https://<도메인>/auth/callback` + `autoVerify`, `/.well-known/assetlinks.json`
