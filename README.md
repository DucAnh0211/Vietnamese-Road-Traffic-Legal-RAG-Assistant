# Legal Traffic RAG

Backend RAG dành cho tra cứu văn bản pháp luật giao thông Việt Nam. Bản khởi tạo
chạy ở chế độ local/extractive, không cần API key.

## Yêu cầu

- Python 3.11–3.13
- Docker (tùy chọn)

## Chạy local

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn api.main:app --reload
```

Mở Swagger tại http://localhost:8000/docs và health check tại
http://localhost:8000/health.

## Nạp dữ liệu và tạo index

Đặt file `.txt`, `.md` hoặc `.pdf` trong `data/raw`, sau đó chạy:

```powershell
python scripts/ingest.py
python scripts/build_index.py
```

Hỏi thử:

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/chat `
  -ContentType 'application/json' `
  -Body '{"question":"Người lái xe phải mang theo giấy tờ gì?"}'
```

## Docker

```powershell
Copy-Item .env.example .env
docker compose up --build
```

> Câu trả lời chỉ nhằm mục đích tham khảo và không thay thế tư vấn pháp lý.
