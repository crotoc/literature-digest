# features/fulltext

全文下载的编排与业务流程。

## 职责

- 状态机：queued → resolving → fetching → (captcha/auth/validating) → uploading → success/failed
- 候选推导：调 `adapters/resolvers/*`
- 取件：调 `adapters/fetchers/{direct,extension}`
- 校验：调 `caps/fileprobe`
- 存储：调 `caps/blobstore` + `domain/attachments`
- 诊断：写 `domain/traces`

## 接口

- `initiate_fulltext_download(account_id, library_id, work_id, source_url)` → JobDTO
- `record_observation(job_id, observation)` → None
- `record_upload(job_id, file_bytes, filename)` → None

## 约定

- 长流程批量：每篇一个子 job
- 直链优先：先试 direct fetcher，再转 extension
- 不伪造状态：等人交互时停在 awaiting_resume，不轮询判断
