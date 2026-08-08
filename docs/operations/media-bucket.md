# 이미지 버킷 운영 설정

클라이언트가 S3로 직접 올리므로 버킷 쪽 설정이 없으면 API가 정상이어도 업로드가 통째로 실패한다.

## 버킷

퍼블릭 접근을 모두 차단하고 CloudFront를 앞에 둔다. `S3_PUBLIC_BASE_URL`에는 버킷 주소가 아니라 CDN 도메인을 넣는다. 에셋의 `url` 컬럼이 이 값으로 만들어져 그대로 응답에 나가므로, 나중에 바꾸면 이미 저장된 URL과 어긋난다.

## CORS

웹 클라이언트가 `fetch`로 S3에 직접 POST한다. 이 설정이 없으면 브라우저가 요청을 막는다. 네이티브 앱은 CORS를 타지 않아 영향이 없다.

```json
[
  {
    "AllowedOrigins": ["https://mogakco.example.com"],
    "AllowedMethods": ["POST"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag", "Location"],
    "MaxAgeSeconds": 3000
  }
]
```

`AllowedOrigins`에는 실제 웹 도메인을 넣는다. 로컬 개발용 `http://localhost:*`가 필요하면 개발 버킷에만 추가한다.

## IAM

API 역할에 세 가지가 모두 필요하다. presigned POST는 API의 자격 증명으로 서명하므로 `s3:PutObject`가 있어야 하고, 완료 검증에 `s3:GetObject`, 위장 파일 정리에 `s3:DeleteObject`가 쓰인다. `DeleteObject`가 빠지면 거절은 되지만 버킷에 쓰레기가 남는다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::mogakco-media/images/*"
    }
  ]
}
```

## 고아 이미지 정리

크론에서 하루 한 번 부른다. DB 행과 S3 객체를 함께 지운다.

```
uv run python -m app.images.cleanup
```

대상은 **어느 사용자도 참조하지 않고** 생성된 지 `MEDIA_ORPHAN_AGE_HOURS`(기본 24)가 지난 이미지다. 업로드 URL만 받고 올리지 않은 것과, 프로필 사진을 바꿔서 참조가 끊긴 이전 이미지가 모두 여기 걸린다. 올린 직후 아직 프로필에 붙이기 전인 이미지를 지우지 않으려고 시간 여유를 둔다.

수명 주기 규칙은 미완료 멀티파트 업로드에만 건다. 이건 객체가 아니라 조각이라 DB에도 남지 않고 배치가 볼 수 없다.

```json
{
  "Rules": [
    {
      "ID": "abort-incomplete-multipart",
      "Status": "Enabled",
      "Filter": { "Prefix": "images/" },
      "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 1 }
    }
  ]
}
```

객체 만료 규칙은 걸지 않는다. 프로필 사진은 오래됐다고 지우면 안 되고, 참조 여부는 S3가 알 수 없다.

## 클라이언트 업로드 주의

`POST /images/upload-url` 응답의 `fields`를 폼에 그대로 넣고 **파일을 맨 마지막에 붙인다.** S3는 정책 필드보다 앞에 온 파일을 거절한다.

```js
const form = new FormData();
Object.entries(fields).forEach(([name, value]) => form.append(name, value));
form.append("file", file); // 반드시 마지막

await fetch(uploadUrl, { method: "POST", body: form });
await fetch(`/images/${assetId}/complete`, { method: "POST", headers: authHeader });
```

성공 시 S3는 본문 없이 204를 준다. 업로드가 끝나도 `complete`를 부르기 전에는 `PENDING`이라 프로필에 붙일 수 없다.
