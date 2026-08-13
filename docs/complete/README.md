# 완료된 계획

구현과 자동 검증이 끝난 기능 문서를 둔다. 기능 계약, 구현 체크리스트, 검증 결과가 한 파일에 있다.

- [`social-auth.md`](social-auth.md): 소셜 로그인·가입·세션·약관, 공급자별 검증과 콘솔 설정
- [`profile-media-and-keywords.md`](profile-media-and-keywords.md): 이미지 업로드·검증, 사용처 표, 키워드 자동완성과 캐시

## 실환경에서만 확인할 수 있는 것

코드는 끝났지만 실제 자격 증명과 도메인이 있어야 확인되는 항목이다. **배포 전에 처리한다.** 각 문서의 「남은 검증」에 자세히 있다.

- **Google·Apple·Kakao 종단 간 로그인** — 세 공급자 콘솔 등록과 Android·iOS 실기기 필요. Apple Android는 return URL 복귀까지 별도 확인
- **S3 presigned POST 종단 간** — `Conditions` 형식이 틀리면 모든 업로드가 403이 된다. 용량 제한, IAM 권한 세 가지, 실제 `ContentRange` 형식도 함께 확인
- **CDN 도메인 확정** — 에셋 `url`이 `S3_PUBLIC_BASE_URL`로 만들어져 DB에 저장된다. 나중에 바꾸면 이미 저장된 URL과 어긋나므로 **버킷을 만들기 전에** 정해야 한다
